"""Risk and sizing domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, unique
from typing import List


@unique
class RiskSafetyTier(str, Enum):
    REJECT = "REJECT"
    CONTROLLED_RISK = "CONTROLLED_RISK"
    SAFE_INSTITUTIONAL = "SAFE_INSTITUTIONAL"


@unique
class RiskHaltState(str, Enum):
    NOMINAL = "NOMINAL"
    DAILY_LOSS_BREACHED = "DAILY_LOSS_BREACHED"
    CONSECUTIVE_LOSS_BREACHED = "CONSECUTIVE_LOSS_BREACHED"
    TRADING_HALTED = "TRADING_HALTED"


@dataclass(frozen=True, slots=True)
class PositionSizingPayload:
    calculated_lots: float
    risk_percentage_applied: float
    currency_risk: float = 0.0


@dataclass(frozen=True, slots=True)
class RiskEvaluationSnapshot:
    timestamp: datetime
    is_approved: bool
    safety_tier: RiskSafetyTier
    halt_state: RiskHaltState
    sizing: PositionSizingPayload
    applied_spread_pips: float
    rejection_reasons: List[str] = field(default_factory=list)
