"""REST endpoints. The browser UI is a thin client over these."""

from __future__ import annotations

import math
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from .. import __version__
from ..ml import build_features
from ..schemas import (
    RISK_PRESETS,
    SYMBOL_RE,
    Goal,
    RiskLevel,
    TradingStyle,
    UserProfile,
)
from ..services import Container

router = APIRouter()

DISCLAIMER = (
    "Educational decision-support software, not financial advice. Recommendations are "
    "model estimates and can be wrong. Approved actions are simulated (paper trading)."
)


def _c(request: Request) -> Container:
    return request.app.state.c


def _symbols(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    out = [v.strip().upper() for v in values]
    bad = [v for v in out if not SYMBOL_RE.match(v)]
    if bad:
        raise ValueError(f"Invalid ticker symbol: {', '.join(bad)}")
    return list(dict.fromkeys(out))


class GenerateRequest(BaseModel):
    profile_id: int
    symbols: list[str] | None = None
    as_of: date | None = Field(default=None, description="Replay a past date (point-in-time model)")

    @field_validator("symbols")
    @classmethod
    def _check_symbols(cls, v):
        return _symbols(v)

    @field_validator("as_of")
    @classmethod
    def _not_future(cls, v):
        if v and v > date.today():
            raise ValueError("as_of cannot be in the future")
        return v


class DecisionRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=500)


class BacktestRequest(BaseModel):
    profile_id: int
    symbols: list[str] | None = None
    months: int = Field(default=12, ge=3, le=36)
    initial_cash: float | None = Field(default=None, gt=0)
    use_stop_loss: bool = True

    @field_validator("symbols")
    @classmethod
    def _check_symbols(cls, v):
        return _symbols(v)


# ---------------------------------------------------------------------------- meta
@router.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@router.get("/meta")
def meta(request: Request):
    c = _c(request)
    s = c.settings
    return {
        "version": __version__,
        "data_source": c.market.source,
        "model": c.models.info(),
        "risk_levels": [e.value for e in RiskLevel],
        "trading_styles": [e.value for e in TradingStyle],
        "goals": [e.value for e in Goal],
        "presets": {k.value: v for k, v in RISK_PRESETS.items()},
        "default_symbols": s.default_symbols,
        "horizon_days": s.horizon_days,
        "neutral_band": s.neutral_band,
        "starting_cash": s.starting_cash,
        "commission_bps": s.commission_bps,
        "slippage_bps": s.slippage_bps,
        "disclaimer": DISCLAIMER,
    }


# ------------------------------------------------------------------------ profiles
@router.post("/profiles", status_code=201)
def create_profile(profile: UserProfile, request: Request):
    c = _c(request)
    return c.store.create_profile(profile, c.settings.starting_cash)


@router.get("/profiles")
def list_profiles(request: Request):
    return _c(request).store.list_profiles()


@router.get("/profiles/{profile_id}")
def get_profile(profile_id: int, request: Request):
    return _c(request).recommender.require_profile(profile_id)


@router.put("/profiles/{profile_id}")
def update_profile(profile_id: int, profile: UserProfile, request: Request):
    c = _c(request)
    c.recommender.require_profile(profile_id)
    return c.store.update_profile(profile_id, profile)


# --------------------------------------------------------------------------- market
@router.get("/market/{symbol}")
def market_history(symbol: str, request: Request, days: int = Query(180, ge=30, le=1000)):
    symbol = symbol.upper()
    if not SYMBOL_RE.match(symbol):
        raise HTTPException(422, "Invalid ticker symbol")
    c = _c(request)
    df = c.market.history(symbol)
    sma20, sma50 = df["close"].rolling(20).mean(), df["close"].rolling(50).mean()
    view = df.tail(days)

    def num(x):
        return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 2)

    bars = [
        {"date": ts.date().isoformat(), "close": num(row.close),
         "sma20": num(sma20.loc[ts]), "sma50": num(sma50.loc[ts])}
        for ts, row in zip(view.index, view.itertuples(), strict=True)
    ]
    return {"symbol": symbol, "source": c.market.source, "price": bars[-1]["close"],
            "as_of": bars[-1]["date"], "bars": bars}


@router.get("/market/{symbol}/signal")
def market_signal(symbol: str, request: Request, as_of: date | None = None):
    return _c(request).market.signal(symbol.upper(), as_of)


@router.get("/market/{symbol}/features")
def market_features(symbol: str, request: Request):
    df = _c(request).market.history(symbol.upper())
    last = build_features(df).iloc[-1]
    return {k: (None if math.isnan(v) else float(v)) for k, v in last.items()}


# --------------------------------------------------------------- recommendations
@router.post("/recommendations/generate")
def generate(body: GenerateRequest, request: Request):
    recs, errors = _c(request).recommender.generate(body.profile_id, body.symbols, body.as_of)
    return {"recommendations": recs, "errors": errors}


@router.get("/recommendations")
def list_recommendations(
    request: Request,
    profile_id: int,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    return _c(request).store.list_recommendations(profile_id, status, limit)


@router.post("/recommendations/{rec_id}/approve")
def approve(rec_id: int, request: Request, body: DecisionRequest | None = None):
    return _c(request).recommender.approve(rec_id, body.comment if body else None)


@router.post("/recommendations/{rec_id}/reject")
def reject(rec_id: int, request: Request, body: DecisionRequest | None = None):
    return _c(request).recommender.reject(rec_id, body.comment if body else None)


# --------------------------------------------------------------------- portfolio
@router.get("/portfolio/{profile_id}")
def portfolio(profile_id: int, request: Request):
    return _c(request).paper.summary(profile_id)


@router.get("/orders")
def orders(request: Request, profile_id: int, limit: int = Query(50, ge=1, le=500)):
    return _c(request).store.list_orders(profile_id, limit)


# ---------------------------------------------------------------------- backtest
@router.post("/backtest")
def backtest(body: BacktestRequest, request: Request):
    c = _c(request)
    profile = c.recommender.require_profile(body.profile_id)
    symbols = body.symbols or profile.preferred_assets or c.settings.default_symbols
    return c.backtester.run(profile, symbols, body.months, body.initial_cash, body.use_stop_loss)


# ---------------------------------------------------------------------- feedback
@router.post("/feedback/{profile_id}/evaluate")
def evaluate(profile_id: int, request: Request):
    c = _c(request)
    c.recommender.require_profile(profile_id)
    return {"updated": c.feedback.evaluate_outcomes(profile_id)}


@router.get("/feedback/{profile_id}")
def feedback(profile_id: int, request: Request):
    c = _c(request)
    profile = c.recommender.require_profile(profile_id)
    stats = c.feedback.stats(profile_id)
    return {"stats": stats, "suggestions": c.feedback.suggestions(profile, stats)}


# ----------------------------------------------------------------- notifications
@router.get("/notifications")
def notifications(request: Request, profile_id: int, limit: int = Query(30, ge=1, le=200)):
    return _c(request).store.list_notifications(profile_id, limit)


@router.post("/notifications/read")
def read_notifications(request: Request, profile_id: int):
    _c(request).store.mark_notifications_read(profile_id)
    return {"ok": True}


# ------------------------------------------------------------------------- model
@router.get("/model")
def model_info(request: Request):
    return _c(request).models.info()


@router.post("/model/train")
def train_model_endpoint(request: Request):
    return _c(request).models.train().metadata
