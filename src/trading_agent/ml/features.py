"""Feature engineering from daily OHLCV bars.

Every feature at row ``t`` uses data up to and including day ``t`` only, so the same
function is safe for live signals and for walk-forward backtests.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "ret_1", "ret_5", "ret_10", "ret_20", "ret_60",
    "sma_10_ratio", "sma_20_ratio", "sma_50_ratio", "sma_20_50",
    "rsi_14", "macd_hist", "bb_pctb", "atr_pct",
    "vol_10", "vol_20", "volume_ratio", "drawdown_60",
]


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's relative strength index."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


def build_features(ohlcv: pd.DataFrame) -> pd.DataFrame:
    close, high, low = ohlcv["close"], ohlcv["high"], ohlcv["low"]
    volume = ohlcv["volume"].astype(float)
    daily = close.pct_change()

    f = pd.DataFrame(index=ohlcv.index)
    for n in (1, 5, 10, 20, 60):
        f[f"ret_{n}"] = close.pct_change(n)

    sma = {n: close.rolling(n).mean() for n in (10, 20, 50)}
    f["sma_10_ratio"] = close / sma[10] - 1
    f["sma_20_ratio"] = close / sma[20] - 1
    f["sma_50_ratio"] = close / sma[50] - 1
    f["sma_20_50"] = sma[20] / sma[50] - 1

    f["rsi_14"] = rsi(close)

    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    f["macd_hist"] = (macd - macd.ewm(span=9, adjust=False).mean()) / close

    mid, sd = close.rolling(20).mean(), close.rolling(20).std()
    f["bb_pctb"] = (close - (mid - 2 * sd)) / (4 * sd)

    true_range = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    f["atr_pct"] = true_range.rolling(14).mean() / close

    f["vol_10"] = daily.rolling(10).std() * np.sqrt(252)
    f["vol_20"] = daily.rolling(20).std() * np.sqrt(252)
    f["volume_ratio"] = volume / volume.rolling(20).mean()
    f["drawdown_60"] = close / close.rolling(60).max() - 1

    return f.replace([np.inf, -np.inf], np.nan)[FEATURE_COLUMNS]


def add_labels(features: pd.DataFrame, close: pd.Series, horizon: int) -> pd.DataFrame:
    """Attach the forward return and the up/down target (NaN where unknown)."""
    out = features.copy()
    fwd = close.shift(-horizon) / close - 1
    out["fwd_ret"] = fwd
    out["target"] = (fwd > 0).astype(float).where(fwd.notna())
    return out


def build_dataset(frames: dict[str, pd.DataFrame], horizon: int) -> pd.DataFrame:
    """Pool all symbols into one labelled panel (index = date, may repeat across symbols)."""
    parts = []
    for symbol, ohlcv in frames.items():
        data = add_labels(build_features(ohlcv), ohlcv["close"], horizon)
        data["symbol"] = symbol
        parts.append(data)
    if not parts:
        raise ValueError("No price data supplied")
    return pd.concat(parts).sort_index()
