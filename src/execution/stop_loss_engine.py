"""Structural stop-loss helper."""


class DynamicStructuralStopEngine:
    def derive_stop_loss(self, setup, state_snapshot) -> float:
        return setup.stop_loss
