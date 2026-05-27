"""Serialization helpers for state snapshots."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict

from src.core.domain.constants import MarketRegime, SessionState
from src.core.domain.state_models import (
    EngineStateContainer,
    MarketDomainState,
    PositionDomainState,
    RegimeDomainState,
    SessionDomainState,
    SystemHealthDomainState,
)


class StateSnapshotSerializer:
    """Converts state containers to and from plain dictionaries."""

    @staticmethod
    def serialize_to_dict(state: EngineStateContainer) -> Dict[str, Any]:
        return asdict(state)

    @staticmethod
    def deserialize_from_dict(payload: Dict[str, Any]) -> EngineStateContainer:
        def parse_dt(value: Any) -> datetime:
            if isinstance(value, datetime):
                return value
            return datetime.fromisoformat(value)

        market = payload["market"]
        session = payload["session"]
        regime = payload["regime"]
        positions = payload["positions"]
        health = payload["health"]
        return EngineStateContainer(
            sequence_id=payload["sequence_id"],
            timestamp=parse_dt(payload["timestamp"]),
            correlation_id=payload["correlation_id"],
            market=MarketDomainState(**market),
            session=SessionDomainState(
                current_phase=SessionState(session["current_phase"]),
                last_phase_transition=parse_dt(session["last_phase_transition"]),
                killzone_active=bool(session.get("killzone_active", False)),
            ),
            regime=RegimeDomainState(
                current_regime=MarketRegime(regime["current_regime"]),
                volatility_ratio=regime["volatility_ratio"],
                volume_z_score=regime["volume_z_score"],
                last_calculated_at=parse_dt(regime["last_calculated_at"]),
            ),
            positions=PositionDomainState(**positions),
            health=SystemHealthDomainState(**health),
        )
