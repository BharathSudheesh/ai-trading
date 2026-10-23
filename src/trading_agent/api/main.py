"""FastAPI application factory.

Run with:  uvicorn trading_agent.api.main:create_app --factory --reload
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import Settings, get_settings
from ..data import MarketDataError, MarketDataProvider
from ..services import NotFoundError, TradeError, build_container
from .routes import router

STATIC_DIR = Path(__file__).resolve().parents[1] / "web" / "static"


def create_app(
    settings: Settings | None = None, provider: MarketDataProvider | None = None
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Personalized AI Trading & Investment Agent",
        version=__version__,
        description=(
            "Decision support for traders and investors. Recommendations are suggestions only; "
            "the user approves every action and approved actions are paper-traded."
        ),
    )
    app.state.c = build_container(settings, provider)
    app.include_router(router, prefix="/api")

    def _error(status: int):
        async def handler(_: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(status_code=status, content={"detail": str(exc)})

        return handler

    app.add_exception_handler(NotFoundError, _error(404))
    app.add_exception_handler(TradeError, _error(409))
    app.add_exception_handler(MarketDataError, _error(502))
    app.add_exception_handler(ValueError, _error(422))

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    return app
