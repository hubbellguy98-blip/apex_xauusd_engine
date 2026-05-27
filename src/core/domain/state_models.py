"""Runtime state containers for the central state manager."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.core.domain.constants import MarketRegime, SessionState


@dataclass(frozen=True, slots=True)
class MarketDomainState:
    last_tick_time: datetime
    current_ask: float
    current_bid: float
    current_mid: float
    current_spread: float
    accumulated_tick_count: int
    is_synchronized: bool


@dataclass(frozen=True, slots=True)
class SessionDomainState:
    current_phase: SessionState
    last_phase_transition: datetime
    killzone_active: bool = False

    @property
    def is_killzone_active(self) -> bool:
        return self.killzone_active


@dataclass(frozen=True, slots=True)
class RegimeDomainState:
    current_regime: MarketRegime | str
    volatility_ratio: float
    volume_z_score: float
    last_calculated_at: datetime


@dataclass(frozen=True, slots=True)
class PositionDomainState:
    net_exposure_lots: float
    floating_pnl_pips: float
    active_position_count: int
    daily_realized_loss_pct: float
    is_trading_halted: bool


@dataclass(frozen=True, slots=True)
class SystemHealthDomainState:
    uptime_seconds: float
    event_bus_queue_backpressure: int
    broker_latency_ms: float
    memory_usage_bytes: int
    last_heartbeat: datetime


@dataclass(frozen=True, slots=True)
class EngineStateContainer:
    sequence_id: int
    timestamp: datetime
    correlation_id: str
    market: MarketDomainState
    session: SessionDomainState
    regime: RegimeDomainState
    positions: PositionDomainState
    health: SystemHealthDomainState
