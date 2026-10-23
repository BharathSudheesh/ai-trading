"""Generates recommendations and records the user's approve / reject decision."""

from __future__ import annotations

from datetime import date

from ..agents import DecisionPipeline, MarketDataAgent, NotificationAgent
from ..config import Settings
from ..data import MarketDataError
from ..db import Store, utcnow
from ..schemas import Recommendation, RecStatus, UserProfile
from .errors import NotFoundError, TradeError
from .paper_trading import PaperTradingService


class RecommendationService:
    def __init__(
        self,
        store: Store,
        market: MarketDataAgent,
        pipeline: DecisionPipeline,
        notifier: NotificationAgent,
        paper: PaperTradingService,
        settings: Settings,
    ):
        self.store = store
        self.market = market
        self.pipeline = pipeline
        self.notifier = notifier
        self.paper = paper
        self.settings = settings

    def require_profile(self, profile_id: int) -> UserProfile:
        profile = self.store.get_profile(profile_id)
        if profile is None:
            raise NotFoundError(f"Profile {profile_id} not found")
        return profile

    def generate(
        self,
        profile_id: int,
        symbols: list[str] | None = None,
        as_of: date | None = None,
    ) -> tuple[list[Recommendation], list[str]]:
        """Run the full agent pipeline for each symbol.

        ``as_of`` replays a past date using a model that has only seen earlier data. Replayed
        recommendations are marked ``replay`` and do not raise alerts.
        """
        profile = self.require_profile(profile_id)
        universe = profile.preferred_assets or self.settings.default_symbols
        targets = [s.upper() for s in symbols] if symbols else universe
        replay = as_of is not None and as_of < date.today()
        snapshot = self.paper.snapshot(profile_id)

        recs: list[Recommendation] = []
        errors: list[str] = []
        for symbol in targets:
            try:
                signal = self.market.signal(symbol, as_of)
            except MarketDataError as exc:
                errors.append(f"{symbol}: {exc}")
                continue
            rec = self.pipeline.run(profile, signal, snapshot, universe, utcnow(), replay=replay)
            if not replay:
                self.store.expire_pending(profile_id, symbol)
            rec = self.store.add_recommendation(rec)
            self.notifier.notify(profile, rec)
            recs.append(rec)

        if not recs and errors:
            raise MarketDataError("; ".join(errors))
        return recs, errors

    # ------------------------------------------------------------------ decisions
    def _pending(self, rec_id: int) -> Recommendation:
        rec = self.store.get_recommendation(rec_id)
        if rec is None:
            raise NotFoundError(f"Recommendation {rec_id} not found")
        if rec.status != RecStatus.pending:
            raise TradeError(f"Recommendation is already {rec.status.value}")
        return rec

    def approve(self, rec_id: int, comment: str | None = None) -> Recommendation:
        rec = self._pending(rec_id)
        self.paper.execute(rec)  # raises TradeError and leaves the recommendation pending
        self.store.set_decision(rec_id, RecStatus.approved, comment)
        return self.store.get_recommendation(rec_id)  # type: ignore[return-value]

    def reject(self, rec_id: int, comment: str | None = None) -> Recommendation:
        self._pending(rec_id)
        self.store.set_decision(rec_id, RecStatus.rejected, comment)
        return self.store.get_recommendation(rec_id)  # type: ignore[return-value]
