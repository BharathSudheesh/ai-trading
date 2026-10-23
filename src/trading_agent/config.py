"""Application settings. Every field can be overridden with a ``TA_``-prefixed env variable."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TA_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=lambda: Path.cwd() / "data")
    data_source: Literal["auto", "yfinance", "synthetic"] = "auto"
    ml_algorithm: Literal["random_forest", "xgboost"] = "random_forest"
    default_symbols: list[str] = ["AAPL", "MSFT", "NVDA", "JPM", "XOM"]

    history_years: int = Field(default=5, ge=2, le=15)
    horizon_days: int = Field(default=5, ge=1, le=60)
    neutral_band: float = Field(default=0.03, ge=0, le=0.2)

    starting_cash: float = Field(default=100_000.0, gt=0)
    commission_bps: float = Field(default=5.0, ge=0)
    slippage_bps: float = Field(default=5.0, ge=0)

    random_seed: int = 42
    cache_ttl_hours: float = 6.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "trading_agent.db"

    @property
    def model_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def fee_rate(self) -> float:
        """Commission as a fraction of notional."""
        return self.commission_bps / 10_000

    @property
    def slippage_rate(self) -> float:
        return self.slippage_bps / 10_000

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.model_dir, self.cache_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
