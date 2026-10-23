"""Runs one signal through Personal -> Strategy -> Risk -> Decision Engine.

The same pipeline serves live recommendations and the walk-forward backtest, so the
backtest measures exactly the logic the user sees.
"""

from __future__ import annotations

from datetime import datetime

from ..schemas import MarketSignal, PortfolioSnapshot, Recommendation, UserProfile
from .engine import DecisionEngine
from .personal import PersonalAgent
from .risk import RiskAgent
from .strategy import StrategyAgent


class DecisionPipeline:
    def __init__(
        self,
        personal: PersonalAgent,
        strategy: StrategyAgent,
        risk: RiskAgent,
        engine: DecisionEngine,
    ):
        self.personal = personal
        self.strategy = strategy
        self.risk = risk
        self.engine = engine

    def run(
        self,
        profile: UserProfile,
        signal: MarketSignal,
        portfolio: PortfolioSnapshot,
        universe: list[str],
        created_at: datetime,
        replay: bool = False,
    ) -> Recommendation:
        ctx = self.personal.evaluate(profile, signal, universe, set(portfolio.positions))
        strategy = risk = None
        if ctx.candidate is not None:
            strategy = self.strategy.evaluate(profile.trading_style, ctx.candidate, signal.features)
            risk = self.risk.evaluate(
                profile, ctx.candidate, signal.symbol, signal.price, signal.features, portfolio
            )
        return self.engine.decide(profile, signal, ctx, strategy, risk, created_at, replay)
