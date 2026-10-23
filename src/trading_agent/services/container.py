"""Wires settings, storage, ML, agents and services together."""

from __future__ import annotations

from dataclasses import dataclass

from ..agents import (
    DecisionEngine,
    DecisionPipeline,
    MarketDataAgent,
    NotificationAgent,
    PersonalAgent,
    RiskAgent,
    StrategyAgent,
)
from ..config import Settings
from ..data import MarketDataProvider, get_provider
from ..db import Store
from ..ml import ModelStore
from .backtest import Backtester
from .feedback import FeedbackService
from .paper_trading import PaperTradingService
from .recommendation import RecommendationService


@dataclass
class Container:
    settings: Settings
    store: Store
    provider: MarketDataProvider
    models: ModelStore
    market: MarketDataAgent
    pipeline: DecisionPipeline
    notifier: NotificationAgent
    paper: PaperTradingService
    recommender: RecommendationService
    feedback: FeedbackService
    backtester: Backtester


def build_container(
    settings: Settings, provider: MarketDataProvider | None = None
) -> Container:
    settings.ensure_dirs()
    store = Store(settings.db_path)
    provider = provider or get_provider(settings)
    models = ModelStore(settings, provider)
    market = MarketDataAgent(provider, models, settings)
    pipeline = DecisionPipeline(
        PersonalAgent(), StrategyAgent(),
        RiskAgent(settings.fee_rate, settings.slippage_rate), DecisionEngine(),
    )
    notifier = NotificationAgent(store)
    paper = PaperTradingService(store, market, settings)
    return Container(
        settings=settings, store=store, provider=provider, models=models, market=market,
        pipeline=pipeline, notifier=notifier, paper=paper,
        recommender=RecommendationService(store, market, pipeline, notifier, paper, settings),
        feedback=FeedbackService(store, market),
        backtester=Backtester(market, pipeline, settings),
    )
