"""Realtime execution safety filters."""


class RealTimeExecutionSafetyFilter:
    def __init__(self, max_spread_pips: float = 3.5) -> None:
        self._max_spread_pips = max_spread_pips

    def verify_execution_safety(self, setup, state_snapshot) -> tuple[bool, str]:
        spread_pips = getattr(state_snapshot.market, "current_spread", 0.0) * 10.0
        if spread_pips > self._max_spread_pips:
            return False, "SPREAD_ABOVE_EXECUTION_LIMIT"
        if getattr(state_snapshot.positions, "is_trading_halted", False):
            return False, "TRADING_HALTED"
        return True, "OK"
