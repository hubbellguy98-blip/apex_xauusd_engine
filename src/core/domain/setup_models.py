"""Trade setup domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, unique

from src.core.domain.constants import OrderDirection


@unique
class SetupType(str, Enum):
    LIQUIDITY_SWEEP_REVERSAL = "LIQUIDITY_SWEEP_REVERSAL"
    ORDER_BLOCK_CONTINUATION = "ORDER_BLOCK_CONTINUATION"
    FVG_CONTINUATION = "FVG_CONTINUATION"


@unique
class SetupQualityTier(str, Enum):
    INVALID_SETUP = "INVALID_SETUP"
    STANDARD = "STANDARD"
    HIGH_PROBABILITY = "HIGH_PROBABILITY"
    ELITE_INSTITUTIONAL = "ELITE_INSTITUTIONAL"


@dataclass(frozen=True, slots=True)
class SetupOpportunityNode:
    id: str
    setup_type: SetupType
    direction: OrderDirection
    entry_price: float
    stop_loss: float
    take_profit: float
    estimated_rr: float
    quality_tier: SetupQualityTier
    confidence_score: float
    creation_time: datetime
    expiration_time: datetime
    correlation_id: str
    timeframe: str
