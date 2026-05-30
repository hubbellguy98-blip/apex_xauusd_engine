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
class BrokerSizingSpecification:
    """Broker/account values required to convert a stop into account-currency risk."""

    symbol: str
    account_equity: float
    account_currency: str
    volume_min: float
    volume_step: float
    volume_max: float


@dataclass(frozen=True, slots=True)
class PreSubmissionRiskAssessment:
    """Final broker-quote safety result immediately before a routed order."""

    is_approved: bool
    live_entry_price: float
    normalized_lots: float
    currency_risk: float
    maximum_currency_risk: float
    spread_price: float
    quote_age_seconds: float
    rejection_reasons: List[str] = field(default_factory=list)
    requested_lots: float = 0.0
    adapted_to_fit_risk: bool = False
    demo_minimum_lot_override: bool = False


@dataclass(frozen=True, slots=True)
class RiskEvaluationSnapshot:
    timestamp: datetime
    is_approved: bool
    safety_tier: RiskSafetyTier
    halt_state: RiskHaltState
    sizing: PositionSizingPayload
    applied_spread_pips: float
    rejection_reasons: List[str] = field(default_factory=list)
