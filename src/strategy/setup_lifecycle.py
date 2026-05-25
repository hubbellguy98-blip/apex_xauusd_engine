"""Lifecycle management for discovered setups."""

from dataclasses import replace
from datetime import datetime


class SetupLifecycleManager:
    def evaluate_invalidation(self, setup, state_snapshot, current_time: datetime) -> tuple[bool, str]:
        if current_time >= setup.expiration_time:
            return True, "SETUP_EXPIRED"
        return False, ""

    def apply_confidence_decay(self, setup, current_time: datetime):
        elapsed = max((current_time - setup.creation_time).total_seconds(), 0.0)
        decay = min(elapsed / 2700.0 * 20.0, 20.0)
        return replace(setup, confidence_score=max(setup.confidence_score - decay, 0.0))
