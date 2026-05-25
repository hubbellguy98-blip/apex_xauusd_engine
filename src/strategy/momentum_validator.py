"""Momentum confirmation scoring."""

from src.core.domain.confirmation_models import ConfirmationTier
from src.core.domain.market_data import CandleNode


class InstitutionalMomentumValidator:
    def validate_momentum_pulse(self, candle: CandleNode, recent_candles: list[CandleNode], tick_velocity: float) -> tuple[ConfirmationTier, float]:
        body = abs(candle.close_p - candle.open_p)
        spread = max(candle.high_p - candle.low_p, 1e-9)
        score = min(100.0, (body / spread * 70.0) + min(tick_velocity * 10.0, 30.0))
        tier = ConfirmationTier.INVALID if score < 50.0 else ConfirmationTier.MEDIUM_CONVICTION
        if score >= 85.0:
            tier = ConfirmationTier.HIGH_CONVICTION
        return tier, score
