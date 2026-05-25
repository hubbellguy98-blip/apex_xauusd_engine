"""Trade scoring and ranking models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, unique
from typing import List


@unique
class RankedTradeTier(str, Enum):
    ELITE_INSTITUTIONAL_TRADE = "ELITE_INSTITUTIONAL_TRADE"
    HIGH_PROBABILITY_TRADE = "HIGH_PROBABILITY_TRADE"
    MODERATE_TRADE = "MODERATE_TRADE"
    REJECT_TRADE = "REJECT_TRADE"


@dataclass(frozen=True, slots=True)
class ScoringBreakdown:
    structure_score: float
    liquidity_score: float
    momentum_score: float
    volatility_score: float
    rr_score: float
    execution_score: float
    raw_total: float
    applied_penalties: float
    normalized_final_score: float


@dataclass(frozen=True, slots=True)
class PrioritizedExecutionNode:
    setup_id: str
    allocation_priority: int
    ranked_tier: RankedTradeTier
    score_breakdown: ScoringBreakdown
    qualification_timestamp: datetime
    execution_multiplier: float
    is_live_executable: bool
    rejection_payload: List[str] = field(default_factory=list)
