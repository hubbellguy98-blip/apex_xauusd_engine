"""
Apex Engine - Live Trading Profile
Responsibility: Production/live-safe parameter envelope used in real execution sessions.
Latency Profile: Loaded once during process bootstrap.
"""

from __future__ import annotations

from pydantic import Field, SecretStr

from config.base_settings import EngineSettings
from src.core.domain.constants import Environment


class LiveEngineSettings(EngineSettings):
    """Strict live profile with operational risk and reliability defaults."""

    ENV: Environment = Field(default=Environment.PRODUCTION, alias="APEX_ENV")
    DATABASE_URL: SecretStr = Field(
        default=SecretStr("postgresql+asyncpg://postgres:postgres@postgres:5432/apex_db")
    )

    ENABLE_REPLAY_MODE: bool = False
    ENABLE_PAPER_EXECUTION: bool = False
    ENABLE_STRICT_SLIPPAGE_GUARD: bool = True

    HEARTBEAT_INTERVAL_SECONDS: int = 2
    TELEMETRY_FLUSH_INTERVAL_SECONDS: int = 5

    MAX_ALLOWABLE_SPREAD_PIPS: float = 3.2
    MAX_DAILY_LOSS_PCT: float = 2.5