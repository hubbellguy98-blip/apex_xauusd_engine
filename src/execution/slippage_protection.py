"""Order routing slippage checks."""


class ExecutionSlippageProtectionEngine:
    def __init__(self, max_spread_pips: float = 3.5) -> None:
        self._max_spread_pips = max_spread_pips

    def verify_execution_parameters(self, request, state_snapshot) -> tuple[bool, str]:
        spread_pips = getattr(state_snapshot.market, "current_spread", 0.0) * 10.0
        if spread_pips > self._max_spread_pips:
            return False, "SPREAD_ABOVE_ROUTING_LIMIT"
        return True, "OK"
