"""
Apex Engine - Post-Execution Trade Management Matrix
Responsibility: Handles trailing parameters, tracks partial take-profit limits, and applies breakeven logic.
Latency Profile: Constant-time state processing loops.
"""

import structlog
from src.core.domain.constants import OrderDirection
from src.core.domain.market_data import TickNode

logger = structlog.get_logger()

class InstitutionalTradeLifecycleManager:
    """Manages post-execution trailing adjustments and handles position scale-outs."""

    def __init__(self, partial_tp_rr: float = 3.0, volume_scale_out_pct: float = 0.5) -> None:
        self._partial_tp_rr = partial_tp_rr
        self._scale_out_factor = volume_scale_out_pct

    def evaluate_lifecycle_modifications(self, direction: OrderDirection, entry: float, sl: float, tp: float, current_tick: TickNode, has_taken_partial: bool) -> tuple[bool, Optional[float], bool, str]:
        """Calculates trailing targets and scale-out triggers based on incoming price inputs."""
        price = current_tick.mid
        stop_distance = abs(entry - sl)
        
        if stop_distance <= 0.0:
            return False, None, False, "NONE"

        # 1. Process Partial Profit Extraction Targets at designated risk reward extensions
        if not has_taken_partial:
            if direction == OrderDirection.BUY and price >= entry + (stop_distance * self._partial_tp_rr):
                # Bullish scale-out condition reached
                new_sl = entry + 0.05  # Move stop to breakeven + transaction cost buffer
                return True, float(new_sl), True, "PARTIAL_TP_AND_BREAKEVEN_TRIGGERED"
                
            elif direction == OrderDirection.SELL and price <= entry - (stop_distance * self._partial_tp_rr):
                new_sl = entry - 0.05
                return True, float(new_sl), True, "PARTIAL_TP_AND_BREAKEVEN_TRIGGERED"

        # 2. Apply Volatility Trailing Logic behind active structural price runs
        if has_taken_partial:
            # Trailing trailing boundaries once risk exposure has been completely covered
            if direction == OrderDirection.BUY and price > entry + (stop_distance * 4.5):
                # Lock profits behind advanced structural extension runs
                suggested_trail_sl = price - (stop_distance * 1.5)
                if suggested_trail_sl > sl:
                    return True, float(suggested_trail_sl), True, "TRAILING_STOP_PROPAGATION"
            elif direction == OrderDirection.SELL and price < entry - (stop_distance * 4.5):
                suggested_trail_sl = price + (stop_distance * 1.5)
                if suggested_trail_sl < sl:
                    return True, float(suggested_trail_sl), True, "TRAILING_STOP_PROPAGATION"

        return False, None, has_taken_partial, "NONE"