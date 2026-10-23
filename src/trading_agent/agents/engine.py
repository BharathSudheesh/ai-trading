"""AI Decision Engine: merges the signal and all agent verdicts into one recommendation."""

from __future__ import annotations

from datetime import datetime

from ..schemas import (
    Action,
    MarketSignal,
    PersonalContext,
    Recommendation,
    RecStatus,
    RiskResult,
    SignalLabel,
    StrategyResult,
    UserProfile,
)
from .strategy import STYLE_LABEL


class DecisionEngine:
    """A trade is recommended only if the model, the strategy and the risk limits all agree.

    Any single veto turns the result into HOLD and records which agent blocked it.
    """

    def decide(
        self,
        profile: UserProfile,
        signal: MarketSignal,
        ctx: PersonalContext,
        strategy: StrategyResult | None,
        risk: RiskResult | None,
        created_at: datetime,
        replay: bool = False,
    ) -> Recommendation:
        sym = signal.symbol
        style = STYLE_LABEL[profile.trading_style].lower()
        lean = "buy" if signal.label == SignalLabel.potential_buy else "sell"

        action = Action.HOLD
        blocked: str | None = None
        if not ctx.relevant:
            blocked = "profile"
        elif ctx.candidate is None:
            blocked = self._personal_block(signal, ctx)
        elif strategy is not None and not strategy.matched:
            blocked = "strategy"
        elif risk is not None and not risk.within_limit:
            blocked = "risk"
        else:
            action = ctx.candidate

        if action == Action.BUY and risk is not None:
            summary = (
                f"Buy {risk.qty} {sym} (about {risk.size_pct:.1f}% of your portfolio). "
                f"{signal.confidence:.0%} confidence, fits your {style} style, within your risk limits."
            )
        elif action == Action.SELL and risk is not None:
            summary = (
                f"Sell {risk.qty} {sym}. {signal.confidence:.0%} confidence that the price falls, "
                f"and your {style} rules agree."
            )
        else:
            summary = {
                "profile": f"Hold. {sym} is outside your preferred assets.",
                "no_signal": f"Hold. No clear edge on {sym} right now.",
                "confidence": f"Hold. The model leans {lean}, but not with the confidence you require.",
                "no_position": f"Hold. The model leans down, but you hold no {sym} to sell.",
                "strategy": f"Hold. The model leans {lean}, but it does not fit your {style} rules.",
                "risk": f"Hold. The model leans {lean}, but the trade would break your risk limits.",
            }[blocked or "no_signal"]

        reasons = [
            f"The model estimates a {signal.prob_up:.0%} chance that {sym} is higher in "
            f"{signal.horizon_days} trading days.",
            *ctx.reasons,
        ]

        return Recommendation(
            profile_id=profile.id or 0,
            symbol=sym,
            as_of=signal.as_of,
            created_at=created_at,
            price=signal.price,
            horizon_days=signal.horizon_days,
            prob_up=signal.prob_up,
            signal=signal.label,
            confidence=signal.confidence,
            action=action,
            strategy_status=("N/A" if strategy is None else "Matched" if strategy.matched
                             else "Not matched"),
            risk_status=("N/A" if risk is None else "Within limit" if risk.within_limit
                         else "Exceeds limit"),
            risk_score=risk.risk_score if risk else None,
            risk_label=risk.risk_label if risk else None,
            suggested_qty=risk.qty if risk and action != Action.HOLD else 0,
            suggested_position_pct=risk.size_pct if risk and action != Action.HOLD else 0.0,
            stop_loss_price=risk.stop_loss_price if risk and action == Action.BUY else None,
            blocked_by=blocked,
            summary=summary,
            reasons=reasons,
            strategy_checks=strategy.checks if strategy else [],
            risk_checks=risk.checks if risk else [],
            replay=replay,
            status=RecStatus.pending if action != Action.HOLD else RecStatus.info,
        )

    @staticmethod
    def _personal_block(signal: MarketSignal, ctx: PersonalContext) -> str:
        if signal.label == SignalLabel.neutral:
            return "no_signal"
        if any("hold no shares" in r for r in ctx.reasons):
            return "no_position"
        return "confidence"
