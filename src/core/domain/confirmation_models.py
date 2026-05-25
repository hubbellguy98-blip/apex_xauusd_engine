"""Confirmation pipeline domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, unique
from typing import List


@unique
class ConfirmationTier(str, Enum):
    INVALID = "INVALID"
    LOW_CONVICTION = "LOW_CONVICTION"
    MEDIUM_CONVICTION = "MEDIUM_CONVICTION"
    HIGH_CONVICTION = "HIGH_CONVICTION"


@unique
class AlignmentStatus(str, Enum):
    CONFLICTED = "CONFLICTED"
    PARTIALLY_ALIGNED = "PARTIALLY_ALIGNED"
    FULLY_ALIGNED = "FULLY_ALIGNED"


@dataclass(frozen=True, slots=True)
class ConfirmationMetrics:
    momentum_velocity_score: float
    displacement_ratio: float
    wick_rejection_pct: float
    mtf_alignment_score: float
    volatility_expansion_factor: float
    session_efficiency_index: float


@dataclass(frozen=True, slots=True)
class ConfirmationSnapshot:
    timestamp: datetime
    overall_tier: ConfirmationTier
    confidence_score: float
    is_validated: bool
    alignment: AlignmentStatus
    metrics: ConfirmationMetrics
    validated_components: List[str] = field(default_factory=list)
    invalidation_reasons: List[str] = field(default_factory=list)
