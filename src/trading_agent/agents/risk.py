"""Risk Agent: checks a candidate trade against the user's risk limits and sizes it."""

from __future__ import annotations

import math
from collections.abc import Mapping

from ..schemas import Action, Check, PortfolioSnapshot, RiskResult, UserProfile


def _label(score: int) -> str:
    return "Low" if score < 40 else "Medium" if score < 70 else "High"


class RiskAgent:
    """Position sizing follows the fixed-fractional rule: risk at most
    ``risk_per_trade_pct`` of equity if the stop-loss is hit, capped at ``max_position_pct``.

    The agent is long-only: a SELL closes the whole existing position and never shorts.
    """

    def __init__(self, fee_rate: float = 0.0005, slippage_rate: float = 0.0005):
        self.cost_rate = fee_rate + slippage_rate

    def evaluate(
        self,
        profile: UserProfile,
        action: Action,
        symbol: str,
        price: float,
        features: Mapping[str, float],
        portfolio: PortfolioSnapshot,
    ) -> RiskResult:
        vol_pct = float(features["vol_20"]) * 100
        max_vol = float(profile.max_volatility_pct or 100)
        stop = float(profile.stop_loss_pct or 8)
        held = portfolio.positions.get(symbol)

        if action == Action.SELL:
            qty = int(held.qty) if held else 0
            score = round(min(100, 50 * vol_pct / max_vol))
            return RiskResult(
                within_limit=qty >= 1, risk_score=score, risk_label=_label(score),
                size_pct=0.0, qty=qty, stop_loss_price=None,
                checks=[Check(
                    name="Closing an existing position", passed=qty >= 1,
                    detail=f"Selling {qty} shares reduces exposure" if qty else "No shares held",
                )],
            )

        max_pos = float(profile.max_position_pct or 10)
        risk_per_trade = float(profile.risk_per_trade_pct or 1)
        max_dd = float(profile.max_drawdown_pct or 15)

        size_pct = min(max_pos, risk_per_trade / stop * 100)
        held_value = held.qty * price if held else 0.0
        room_qty = math.floor((size_pct / 100 * portfolio.equity - held_value) / price)
        afford_qty = math.floor(portfolio.cash / (price * (1 + self.cost_rate)))
        qty = max(0, min(room_qty, afford_qty))
        held_pct = held_value / portfolio.equity * 100 if portfolio.equity else 0.0

        checks = [
            Check(name="Volatility within limit", passed=vol_pct <= max_vol,
                  detail=f"{vol_pct:.0f}% annualised vs your {max_vol:.0f}% limit"),
            Check(name="Portfolio drawdown within limit", passed=portfolio.drawdown_pct < max_dd,
                  detail=f"{portfolio.drawdown_pct:.1f}% below peak vs your {max_dd:.0f}% limit"),
            Check(name="Room in position limit", passed=room_qty >= 1,
                  detail=f"Target {size_pct:.1f}% of portfolio, already holding {held_pct:.1f}%"),
            Check(name="Enough cash", passed=afford_qty >= 1,
                  detail=f"Cash covers {max(afford_qty, 0)} shares at ${price:,.2f}"),
        ]
        within = all(c.passed for c in checks)
        score = round(min(100, 50 * vol_pct / max_vol + 50 * size_pct / max_pos))
        return RiskResult(
            within_limit=within, risk_score=score, risk_label=_label(score),
            size_pct=round(qty * price / portfolio.equity * 100, 2) if portfolio.equity else 0.0,
            qty=qty if within else 0,
            stop_loss_price=round(price * (1 - stop / 100), 2),
            checks=checks,
        )
