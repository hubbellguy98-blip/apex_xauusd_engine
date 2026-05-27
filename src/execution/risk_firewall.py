"""
Apex Engine - Central Risk Orchestration Subsystem
Responsibility: Connects confirmation and scoring components to execute pre-execution risk checks.
Latency Profile: Single-threaded async processing loop running via internal priority event channels.
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any
import structlog

from src.core.base_engine import BaseSubsystem
from src.core.events.event_bus import EventBus
from src.core.events.event_types import EngineEventType
from src.core.domain.constants import EventPriority, OrderDirection
from src.strategy.state_manager import CentralRuntimeStateManager

# Core Risk Infrastructure Imports
from src.core.domain.setup_models import SetupOpportunityNode
from src.core.domain.confirmation_models import ConfirmationSnapshot
from src.core.domain.risk_models import RiskEvaluationSnapshot, RiskSafetyTier, RiskHaltState, PositionSizingPayload

from src.execution.position_sizer import InstitutionalPositionSizer
from src.execution.stop_loss_engine import DynamicStructuralStopEngine
from src.execution.rr_validator import AsymmetricOpportunityValidator
from src.execution.drawdown_protection import CapitalProtectionDrawdownEngine
from src.execution.safety_filters import RealTimeExecutionSafetyFilter

logger = structlog.get_logger()

class RiskManagementOrchestrator(BaseSubsystem):
    """Monitors incoming setups, executes capital sizing algorithms, and manages pre-trade safety checks."""

    def __init__(
        self,
        event_bus: EventBus,
        state_manager: CentralRuntimeStateManager,
        maximum_lots: Optional[float] = None,
        position_sizer: Optional[InstitutionalPositionSizer] = None,
    ) -> None:
        super().__init__("RiskManagementOrchestrator")
        self._event_bus = event_bus
        self._state_manager = state_manager

        # Instantiate composite verification modules
        self._sizer = position_sizer or InstitutionalPositionSizer(maximum_lots=maximum_lots)
        self._stop_engine = DynamicStructuralStopEngine()
        self._rr_validator = AsymmetricOpportunityValidator()
        self._drawdown_firewall = CapitalProtectionDrawdownEngine()
        self._safety_filter = RealTimeExecutionSafetyFilter()

        # Local transaction validation logging database
        self._audits: Dict[str, RiskEvaluationSnapshot] = {}

    async def bootstrap(self) -> None:
        """Initializes dependencies and logs subsystem readiness metrics."""
        logger.info("risk_orchestrator.bootstrap_complete", access_gate="SECURED")

    async def terminate(self) -> None:
        """Gracefully flushes internal auditing metrics caches."""
        self._audits.clear()
        logger.info("risk_orchestrator.terminated")

    async def evaluate_trade_entry_gate(self, setup: SetupOpportunityNode, confirmation: ConfirmationSnapshot, quality_score_multiplier: float) -> tuple[bool, RiskEvaluationSnapshot]:
        """Interrogates a trade candidate, returning validation states and lot assignments."""
        rejection_reasons: List[str] = []
        current_time = datetime.utcnow()
        state_snapshot = self._state_manager.snapshot

        # 1. Run Baseline Portfolio Capital Drawdown Checks
        halt_state = self._drawdown_firewall.evaluate_systemic_restrictions(state_snapshot, current_time)
        if halt_state != RiskHaltState.NOMINAL:
            rejection_reasons.append(f"SYSTEMIC_RISK_HALT_ACTIVE: {halt_state.name}")

        # 2. Enforce Real-Time Execution Safety Filters
        is_safe, safety_message = self._safety_filter.verify_execution_safety(setup, state_snapshot)
        if not is_safe:
            rejection_reasons.append(safety_message)

        # 3. Validate Risk-Reward Target Feasibility
        is_rr_valid, rr_message = self._rr_validator.validate_target_feasibility(setup, state_snapshot)
        if not is_rr_valid:
            rejection_reasons.append(rr_message)

        # 4. Execute Institutional Capital Position Sizing Routines
        try:
            sizing_payload = self._sizer.calculate_lot_size(setup, state_snapshot, quality_score_multiplier)
        except (RuntimeError, ValueError) as exc:
            sizing_payload = PositionSizingPayload(calculated_lots=0.0, risk_percentage_applied=0.0, currency_risk=0.0)
            rejection_reasons.append(f"POSITION_SIZING_CALCULATION_FAILED: {exc}")
        if sizing_payload.calculated_lots <= 0.0:
            rejection_reasons.append("POSITION_SIZING_GENERATED_LOT_ZERO_ALLOCATION")

        is_approved = len(rejection_reasons) == 0

        # 5. Classify the finalized transaction into a Risk Safety Tier
        if is_approved:
            if sizing_payload.risk_percentage_applied >= 0.8:
                safety_tier = RiskSafetyTier.SAFE_INSTITUTIONAL
            else:
                safety_tier = RiskSafetyTier.CONTROLLED_RISK
        else:
            safety_tier = RiskSafetyTier.REJECT

        # Compile the unified risk snapshot
        snapshot = RiskEvaluationSnapshot(
            timestamp=current_time,
            is_approved=is_approved,
            safety_tier=safety_tier,
            halt_state=halt_state,
            sizing=sizing_payload,
            applied_spread_pips=state_snapshot.market.current_spread * 10.0,
            rejection_reasons=rejection_reasons
        )

        # Synchronize audits cache
        self._audits[setup.id] = snapshot

        # 6. Route high-priority event notifications to the system loop
        if is_approved:
            logger.info("risk_orchestrator.trade_approved", setup_id=setup.id, lots=sizing_payload.calculated_lots, risk_pct=f"{sizing_payload.risk_percentage_applied:.2f}%")
            # Publish risk-approved event package to trigger ordering layer execution loops
            # self._event_bus.publish(EngineEventType.RISK_APPROVED, base_payload)
        else:
            logger.warn("risk_orchestrator.trade_blocked", setup_id=setup.id, inhibitors=rejection_reasons)
            # self._event_bus.publish(EngineEventType.SYSTEM_CRITICAL_HALT, fallback)

        return is_approved, snapshot

    def notify_transaction_outcome(self, setup_id: str, is_profitable: bool) -> None:
        """Updates internal drawdown metrics based on the outcome of a closed transaction sequence."""
        if is_profitable:
            self._drawdown_firewall.register_execution_win_event()
        else:
            self._drawdown_firewall.register_execution_loss_event()

    @property
    def validation_history(self) -> Dict[str, RiskEvaluationSnapshot]:
        return self._audits
