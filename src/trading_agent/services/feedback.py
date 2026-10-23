"""Feedback loop: scores past recommendations against what the market did, and
turns the user's decisions into profile suggestions.

Suggestions are advice only. The user decides whether to change their own limits.
"""

from __future__ import annotations

import pandas as pd

from ..agents import MarketDataAgent
from ..data import MarketDataError
from ..db import Store
from ..schemas import Action, Recommendation, RecStatus, UserProfile

MIN_SAMPLE = 5


def _rate(items: list[Recommendation]) -> float | None:
    done = [r for r in items if r.outcome_correct is not None]
    return round(sum(r.outcome_correct for r in done) / len(done) * 100, 1) if done else None


class FeedbackService:
    def __init__(self, store: Store, market: MarketDataAgent):
        self.store = store
        self.market = market

    def evaluate_outcomes(self, profile_id: int) -> int:
        """Fill in the realised return for every BUY/SELL whose horizon has passed."""
        updated = 0
        for rec in self.store.list_recommendations(profile_id, limit=1000):
            if rec.action == Action.HOLD or rec.outcome_return is not None:
                continue
            try:
                df = self.market.history(rec.symbol)
            except MarketDataError:
                continue
            after = df[df.index > pd.Timestamp(rec.as_of)]
            if len(after) < rec.horizon_days:
                continue
            bar = after.iloc[rec.horizon_days - 1]
            ret = float(bar["close"]) / rec.price - 1
            correct = ret > 0 if rec.action == Action.BUY else ret < 0
            self.store.set_outcome(rec.id, ret, correct, pd.Timestamp(bar.name).date())
            updated += 1
        return updated

    def stats(self, profile_id: int) -> dict:
        recs = [
            r for r in self.store.list_recommendations(profile_id, limit=1000)
            if r.action != Action.HOLD and r.status != RecStatus.expired
        ]
        approved = [r for r in recs if r.status == RecStatus.approved]
        rejected = [r for r in recs if r.status == RecStatus.rejected]
        pending = [r for r in recs if r.status == RecStatus.pending]
        decided = len(approved) + len(rejected)
        scored = [r for r in recs if r.outcome_correct is not None]

        signed = [
            (r.outcome_return if r.action == Action.BUY else -r.outcome_return) * 100
            for r in scored
        ]
        by_symbol: dict[str, dict] = {}
        for sym in sorted({r.symbol for r in scored}):
            group = [r for r in scored if r.symbol == sym]
            by_symbol[sym] = {"n": len(group), "hit_rate": _rate(group)}

        return {
            "total": len(recs),
            "pending": len(pending),
            "approved": len(approved),
            "rejected": len(rejected),
            "approval_rate": round(len(approved) / decided * 100, 1) if decided else None,
            "scored": len(scored),
            "hit_rate": _rate(scored),
            "hit_rate_approved": _rate(approved),
            "hit_rate_rejected": _rate(rejected),
            "avg_signal_return_pct": round(sum(signed) / len(signed), 2) if signed else None,
            "by_symbol": by_symbol,
        }

    def suggestions(self, profile: UserProfile, stats: dict) -> list[dict]:
        out: list[dict] = []
        decided = stats["approved"] + stats["rejected"]
        current = float(profile.min_confidence_pct or 50)

        if decided >= MIN_SAMPLE and stats["approval_rate"] is not None \
                and stats["approval_rate"] < 50:
            out.append({
                "title": "You reject most recommendations",
                "detail": (
                    f"You approved {stats['approval_rate']:.0f}% of {decided} decisions. "
                    f"Raising your minimum confidence from {current:.0f}% to "
                    f"{min(95, current + 3):.0f}% would show you fewer, stronger signals."
                ),
                "field": "min_confidence_pct", "value": min(95, current + 3),
            })
        if stats["approved"] >= MIN_SAMPLE and (stats["hit_rate_approved"] or 100) < 45:
            out.append({
                "title": "Approved trades have been right less than half the time",
                "detail": (
                    f"{stats['hit_rate_approved']:.0f}% of your scored approvals moved the way the "
                    "model predicted. Consider a higher confidence bar or a different trading style."
                ),
                "field": "min_confidence_pct", "value": min(95, current + 3),
            })
        if stats["rejected"] >= MIN_SAMPLE and (stats["hit_rate_rejected"] or 0) >= 65:
            out.append({
                "title": "Rejected signals have often been right",
                "detail": (
                    f"{stats['hit_rate_rejected']:.0f}% of the recommendations you rejected would "
                    "have been correct. Review why you reject them; your limits may be too tight."
                ),
                "field": None, "value": None,
            })
        return out
