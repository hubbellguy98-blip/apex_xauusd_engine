"""Contextual scoring penalties."""


class DynamicContextualPenaltyEngine:
    def calculate_cumulative_penalties(self, setup, state_snapshot) -> float:
        penalty = 0.0
        if getattr(state_snapshot.positions, "active_position_count", 0) > 0:
            penalty += 10.0
        if getattr(state_snapshot.positions, "is_trading_halted", False):
            penalty += 100.0
        return penalty
