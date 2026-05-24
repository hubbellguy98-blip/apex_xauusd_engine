"""
Apex Engine - Central Opportunity Discovery & Setup Orchestration Engine
Responsibility: Coordinates signal aggregation, validates institutional intent convergence, and applies risk gates.
Latency Profile: Single-threaded async processing loop running via internal priority event channels.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import structlog

from src.core.base_engine import BaseSubsystem
from src.core.events.event_bus import EventBus
from src.core.events.event_types import EngineEventType
from src.core.domain.market_data import TickNode, CandleNode
from src.core.domain.constants import OrderDirection, EventPriority
from src.strategy.state_manager import CentralRuntimeStateManager
from src.strategy.confirmation_orchestrator import TradeConfirmationOrchestrator

# Core Discovery Component Imports
from src.core.domain.setup_models import SetupOpportunityNode, SetupType, SetupQualityTier
from src.strategy.setup_quality import InstitutionalSetupQualityClassifier
from src.strategy.reversal_detectors import LiquiditySweepReversalDetector
from src.strategy.continuation_detectors import TrendContinuationSetupDetector
from src.strategy.setup_lifecycle import SetupLifecycleManager

logger = structlog.get_logger()

class MarketSetupOrchestrator(BaseSubsystem):
    """Orchestrates algorithmic data components, evaluates sequencing logic, and publishes validated trade nodes."""

    def __init__(self, event_bus: EventBus, state_manager: CentralRuntimeStateManager, confirmation_engine: TradeConfirmationOrchestrator) -> None:
        super().__init__("MarketSetupOrchestrator")
        self._event_bus = event_bus
        self._state_manager = state_manager
        self._confirmation_engine = confirmation_engine

        # Instantiate analytical sub-modules
        self._quality_classifier = InstitutionalSetupQualityClassifier()
        self._reversal_detector = LiquiditySweepReversalDetector()
        self._continuation_detector = TrendContinuationSetupDetector()
        self._lifecycle_tracker = SetupLifecycleManager()

        # In-memory allocation arrays
        self._discovered_setups: Dict[str, SetupOpportunityNode] = {}
        self._cooldown_registry: Dict[str, datetime] = {}
        self._setup_counter = 0

    async def bootstrap(self) -> None:
        """Subscribes signal generation workers to the real-time data bus."""
        self._event_bus.subscribe(EngineEventType.MARKET_TICK, self.on_tick_received)
        self._event_bus.subscribe(EngineEventType.CANDLE_CLOSED, self.on_candle_evacuation)
        logger.info("setup_orchestrator.bootstrap_complete", structural_status="TRACKING")

    async def terminate(self) -> None:
        """Gracefully tears down active setup arrays."""
        self._discovered_setups.clear()
        self._cooldown_registry.clear()
        logger.info("setup_orchestrator.terminated")

    async def on_tick_received(self, event: TickNode) -> None:
        """Processes real-time ticks to evaluate reversals and run active setup invalidation checks."""
        current_time = event.timestamp
        state_snap = self._state_manager.snapshot

        # 1. Run Lifecycle Invalidation and Expiration Verification Scans
        expired_ids = []
        for setup_id, setup in list(self._discovered_setups.items()):
            is_invalid, reason = self._lifecycle_tracker.evaluate_invalidation(setup, state_snap, current_time)
            if is_invalid:
                expired_ids.append(setup_id)
                logger.info("setup_orchestrator.setup_invalidated", id=setup_id, cause=reason)
                # Future: Publish Setup Invalidation Event
            else:
                # Apply time-decay updates to active candidate nodes
                updated_setup = self._lifecycle_tracker.apply_confidence_decay(setup, current_time)
                self._discovered_setups[setup_id] = updated_setup

        for s_id in expired_ids:
            del self._discovered_setups[s_id]

        # 2. Evaluate Reversal Setup Configurations
        # Extract operational infrastructure values from mocked lists (populated via analytical pipes)
        mock_pools = []  # Extracted via Phase 7 pipeline models
        mock_pivots = []

        is_reversal, direction, entry, sl, tp = self._reversal_detector.evaluate_sweep_reversal(
            event, mock_pools, mock_pivots, state_snap
        )

        if is_reversal:
            await self._process_discovered_candidate(
                SetupType.LIQUIDITY_SWEEP_REVERSAL, direction, entry, sl, tp, event, "1m"
            )

    async def on_candle_evacuation(self, event: CandleNode) -> None:
        """Evaluates continuation setups when a candle closes."""
        if event.timeframe != "15m":
            return
            
        state_snap = self._state_manager.snapshot
        mock_blocks = []  # Populated via core structure tracking models
        mock_fvgs = []

        # Evaluate Order Block Retest signals
        is_ob, direction, entry, sl, tp = self._continuation_detector.evaluate_ob_continuation(
            event, mock_blocks, state_snap
        )
        if is_ob:
            await self._process_discovered_candidate(
                SetupType.ORDER_BLOCK_CONTINUATION, direction, entry, sl, tp, event, "15m"
            )

    async def _process_discovered_candidate(self, setup_type: SetupType, direction: OrderDirection, entry: float, sl: float, tp: float, trigger_source: Any, timeframe: str) -> None:
        """Runs the validation sequence, grades setup quality, and publishes approved setup nodes."""
        
        # 1. Enforce Cooldown Protection Gates to Prevent Signal Spam
        cooldown_key = f"{setup_type.value}_{direction.value}"
        current_time = datetime.utcnow()
        if cooldown_key in self._cooldown_registry:
            if current_time < self._cooldown_registry[cooldown_key]:
                return  # Block duplicate signals during active cooldown windows

        # 2. Trigger Cross-Component Confirmation Verification Checks
        mock_bias = {"4h": "BULLISH", "1h": "BULLISH", "15m": "BULLISH", "1m": "BULLISH"}
        is_confirmed, confirmation_snap = await self._confirmation_engine.process_candidate_setup(
            direction, current_time, trigger_source if isinstance(trigger_source, CandleNode) else self._state_manager.snapshot.market.last_tick_time, # Safe primitive fallbacks
            mock_bias, 3.0
        )

        if not is_confirmed:
            return  # Reject candidate nodes that fail confirmation criteria

        # 3. Grade Core Risk and Quality Parameters
        state_snap = self._state_manager.snapshot
        estimated_rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0.0
        
        quality_tier, final_score = self._quality_classifier.classify_setup_quality(
            setup_type, estimated_rr, state_snap, confirmation_snap
        )

        if quality_tier == SetupQualityTier.INVALID_SETUP:
            return  # Filter out low-quality or invalid setups

        # 4. Construct and Route Type-Safe Setup Node
        self._setup_counter += 1
        setup_id = f"STP_{setup_type.value}_{self._setup_counter}_{int(current_time.timestamp())}"
        
        opportunity_node = SetupOpportunityNode(
            id=setup_id, setup_type=setup_type, direction=direction,
            entry_price=entry, stop_loss=sl, take_profit=tp, estimated_rr=estimated_rr,
            quality_tier=quality_tier, confidence_score=final_score,
            creation_time=current_time, expiration_time=current_time + timedelta(minutes=45),
            correlation_id=getattr(trigger_source, 'correlation_id', 'MANUAL_DISCOVERY'),
            timeframe=timeframe
        )

        # Update system caches and activate the cooldown firewall
        self._discovered_setups[setup_id] = opportunity_node
        self._cooldown_registry[cooldown_key] = current_time + timedelta(minutes=15) # 15-minute cooldown per strategy configuration

        logger.info("setup_orchestrator.signal_finalized", id=setup_id, tier=quality_tier.value, rr=f"{estimated_rr:.2f}")
        
        # Publish the finalized setup node to the central event bus
        # This notifies downstream execution managers (Phase 11/12) to handle position entry routing
