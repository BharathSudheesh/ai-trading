"""Domain models shared by agents, services, storage and the API.

Percent-valued fields end in ``_pct`` and use percent units (``10`` means 10%).
Probabilities and confidences are fractions in ``[0, 1]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^=]{1,12}$")


class RiskLevel(str, Enum):
    conservative = "conservative"
    moderate = "moderate"
    aggressive = "aggressive"


class TradingStyle(str, Enum):
    momentum = "momentum"
    swing = "swing"
    mean_reversion = "mean_reversion"
    long_term = "long_term"


class Goal(str, Enum):
    growth = "growth"
    income = "income"
    capital_preservation = "capital_preservation"
    speculation = "speculation"


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalLabel(str, Enum):
    potential_buy = "Potential Buy"
    potential_sell = "Potential Sell"
    neutral = "Neutral"


class RecStatus(str, Enum):
    pending = "pending"  # BUY/SELL waiting for the user's decision
    approved = "approved"
    rejected = "rejected"
    info = "info"  # HOLD, nothing to approve
    expired = "expired"  # replaced by a newer recommendation


# Defaults applied when a profile does not set its own limits.
RISK_PRESETS: dict[RiskLevel, dict[str, float]] = {
    RiskLevel.conservative: dict(
        max_position_pct=10, stop_loss_pct=5, risk_per_trade_pct=0.5,
        max_volatility_pct=30, max_drawdown_pct=10, min_confidence_pct=60,
    ),
    RiskLevel.moderate: dict(
        max_position_pct=15, stop_loss_pct=8, risk_per_trade_pct=1.0,
        max_volatility_pct=45, max_drawdown_pct=15, min_confidence_pct=56,
    ),
    RiskLevel.aggressive: dict(
        max_position_pct=25, stop_loss_pct=12, risk_per_trade_pct=2.0,
        max_volatility_pct=70, max_drawdown_pct=25, min_confidence_pct=53,
    ),
}


class UserProfile(BaseModel):
    """Investment preferences and risk limits for one user."""

    id: int | None = None
    name: str = Field(min_length=1, max_length=60)
    risk_level: RiskLevel = RiskLevel.moderate
    trading_style: TradingStyle = TradingStyle.swing
    goals: list[Goal] = Field(default_factory=lambda: [Goal.growth])
    preferred_assets: list[str] = Field(default_factory=list, max_length=20)

    # Risk limits. Left empty, they are filled from the risk-level preset.
    max_position_pct: float | None = Field(default=None, gt=0, le=100)
    stop_loss_pct: float | None = Field(default=None, gt=0, le=50)
    risk_per_trade_pct: float | None = Field(default=None, gt=0, le=10)
    max_volatility_pct: float | None = Field(default=None, gt=0, le=300)
    max_drawdown_pct: float | None = Field(default=None, gt=0, le=90)
    min_confidence_pct: float | None = Field(default=None, ge=50, le=95)
    notify_enabled: bool = True

    @field_validator("preferred_assets")
    @classmethod
    def _normalise_assets(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for raw in value:
            sym = raw.strip().upper()
            if not SYMBOL_RE.match(sym):
                raise ValueError(f"'{raw}' is not a valid ticker symbol")
            if sym not in out:
                out.append(sym)
        return out

    @model_validator(mode="after")
    def _apply_presets(self) -> UserProfile:
        for key, preset in RISK_PRESETS[self.risk_level].items():
            if getattr(self, key) is None:
                setattr(self, key, preset)
        return self


class Check(BaseModel):
    name: str
    passed: bool
    detail: str


class MarketSignal(BaseModel):
    symbol: str
    as_of: date
    price: float
    prob_up: float
    label: SignalLabel
    confidence: float
    horizon_days: int
    features: dict[str, float]


class PersonalContext(BaseModel):
    relevant: bool
    candidate: Action | None
    reasons: list[str]


class StrategyResult(BaseModel):
    matched: bool
    score: float
    rule: str
    checks: list[Check]


class RiskResult(BaseModel):
    within_limit: bool
    risk_score: int
    risk_label: str
    size_pct: float
    qty: int
    stop_loss_price: float | None
    checks: list[Check]


class Recommendation(BaseModel):
    id: int | None = None
    profile_id: int
    symbol: str
    as_of: date
    created_at: datetime
    price: float
    horizon_days: int
    prob_up: float
    signal: SignalLabel
    confidence: float
    action: Action
    strategy_status: str  # Matched | Not matched | N/A
    risk_status: str  # Within limit | Exceeds limit | N/A
    risk_score: int | None = None
    risk_label: str | None = None
    suggested_qty: int = 0
    suggested_position_pct: float = 0.0
    stop_loss_price: float | None = None
    blocked_by: str | None = None  # profile | no_signal | confidence | no_position | strategy | risk
    summary: str
    reasons: list[str]
    strategy_checks: list[Check] = Field(default_factory=list)
    risk_checks: list[Check] = Field(default_factory=list)
    replay: bool = False
    status: RecStatus = RecStatus.info
    decided_at: datetime | None = None
    comment: str | None = None
    outcome_return: float | None = None
    outcome_correct: bool | None = None
    outcome_date: date | None = None


class Position(BaseModel):
    symbol: str
    qty: float
    avg_price: float


class Order(BaseModel):
    id: int | None = None
    profile_id: int
    rec_id: int | None
    symbol: str
    side: str
    qty: float
    price: float
    fees: float
    created_at: datetime


class PositionView(BaseModel):
    symbol: str
    qty: float
    avg_price: float
    price: float
    market_value: float
    pnl: float
    pnl_pct: float
    weight_pct: float


class PortfolioSummary(BaseModel):
    profile_id: int
    cash: float
    invested: float
    equity: float
    starting_cash: float
    total_pnl: float
    total_return_pct: float
    peak_equity: float
    drawdown_pct: float
    positions: list[PositionView]


class Notification(BaseModel):
    id: int | None = None
    profile_id: int
    rec_id: int | None
    symbol: str
    message: str
    created_at: datetime
    is_read: bool = False


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Point-in-time view of a portfolio that the risk agent evaluates against."""

    cash: float
    equity: float
    peak_equity: float
    positions: dict[str, Position] = field(default_factory=dict)

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity * 100)
