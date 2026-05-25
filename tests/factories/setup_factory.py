"""Setup factory helpers."""

from datetime import datetime, timedelta, timezone

from src.core.domain.constants import OrderDirection
from src.core.domain.setup_models import SetupOpportunityNode, SetupQualityTier, SetupType


class SetupOpportunityFactory:
    @staticmethod
    def create_setup(entry: float = 2400.0, sl: float = 2395.0, tp: float = 2415.0) -> SetupOpportunityNode:
        now = datetime.now(timezone.utc)
        rr = abs(tp - entry) / max(abs(entry - sl), 1e-9)
        return SetupOpportunityNode(
            id="TEST_SETUP",
            setup_type=SetupType.LIQUIDITY_SWEEP_REVERSAL,
            direction=OrderDirection.BUY,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            estimated_rr=rr,
            quality_tier=SetupQualityTier.HIGH_PROBABILITY,
            confidence_score=80.0,
            creation_time=now,
            expiration_time=now + timedelta(minutes=30),
            correlation_id="TEST",
            timeframe="1m",
        )
