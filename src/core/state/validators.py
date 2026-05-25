"""Invariant checks for runtime state containers."""

from src.core.domain.state_models import EngineStateContainer
from src.shared.exceptions import ValidationError


class StateInvariantValidator:
    """Validates critical state relationships before mutation."""

    def verify_invariants(self, state: EngineStateContainer) -> None:
        if state.market.current_ask < state.market.current_bid:
            raise ValidationError("Market ask price cannot be below bid price.")
        if state.market.current_spread < 0:
            raise ValidationError("Market spread cannot be negative.")
        if state.positions.active_position_count < 0:
            raise ValidationError("Active position count cannot be negative.")
