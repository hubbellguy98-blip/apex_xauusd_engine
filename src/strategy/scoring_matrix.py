"""
Apex Engine - Trade Scoring & Prioritization Orchestrator
Responsibility: Integrates component analytics, coordinates normalizations, ranks setups, and publishes signals.
Latency Profile: Highly optimized single-threaded processing loop executing via execution priority event channels.
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
import structlog

from src.core.base_engine import BaseSubsystem
from src.core.events.event_bus import EventBus
from src.core.events.event_types import EngineEventType
from src.core.domain.constants import EventPriority
from src.strategy.state_manager import CentralRuntimeStateManager

# Analytical Engine Layer Module Imports
from src.core.domain.setup_models import SetupOpportunityNode, SetupQualityTier
from src.core.domain.confirmation_models import ConfirmationSnapshot
from src.core.domain.scoring_models import PrioritizedExecutionNode, ScoringBreakdown, RankedTradeTier

from src.strategy.scoring_components import StructureLiquidityScorer, VolatilityMomentumScorer, RRExecutionScorer
from src.strategy.penalty_engine import DynamicContextualPenaltyEngine

logger = structlog.get_logger()

class TradeScoringOrchestrator(BaseSubsystem):
    """Coordinates risk parameters and prioritizes execution nodes using a unified evaluation framework."""

    def __init__(self, event_bus: EventBus, state_manager: CentralRuntimeStateManager) -> None:
        super().__init__("TradeScoringOrchestrator")
        self._event_bus = event_bus
        self._state_manager = state_manager
        
        # Instantiate composite matrix evaluation primitives
        self._struct_liq_scorer = StructureLiquidityScorer()
        self._vol_mom_scorer = VolatilityMomentumScorer()
        self._rr_exec_scorer = RRExecutionScorer()
        self._penalty_engine = DynamicContextualPenaltyEngine()

        # In-memory execution ranking queue arrays
        self._prioritized_registry: Dict[str, PrioritizedExecutionNode] = {}

    async def bootstrap(self) -> None:
        """Initializes internal variables and connects components to the system bus."""
        # Subsystem bindings are handled inside main execution lifecycle manager chains
        logger.info("trade_scoring_orchestrator.bootstrap_complete", framework_status="LIVE")

    async def terminate(self) -> None:
        """Gracefully flushes internal tracking states."""
        self._prioritized_registry.clear()
        logger.info("trade_scoring_orchestrator.terminated")

    async def process_and_rank_setup(self, setup: SetupOpportunityNode, confirmation: ConfirmationSnapshot) -> PrioritizedExecutionNode:
        """Evaluates trade setup configurations to derive prioritized execution profiles."""
        rejection_payload: List[str] = []
        state_snapshot = self._state_manager.snapshot

        # 1. Process Individual Sub-Component Scores
        struct_score = self._struct_liq_scorer.evaluate_structural_quality(setup, state_snapshot)
        liq_score = self._struct_liq_scorer.evaluate_liquidity_quality(setup, state_snapshot)
        mom_score = self._vol_mom_scorer.evaluate_momentum_quality(confirmation)
        vol_score = self._vol_mom_scorer.evaluate_volatility_quality(state_snapshot)
        rr_score = self._rr_exec_scorer.evaluate_rr_quality(setup)
        exec_score = self._rr_exec_scorer.evaluate_execution_efficiency(state_snapshot)

        # 2. Compute Weighted Composite Configuration Score
        raw_total = (
            (struct_score * 0.20) +
            (liq_score * 0.20) +
            (mom_score * 0.15) +
            (vol_score * 0.15) +
            (rr_score * 0.15) +
            (exec_score * 0.15)
        )

        # 3. Run Contextual Penalty Deduction Operations
        applied_penalties = self._penalty_engine.calculate_cumulative_penalties(setup, state_snapshot)
        normalized_final_score = max(min(raw_total - applied_penalties, 100.0), 0.0)

        # 4. Apply Hard Pre-Execution Gate Filters
        if normalized_final_score < 75.0:
            rejection_payload.append("SCORE_BELOW_MINIMUM_EXECUTION_THRESHOLD")
        if exec_score <= 0.0:
            rejection_payload.append("CRITICAL_SPREAD_EXPANSION_VIOLATION")
        if rr_score <= 0.0:
            rejection_payload.append("ASYMMETRIC_RISK_REWARD_INSUFFICIENT")

        is_executable = len(rejection_payload) == 0

        # 5. Classify the finalized score into an operational Ranked Trade Tier
        if is_executable:
            if normalized_final_score >= 90.0 and setup.quality_tier == SetupQualityTier.ELITE_INSTITUTIONAL:
                ranked_tier = RankedTradeTier.ELITE_INSTITUTIONAL_TRADE
                priority_index = 0
                size_multiplier = 1.0  # Max planned risk allocation multiplier
            elif normalized_final_score >= 80.0:
                ranked_tier = RankedTradeTier.HIGH_PROBABILITY_TRADE
                priority_index = 1
                size_multiplier = 0.75
            else:
                ranked_tier = RankedTradeTier.MODERATE_TRADE
                priority_index = 2
                size_multiplier = 0.50
        else:
            ranked_tier = RankedTradeTier.REJECT_TRADE
            priority_index = 99
            size_multiplier = 0.0

        # Compile matrix breakdown containers
        breakdown = ScoringBreakdown(
            structure_score=struct_score, liquidity_score=liq_score,
            momentum_score=mom_score, volatility_score=vol_score,
            rr_score=rr_score, execution_score=exec_score,
            raw_total=raw_total, applied_penalties=applied_penalties,
            normalized_final_score=normalized_final_score
        )

        execution_node = PrioritizedExecutionNode(
            setup_id=setup.id, allocation_priority=priority_index,
            ranked_tier=ranked_tier, score_breakdown=breakdown,
            qualification_timestamp=datetime.utcnow(),
            execution_multiplier=size_multiplier, is_live_executable=is_executable,
            rejection_payload=rejection_payload
        )

        # 6. Synchronize priorities and manage memory registry caches
        self._prioritized_registry[setup.id] = execution_node
        self._clean_registry_cache_bounds()

        # 7. Route specialized structural event alerts to the central system loop
        if is_executable:
            logger.info("scoring_orchestrator.elite_trade_qualified", 
                        setup_id=setup.id, final_score=f"{normalized_final_score:.2f}", tier=ranked_tier.value)
            # Future pipeline tracking point: publish risk-approval verification payloads
        else:
            logger.warn("scoring_orchestrator.candidate_filtered", setup_id=setup.id, limits=rejection_payload)

        return execution_node

    def _clean_registry_cache_bounds(self) -> None:
        """Keeps the in-memory registry size bounded to control performance overhead."""
        if len(self._prioritized_registry) > 1000:
            # Evict the oldest key safely
            oldest_key = next(iter(self._prioritized_registry))
            del self._prioritized_registry[oldest_key]

    @property
    def active_prioritized_nodes(self) -> Dict[str, PrioritizedExecutionNode]:
        return self._prioritized_registry
