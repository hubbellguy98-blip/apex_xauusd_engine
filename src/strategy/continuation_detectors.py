"""Continuation setup detectors."""

from src.core.domain.constants import OrderDirection


class TrendContinuationSetupDetector:
    def evaluate_ob_continuation(self, candle, order_blocks, state_snapshot):
        for block in order_blocks:
            direction = getattr(block, "direction", OrderDirection.BUY)
            entry = candle.close_p
            stop = candle.low_p if direction == OrderDirection.BUY else candle.high_p
            risk = abs(entry - stop) or 1.0
            target = entry + risk * 3.0 if direction == OrderDirection.BUY else entry - risk * 3.0
            return True, direction, entry, stop, target
        return False, OrderDirection.BUY, 0.0, 0.0, 0.0
