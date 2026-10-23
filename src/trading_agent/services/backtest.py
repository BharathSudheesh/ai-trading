"""Walk-forward backtest of the full decision pipeline.

How look-ahead bias is avoided:
* the model is trained only on bars before the first test day;
* features at day ``t`` use data up to ``t`` only;
* decisions are made at the close of day ``t`` and filled at the open of day ``t+1``.

Fees and slippage use the same rates as paper trading. The benchmark is an equal-weight
buy-and-hold of the same symbols, without costs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import reduce

import numpy as np
import pandas as pd
from pydantic import BaseModel
from sklearn.metrics import roc_auc_score

from ..agents import DecisionPipeline, MarketDataAgent, make_signal
from ..config import Settings
from ..ml import build_features, train_model
from ..ml.features import add_labels
from ..schemas import Action, PortfolioSnapshot, Position, UserProfile


class BacktestTrade(BaseModel):
    date: date
    symbol: str
    side: str
    qty: int
    price: float
    pnl: float | None
    reason: str


class CurvePoint(BaseModel):
    date: date
    equity: float
    benchmark: float


class BacktestMetrics(BaseModel):
    final_equity: float
    total_return_pct: float
    cagr_pct: float | None
    volatility_pct: float
    sharpe: float | None
    max_drawdown_pct: float
    n_trades: int
    win_rate_pct: float | None
    exposure_pct: float
    benchmark_return_pct: float
    benchmark_max_drawdown_pct: float
    excess_return_pct: float


class BacktestResult(BaseModel):
    profile_id: int
    symbols: list[str]
    start: date
    end: date
    initial_cash: float
    data_source: str
    metrics: BacktestMetrics
    model_oos: dict | None
    curve: list[CurvePoint]
    trades: list[BacktestTrade]
    open_positions: int
    notes: list[str]


@dataclass
class _Lot:
    qty: int
    avg: float


def _max_drawdown(series: pd.Series) -> float:
    return float(((series / series.cummax()) - 1).min() * 100)


class Backtester:
    def __init__(self, market: MarketDataAgent, pipeline: DecisionPipeline, settings: Settings):
        self.market = market
        self.pipeline = pipeline
        self.settings = settings

    def run(
        self,
        profile: UserProfile,
        symbols: list[str],
        months: int = 12,
        initial_cash: float | None = None,
        use_stop_loss: bool = True,
    ) -> BacktestResult:
        s = self.settings
        initial = float(initial_cash or s.starting_cash)
        frames = {sym: self.market.history(sym) for sym in symbols}
        common = reduce(lambda a, b: a.intersection(b), (f.index for f in frames.values()))
        start_ts = pd.Timestamp(date.today() - timedelta(days=int(months * 30.44)))
        dates = common[common >= start_ts]
        if len(dates) < 40:
            raise ValueError("Not enough overlapping price history for this backtest window")

        train_frames = {sym: f[f.index < dates[0]] for sym, f in frames.items()}
        model = train_model(
            train_frames, algorithm=s.ml_algorithm, horizon=s.horizon_days,
            seed=s.random_seed, evaluate=False,
        )

        feats, probs, rows = {}, {}, {}
        for sym in symbols:
            full = build_features(frames[sym]).loc[dates]
            ok = full.notna().all(axis=1)
            p = pd.Series(np.nan, index=dates)
            if ok.any():
                p[ok] = model.predict_proba_up(full[ok])
            feats[sym], probs[sym], rows[sym] = full, p, full.to_dict("index")

        model_oos = self._out_of_sample(frames, probs, dates)

        fee, slip = s.fee_rate, s.slippage_rate
        cash, peak = initial, initial
        lots: dict[str, _Lot] = {}
        pending: list[tuple[str, str, int, str]] = []  # (symbol, side, qty, reason)
        curve_dates, equity_series, exposures = [], [], []
        trades: list[BacktestTrade] = []
        stop = float(profile.stop_loss_pct or 8)
        now = datetime.now(timezone.utc)

        for i, d in enumerate(dates):
            for sym, side, qty, reason in sorted(pending, key=lambda x: x[1] != "SELL"):
                open_ = float(frames[sym].at[d, "open"])
                if side == "SELL":
                    lot = lots.pop(sym, None)
                    if lot is None:
                        continue
                    fill = open_ * (1 - slip)
                    fees = lot.qty * fill * fee
                    cash += lot.qty * fill - fees
                    pnl = (fill - lot.avg) * lot.qty - fees
                    trades.append(BacktestTrade(date=d.date(), symbol=sym, side="SELL", qty=lot.qty,
                                                price=round(fill, 2), pnl=round(pnl, 2), reason=reason))
                else:
                    fill = open_ * (1 + slip)
                    n = min(qty, math.floor(cash / (fill * (1 + fee))))
                    if n < 1:
                        continue
                    fees = n * fill * fee
                    cash -= n * fill + fees
                    old = lots.get(sym)
                    total = (old.qty if old else 0) + n
                    avg = ((old.qty * old.avg if old else 0) + n * fill) / total
                    lots[sym] = _Lot(total, avg)
                    trades.append(BacktestTrade(date=d.date(), symbol=sym, side="BUY", qty=n,
                                                price=round(fill, 2), pnl=None, reason=reason))
            pending = []

            closes = {sym: float(frames[sym].at[d, "close"]) for sym in symbols}
            equity = cash + sum(lot.qty * closes[sym] for sym, lot in lots.items())
            peak = max(peak, equity)
            curve_dates.append(d)
            equity_series.append(equity)
            exposures.append(1 - cash / equity)
            if i == len(dates) - 1:
                break

            snapshot = PortfolioSnapshot(
                cash=cash, equity=equity, peak_equity=peak,
                positions={k: Position(symbol=k, qty=v.qty, avg_price=v.avg) for k, v in lots.items()},
            )
            for sym in symbols:
                prob = probs[sym].iloc[i]
                if np.isnan(prob):
                    continue
                held = lots.get(sym)
                if use_stop_loss and held and closes[sym] <= held.avg * (1 - stop / 100):
                    pending.append((sym, "SELL", held.qty, "stop-loss"))
                    continue
                signal = make_signal(sym, d.date(), closes[sym], float(prob), rows[sym][d],
                                     s.horizon_days, s.neutral_band)
                rec = self.pipeline.run(profile, signal, snapshot, symbols, now, replay=True)
                if rec.action == Action.SELL:
                    pending.append((sym, "SELL", rec.suggested_qty, "model signal"))
                elif rec.action == Action.BUY:
                    pending.append((sym, "BUY", rec.suggested_qty, "model signal"))

        equity_s = pd.Series(equity_series, index=pd.DatetimeIndex(curve_dates))
        bench = pd.concat(
            [frames[sym]["close"].loc[dates] / frames[sym]["close"].loc[dates[0]] for sym in symbols],
            axis=1,
        ).mean(axis=1) * initial

        metrics = self._metrics(equity_s, bench, trades, exposures, initial)
        notes = [
            "Model trained only on data before the first test day.",
            "Orders are decided at the close and filled at the next open, with fees and slippage.",
            "The benchmark is an equal-weight buy-and-hold of the same symbols, without costs.",
            "Past simulated performance does not predict future results.",
        ]
        if self.market.source == "synthetic":
            notes.insert(0, "Prices are synthetic demo data, so these results say nothing about real markets.")

        return BacktestResult(
            profile_id=profile.id or 0, symbols=symbols, start=dates[0].date(), end=dates[-1].date(),
            initial_cash=initial, data_source=self.market.source, metrics=metrics, model_oos=model_oos,
            curve=[
                CurvePoint(date=d.date(), equity=round(float(e), 2), benchmark=round(float(b), 2))
                for d, e, b in zip(equity_s.index, equity_s.values, bench.values, strict=True)
            ],
            trades=list(reversed(trades))[:200],
            open_positions=len(lots),
            notes=notes,
        )

    def _out_of_sample(self, frames, probs, dates) -> dict | None:
        y_all, p_all = [], []
        for sym, p in probs.items():
            labelled = add_labels(pd.DataFrame(index=frames[sym].index), frames[sym]["close"],
                                  self.settings.horizon_days)
            y = labelled["target"].reindex(dates)
            ok = p.notna() & y.notna()
            y_all.append(y[ok].to_numpy())
            p_all.append(p[ok].to_numpy())
        if not y_all:
            return None
        y_arr, p_arr = np.concatenate(y_all), np.concatenate(p_all)
        if len(y_arr) < 30:
            return None
        return {
            "n": int(len(y_arr)),
            "accuracy": round(float(((p_arr >= 0.5) == (y_arr == 1)).mean()), 3),
            "baseline_accuracy": round(float(max(y_arr.mean(), 1 - y_arr.mean())), 3),
            "roc_auc": round(float(roc_auc_score(y_arr, p_arr)), 3) if len(set(y_arr)) == 2 else None,
        }

    @staticmethod
    def _metrics(equity: pd.Series, bench: pd.Series, trades, exposures, initial: float):
        rets = equity.pct_change().dropna()
        years = (equity.index[-1] - equity.index[0]).days / 365.25
        total = equity.iloc[-1] / initial - 1
        std = float(rets.std()) if len(rets) > 1 else 0.0
        sells = [t for t in trades if t.side == "SELL"]
        bench_total = bench.iloc[-1] / initial - 1
        return BacktestMetrics(
            final_equity=round(float(equity.iloc[-1]), 2),
            total_return_pct=round(float(total * 100), 2),
            cagr_pct=round(float(((1 + total) ** (1 / years) - 1) * 100), 2) if years >= 0.5 else None,
            volatility_pct=round(std * math.sqrt(252) * 100, 2),
            sharpe=round(float(rets.mean() / std * math.sqrt(252)), 2) if std > 0 else None,
            max_drawdown_pct=round(_max_drawdown(equity), 2),
            n_trades=len(trades),
            win_rate_pct=round(sum(t.pnl > 0 for t in sells) / len(sells) * 100, 1) if sells else None,
            exposure_pct=round(float(np.mean(exposures) * 100), 1),
            benchmark_return_pct=round(float(bench_total * 100), 2),
            benchmark_max_drawdown_pct=round(_max_drawdown(bench), 2),
            excess_return_pct=round(float((total - bench_total) * 100), 2),
        )
