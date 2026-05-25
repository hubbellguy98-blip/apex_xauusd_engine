"""Multi-timeframe directional alignment scoring."""

from src.core.domain.confirmation_models import AlignmentStatus
from src.core.domain.constants import OrderDirection


class MultiTimeframeAlignmentFramework:
    def evaluate_alignment_matrix(self, direction: OrderDirection, bias_matrix: dict[str, str]) -> tuple[AlignmentStatus, float]:
        if not bias_matrix:
            return AlignmentStatus.CONFLICTED, 0.0
        desired = "BULLISH" if direction == OrderDirection.BUY else "BEARISH"
        aligned = sum(1 for bias in bias_matrix.values() if str(bias).upper() == desired)
        ratio = aligned / len(bias_matrix)
        if ratio >= 0.75:
            return AlignmentStatus.FULLY_ALIGNED, ratio * 100.0
        if ratio >= 0.5:
            return AlignmentStatus.PARTIALLY_ALIGNED, ratio * 100.0
        return AlignmentStatus.CONFLICTED, ratio * 100.0
