"""
Apex Engine - Simulation Profile
Responsibility: Backtest/replay-safe configuration envelope for deterministic research runs.
Latency Profile: Loaded once during process bootstrap.
"""

from __future__ import annotations

from pydantic import Field, SecretStr

from config.base_settings import EngineSettings
from src.core.domain.constants import Environment


class SimulationEngineSettings(EngineSettings):
    """Simulation profile tuned for deterministic offline execution."""

    ENV: Environment = Field(default=Environment.BACKTEST, alias="APEX_ENV")
    DATABASE_URL: SecretStr = Field(
        default=SecretStr("sqlite+aiosqlite:///./apex_simulation.db")
    )

    ENABLE_REPLAY_MODE: bool = True
    ENABLE_PAPER_EXECUTION: bool = True
    ENABLE_STRICT_SLIPPAGE_GUARD: bool = False

    HEARTBEAT_INTERVAL_SECONDS: int = 10
    TELEMETRY_FLUSH_INTERVAL_SECONDS: int = 30

    MAX_ALLOWABLE_SPREAD_PIPS: float = 6.0
    MAX_DAILY_LOSS_PCT: float = 10.0