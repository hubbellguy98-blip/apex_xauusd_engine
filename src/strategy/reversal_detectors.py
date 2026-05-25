"""Liquidity-sweep reversal detector placeholders with deterministic gates."""

from src.core.domain.constants import OrderDirection


class LiquiditySweepReversalDetector:
    def evaluate_sweep_reversal(self, tick, liquidity_pools, pivots, state_snapshot):
        for pool in liquidity_pools:
            if getattr(pool, "is_swept", False):
                direction = OrderDirection.SELL if getattr(pool, "is_buy_side", False) else OrderDirection.BUY
                entry = tick.mid
                stop = entry + 2.0 if direction == OrderDirection.SELL else entry - 2.0
                target = entry - 6.0 if direction == OrderDirection.SELL else entry + 6.0
                return True, direction, entry, stop, target
        return False, OrderDirection.BUY, 0.0, 0.0, 0.0
