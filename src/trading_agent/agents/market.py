"""Market Data Agent: loads prices, builds features and asks the ML model for a signal."""

from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd

from ..config import Settings
from ..data import MarketDataError, MarketDataProvider
from ..ml import ModelStore, build_features
from ..ml.features import FEATURE_COLUMNS
from ..schemas import MarketSignal, SignalLabel


def classify_probability(prob_up: float, neutral_band: float) -> tuple[SignalLabel, float]:
    """Turn P(up) into a label and the model's confidence in that direction."""
    if prob_up >= 0.5 + neutral_band:
        return SignalLabel.potential_buy, prob_up
    if prob_up <= 0.5 - neutral_band:
        return SignalLabel.potential_sell, 1 - prob_up
    return SignalLabel.neutral, max(prob_up, 1 - prob_up)


def make_signal(
    symbol: str,
    as_of: date,
    price: float,
    prob_up: float,
    features: dict[str, float],
    horizon: int,
    neutral_band: float,
) -> MarketSignal:
    label, confidence = classify_probability(prob_up, neutral_band)
    return MarketSignal(
        symbol=symbol, as_of=as_of, price=price, prob_up=prob_up, label=label,
        confidence=confidence, horizon_days=horizon, features=features,
    )


class MarketDataAgent:
    def __init__(self, provider: MarketDataProvider, models: ModelStore, settings: Settings):
        self.provider = provider
        self.models = models
        self.settings = settings
        self._cache: dict[tuple[str, date], tuple[float, pd.DataFrame]] = {}

    @property
    def source(self) -> str:
        return self.provider.source

    def history(self, symbol: str, as_of: date | None = None) -> pd.DataFrame:
        end = as_of or date.today()
        key = (symbol, end)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < 300:
            return hit[1]
        start = end - timedelta(days=int(self.settings.history_years * 365.25))
        df = self.provider.get_history(symbol, start, end)
        self._cache[key] = (time.monotonic(), df)
        return df

    def latest_price(self, symbol: str) -> tuple[date, float]:
        df = self.history(symbol)
        return pd.Timestamp(df.index[-1]).date(), float(df["close"].iloc[-1])

    def signal(self, symbol: str, as_of: date | None = None) -> MarketSignal:
        df = self.history(symbol, as_of)
        feats = build_features(df)
        row = feats.iloc[[-1]]
        if row.isna().any(axis=None):
            raise MarketDataError(f"Not enough price history for {symbol} to compute indicators")
        model = self.models.get(as_of)
        prob_up = float(model.predict_proba_up(row)[0])
        return make_signal(
            symbol=symbol,
            as_of=pd.Timestamp(df.index[-1]).date(),
            price=float(df["close"].iloc[-1]),
            prob_up=prob_up,
            features={c: float(row[c].iloc[0]) for c in FEATURE_COLUMNS},
            horizon=self.settings.horizon_days,
            neutral_band=self.settings.neutral_band,
        )
