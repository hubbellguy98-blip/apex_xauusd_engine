"""
Apex Engine - Confirmation Coordination Engine Subsystem
Responsibility: Orchestrates validation modules, tallies weights, and controls risk execution entry parameters.
Latency Profile: Highly optimized unified processing path executing on internal data streams.
"""

import asyncio
from datetime import datetime
from typing import List, Dict, Any, Tuple
import structlog

from src.core.base_engine import BaseSubsystem
from src.core.events.event_bus import EventBus
from src.core.events.event_types import EngineEventType
from src.core.domain.market_data import TickNode, CandleNode
from src.core.domain.constants import OrderDirection, EventPriority
from src.strategy.state_manager import CentralRuntimeStateManager

# Infrastructure Analytics Imports
from src.strategy.momentum_validator import InstitutionalMomentumValidator
from src.strategy.displacement_validator import InstitutionalDisplacementValidator
from src.strategy.mtf_alignment import MultiTimeframeAlignmentFramework
from src.core.domain.confirmation_models import ConfirmationSnapshot, ConfirmationTier, ConfirmationMetrics, AlignmentStatus

logger = structlog.get_logger()

class TradeConfirmationOrchestrator(BaseSubsystem):
    """Executes validation pipelines, checks parameters against filters, and manages system state."""

    def __init__(self, event_bus: EventBus, state_manager: CentralRuntimeStateManager) -> None:
        super().__init__("TradeConfirmationOrchestrator")
        self._event_bus = event_bus
        self._state_manager = state_manager
        
        # Instantiate structural sub-validation blocks
        self._momentum_engine = InstitutionalMomentumValidator()
        self._displacement_engine = InstitutionalDisplacementValidator()
        self._alignment_engine = MultiTimeframeAlignmentFramework()

        # Local window sliding history cache (Tracks execution anchor parameters)
        self._candle_window_1m: List[CandleNode] = []

    async def bootstrap(self) -> None:
        """Attaches execution validation subscribers to system event channels."""
        self._event_bus.subscribe(EngineEventType.CANDLE_CLOSED, self.on_candle_evacuation)
        logger.info("confirmation_orchestrator.bootstrap_complete", pipeline_status="ONLINE")

    async def terminate(self) -> None:
        """Gracefully flushes confirmation reference metrics."""
        self._candle_window_1m.clear()
        logger.info("confirmation_orchestrator.terminated")

    async def on_candle_evacuation(self, event: CandleNode) -> None:
        """Updates internal historical reference loops when a candle closes."""
        if event.timeframe != "1m":
            return
        self._candle_window_1m.append(event)
        if len(self._candle_window_1m) > 10:
            self._candle_window_1m.pop(0)

    async def process_candidate_setup(self, direction: OrderDirection, setup_time: datetime, trigger_candle: CandleNode, directional_bias_matrix: Dict[str, str], internal_tick_velocity: float) -> tuple[bool, ConfirmationSnapshot]:
        """Interrogates a trade setup candidate, returning validation states and confidence scores."""
        invalidation_reasons: List[str] = []
        validated_components: List[str] = []
        
        # Pull live operational contexts from central memory snapshots
        state_snapshot = self._state_manager.snapshot
        
        # 1. Enforce Core Algorithmic Session Boundaries
        if not state_snapshot.session.is_killzone_active:
            invalidation_reasons.append("SETUP_OUTSIDE_KILLZONE_BOUNDARIES")
            
        if state_snapshot.regime.current_regime == "POST_NEWS_CHAOS":
            invalidation_reasons.append("TOXIC_POST_NEWS_REGIME_ACTIVE")

        # 2. Evaluate Momentum Pulse Suitability Parameters
        mom_tier, mom_score = self._momentum_engine.validate_momentum_pulse(
            trigger_candle, self._candle_window_1m, internal_tick_velocity
        )
        if mom_tier != ConfirmationTier.INVALID:
            validated_components.append("MOMENTUM_CONVICTION")
        else:
            invalidation_reasons.append("MOMENTUM_ACCELERATION_INSUFFICIENT")

        # 3. Evaluate Displacement Candle Footprint Quality
        disp_tier, disp_score = self._displacement_engine.verify_displacement_footprint(trigger_candle)
        if disp_tier != ConfirmationTier.INVALID:
            validated_components.append("DISPLACEMENT_VALIDATED")
        else:
            invalidation_reasons.append("DISPLACEMENT_WICK_EXHAUSTION_BREACH")

        # 4. Multi-Timeframe Trend Cohesion Verification
        align_status, align_score = self._alignment_engine.evaluate_alignment_matrix(direction, directional_bias_matrix)
        if align_status == AlignmentStatus.FULLY_ALIGNED or align_status == AlignmentStatus.PARTIALLY_ALIGNED:
            validated_components.append("MTF_TREND_ALIGNMENT")
        else:
            invalidation_reasons.append("MTF_STRUCTURAL_BIAS_CONFLICT")

        # 5. Composite Score Calculation Model
        # Formula uses static weights to prevent floating pointer calculation drift
        weighted_score = (mom_score * 0.35) + (disp_score * 0.35) + (align_score * 0.30)
        
        is_validated = len(invalidation_reasons) == 0 and weighted_score >= 75.0
        
        if not is_validated and weighted_score >= 75.0:
            weighted_score = 74.0 # Force decay limit under filtered parameters

        overall_tier = ConfirmationTier.INVALID
        if is_validated:
            if weighted_score >= 88.0:
                overall_tier = ConfirmationTier.HIGH_CONVICTION
            elif weighted_score >= 75.0:
                overall_tier = ConfirmationTier.MEDIUM_CONVICTION
        
        # Compile the metrics container
        metrics = ConfirmationMetrics(
            momentum_velocity_score=mom_score,
            displacement_ratio=disp_score,
            wick_rejection_pct=100.0 - disp_score,  # Inverse representation factor
            mtf_alignment_score=align_score,
            volatility_expansion_factor=state_snapshot.regime.volatility_ratio,
            session_efficiency_index=100.0 if state_snapshot.session.is_killzone_active else 0.0
        )

        snapshot = ConfirmationSnapshot(
            timestamp=datetime.utcnow(),
            overall_tier=overall_tier,
            confidence_score=float(weighted_score),
            is_validated=is_validated,
            alignment=align_status,
            metrics=metrics,
            validated_components=validated_components,
            invalidation_reasons=invalidation_reasons
        )

        # 6. Synchronize confirmations and trigger event alerts
        if is_validated:
            logger.info("confirmation_orchestrator.setup_approved", score=weighted_score, tier=overall_tier.value)
            # Route an abstract confirmation token to clear pending gates
            # event_bus routing can use standard infrastructure base events
        else:
            logger.warn("confirmation_orchestrator.setup_rejected", reasons=invalidation_reasons, score=weighted_score)

        return is_validated, snapshot
