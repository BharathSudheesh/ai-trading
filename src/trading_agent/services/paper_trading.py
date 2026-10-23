"""Paper trading: simulated fills against a stored cash balance and positions."""

from __future__ import annotations

import math

from ..agents import MarketDataAgent
from ..config import Settings
from ..db import Store, utcnow
from ..schemas import (
    Action,
    Order,
    PortfolioSnapshot,
    PortfolioSummary,
    PositionView,
    Recommendation,
)
from .errors import NotFoundError, TradeError


class PaperTradingService:
    """Fills approved recommendations at the reference price plus slippage and commission.

    No real orders are ever sent to a broker.
    """

    def __init__(self, store: Store, market: MarketDataAgent, settings: Settings):
        self.store = store
        self.market = market
        self.settings = settings

    def _account(self, profile_id: int) -> dict:
        try:
            return self.store.get_account(profile_id)
        except KeyError as exc:
            raise NotFoundError(f"Profile {profile_id} not found") from exc

    def snapshot(self, profile_id: int) -> PortfolioSnapshot:
        account = self._account(profile_id)
        positions = self.store.get_positions(profile_id)
        equity = account["cash"] + sum(
            p.qty * self.market.latest_price(sym)[1] for sym, p in positions.items()
        )
        peak = self.store.update_peak(profile_id, equity)
        return PortfolioSnapshot(
            cash=account["cash"], equity=equity, peak_equity=peak, positions=positions
        )

    def summary(self, profile_id: int) -> PortfolioSummary:
        account = self._account(profile_id)
        snap = self.snapshot(profile_id)
        views: list[PositionView] = []
        for sym, pos in sorted(snap.positions.items()):
            price = self.market.latest_price(sym)[1]
            value = pos.qty * price
            views.append(PositionView(
                symbol=sym, qty=pos.qty, avg_price=pos.avg_price, price=price,
                market_value=value, pnl=value - pos.qty * pos.avg_price,
                pnl_pct=(price / pos.avg_price - 1) * 100,
                weight_pct=value / snap.equity * 100 if snap.equity else 0.0,
            ))
        invested = sum(v.market_value for v in views)
        start = account["starting_cash"]
        return PortfolioSummary(
            profile_id=profile_id, cash=snap.cash, invested=invested, equity=snap.equity,
            starting_cash=start, total_pnl=snap.equity - start,
            total_return_pct=(snap.equity / start - 1) * 100, peak_equity=snap.peak_equity,
            drawdown_pct=snap.drawdown_pct, positions=views,
        )

    def execute(self, rec: Recommendation) -> Order:
        if rec.action == Action.HOLD:
            raise TradeError("A HOLD recommendation has nothing to execute")
        fee, slip = self.settings.fee_rate, self.settings.slippage_rate
        account = self._account(rec.profile_id)
        cash = account["cash"]
        positions = self.store.get_positions(rec.profile_id)
        pos = positions.get(rec.symbol)

        if rec.action == Action.BUY:
            fill = rec.price * (1 + slip)
            qty = min(rec.suggested_qty, math.floor(cash / (fill * (1 + fee))))
            if qty < 1:
                raise TradeError(f"Not enough cash to buy {rec.symbol}")
            fees = qty * fill * fee
            self.store.set_cash(rec.profile_id, cash - qty * fill - fees)
            old_qty = pos.qty if pos else 0
            old_avg = pos.avg_price if pos else 0.0
            new_qty = old_qty + qty
            new_avg = (old_qty * old_avg + qty * fill) / new_qty
            self.store.set_position(rec.profile_id, rec.symbol, new_qty, new_avg)
            side = "BUY"
        else:
            if pos is None:
                raise TradeError(f"You hold no {rec.symbol} to sell")
            fill = rec.price * (1 - slip)
            qty = min(rec.suggested_qty, int(pos.qty))
            if qty < 1:
                raise TradeError(f"You hold no {rec.symbol} to sell")
            fees = qty * fill * fee
            self.store.set_cash(rec.profile_id, cash + qty * fill - fees)
            self.store.set_position(rec.profile_id, rec.symbol, pos.qty - qty, pos.avg_price)
            side = "SELL"

        return self.store.add_order(Order(
            profile_id=rec.profile_id, rec_id=rec.id, symbol=rec.symbol, side=side,
            qty=qty, price=round(fill, 4), fees=round(fees, 2), created_at=utcnow(),
        ))
