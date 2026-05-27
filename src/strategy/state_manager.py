"""
Apex Engine - Central Business Runtime State Manager
Responsibility: Single Source of Truth (SSOT) coordinator orchestration engine context.
Latency Profile: Atomic single-threaded asyncio state access layer.
"""

import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List
from collections import deque
import structlog

from src.core.base_engine import BaseSubsystem
from src.core.domain.constants import MarketRegime, SessionState
from src.core.domain.state_models import (
    EngineStateContainer, MarketDomainState, SessionDomainState,
    RegimeDomainState, PositionDomainState, SystemHealthDomainState
)
from src.core.state.validators import StateInvariantValidator
from src.core.state.mutator import EngineStateMutator
from src.core.state.snapshots import StateSnapshotSerializer

logger = structlog.get_logger()

class CentralRuntimeStateManager(BaseSubsystem):
    """Orchestrates in-memory domain states, handling updates and rollback sequences."""

    def __init__(self, historical_journal_capacity: int = 10000) -> None:
        super().__init__("CentralRuntimeStateManager")
        self._validator = StateInvariantValidator()
        self._mutator = EngineStateMutator(self._validator)
        
        # Instantiate base engine state container matching cold boot environments
        self._active_state: EngineStateContainer = self._generate_cold_boot_state()
        self._journal: deque[EngineStateContainer] = deque(maxlen=historical_journal_capacity)
        
        # Read-Write Operational Gateways (Cooperative async single-threaded pattern safe)
        self._state_lock = asyncio.Lock()

    async def bootstrap(self) -> None:
        """Initializes state parameters and sets state readiness flags."""
        async with self._state_lock:
            self._active_state = self._generate_cold_boot_state()
            self._journal.append(self._active_state)
            logger.info("state_manager.bootstrap_complete", status="READY")

    async def terminate(self) -> None:
        """Safely commits structural diagnostics and clears transient tracking arrays."""
        async with self._state_lock:
            self._journal.clear()
            logger.info("state_manager.terminated")

    @property
    def snapshot(self) -> EngineStateContainer:
        """Returns the current state configuration. Direct mutation is prevented by frozen boundaries."""
        return self._active_state

    async def commit_market_update(self, update_fields: Dict[str, Any], correlation_id: str) -> EngineStateContainer:
        """Applies an atomic mutation to the market state domain."""
        async with self._state_lock:
            self._active_state = self._mutator.mutate_market(self._active_state, update_fields, correlation_id)
            self._journal.append(self._active_state)
            return self._active_state

    async def commit_session_update(self, update_fields: Dict[str, Any], correlation_id: str) -> EngineStateContainer:
        """Applies an atomic mutation to the session state domain."""
        async with self._state_lock:
            self._active_state = self._mutator.mutate_session(self._active_state, update_fields, correlation_id)
            self._journal.append(self._active_state)
            return self._active_state

    async def commit_position_update(self, update_fields: Dict[str, Any], correlation_id: str) -> EngineStateContainer:
        """Applies an atomic mutation to the position state domain."""
        async with self._state_lock:
            self._active_state = self._mutator.mutate_positions(self._active_state, update_fields, correlation_id)
            self._journal.append(self._active_state)
            return self._active_state

    async def commit_health_update(self, update_fields: Dict[str, Any], correlation_id: str) -> EngineStateContainer:
        """Applies an atomic mutation to the health state domain."""
        # Bypasses locking overhead for high-frequency infrastructure logging metrics
        self._active_state = self._mutator.mutate_health(self._active_state, update_fields, correlation_id)
        return self._active_state

    async def revert_to_sequence(self, target_sequence_id: int) -> bool:
        """Rolls back the active state to a specific sequence ID to clear tracking anomalies."""
        async with self._state_lock:
            for state_record in reversed(self._journal):
                if state_record.sequence_id == target_sequence_id:
                    # Target version verified in journal array memory
                    self._active_state = state_record
                    logger.warn("state_manager.rollback_executed", target_sequence=target_sequence_id)
                    return True
            logger.error("state_manager.rollback_failed_sequence_not_found", target_sequence=target_sequence_id)
            return False

    def load_historical_snapshot(self, snapshot_payload: Dict[str, Any]) -> None:
        """Forces an explicit data configuration onto the active state container to support backtest replays."""
        parsed_container = StateSnapshotSerializer.deserialize_from_dict(snapshot_payload)
        self._validator.verify_invariants(parsed_container)
        self._active_state = parsed_container
        self._journal.append(self._active_state)

    def _generate_cold_boot_state(self) -> EngineStateContainer:
        """Constructs the initial baseline state for clear initialization tracking."""
        init_time = datetime.utcnow()
        return EngineStateContainer(
            sequence_id=0,
            timestamp=init_time,
            correlation_id="COLD_BOOT",
            market=MarketDomainState(
                last_tick_time=init_time, current_ask=0.0, current_bid=0.0,
                current_mid=0.0, current_spread=0.0, accumulated_tick_count=0, is_synchronized=False
            ),
            session=SessionDomainState(
                current_phase=SessionState.SYSTEM_SHUTDOWN, last_phase_transition=init_time,
                killzone_active=False,
            ),
            regime=RegimeDomainState(
                current_regime=MarketRegime.UNKNOWN, volatility_ratio=1.0,
                volume_z_score=0.0, last_calculated_at=init_time
            ),
            positions=PositionDomainState(
                net_exposure_lots=0.0, floating_pnl_pips=0.0, active_position_count=0,
                daily_realized_loss_pct=0.0, is_trading_halted=False
            ),
            health=SystemHealthDomainState(
                uptime_seconds=0.0, event_bus_queue_backpressure=0,
                broker_latency_ms=0.0, memory_usage_bytes=0, last_heartbeat=init_time
            )
        )
