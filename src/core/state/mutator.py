"""Immutable state mutation helpers."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Dict

from src.core.domain.state_models import EngineStateContainer
from src.core.state.validators import StateInvariantValidator


class EngineStateMutator:
    """Applies validated immutable updates to domain sub-states."""

    def __init__(self, validator: StateInvariantValidator) -> None:
        self._validator = validator

    def _commit(self, state: EngineStateContainer, correlation_id: str, **updates: Any) -> EngineStateContainer:
        next_state = replace(
            state,
            sequence_id=state.sequence_id + 1,
            timestamp=datetime.utcnow(),
            correlation_id=correlation_id,
            **updates,
        )
        self._validator.verify_invariants(next_state)
        return next_state

    def mutate_market(self, state: EngineStateContainer, fields: Dict[str, Any], correlation_id: str) -> EngineStateContainer:
        market = replace(state.market, **fields)
        return self._commit(state, correlation_id, market=market)

    def mutate_session(self, state: EngineStateContainer, fields: Dict[str, Any], correlation_id: str) -> EngineStateContainer:
        session = replace(state.session, **fields)
        return self._commit(state, correlation_id, session=session)

    def mutate_positions(self, state: EngineStateContainer, fields: Dict[str, Any], correlation_id: str) -> EngineStateContainer:
        positions = replace(state.positions, **fields)
        return self._commit(state, correlation_id, positions=positions)

    def mutate_health(self, state: EngineStateContainer, fields: Dict[str, Any], correlation_id: str) -> EngineStateContainer:
        health = replace(state.health, **fields)
        return self._commit(state, correlation_id, health=health)
