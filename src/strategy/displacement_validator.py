"""Displacement candle validation."""

from src.core.domain.confirmation_models import ConfirmationTier
from src.core.domain.market_data import CandleNode


class InstitutionalDisplacementValidator:
    def verify_displacement_footprint(self, candle: CandleNode) -> tuple[ConfirmationTier, float]:
        full_range = max(candle.high_p - candle.low_p, 1e-9)
        body = abs(candle.close_p - candle.open_p)
        score = min(100.0, body / full_range * 100.0)
        if score < 45.0:
            return ConfirmationTier.INVALID, score
        if score >= 80.0:
            return ConfirmationTier.HIGH_CONVICTION, score
        return ConfirmationTier.MEDIUM_CONVICTION, score
