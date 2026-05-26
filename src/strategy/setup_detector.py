"""
Apex Engine - Central Opportunity Discovery & Setup Orchestration Engine
Responsibility: Coordinates signal aggregation, validates institutional intent convergence, and applies risk gates.
Latency Profile: Single-threaded async processing loop running via internal priority event channels.
"""

from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, List, Tuple
import structlog

from src.core.base_engine import BaseSubsystem
from src.core.events.event_bus import EventBus
from src.core.events.event_types import EngineEventType
from src.core.domain.market_data import TickNode, CandleNode
from src.core.domain.constants import OrderDirection
from src.core.domain.confirmation_models import ConfirmationSnapshot
from src.strategy.state_manager import CentralRuntimeStateManager
from src.strategy.confirmation_orchestrator import TradeConfirmationOrchestrator

# Core Discovery Component Imports
from src.analytics.liquidity_engine import LiquidityInterceptionEngine
from src.analytics.session_engine import GoldSessionIntelligenceEngine
from src.analytics.structure_engine import DeterministicStructureEngine
from src.core.domain.setup_models import SetupOpportunityNode, SetupType, SetupQualityTier
from src.strategy.setup_quality import InstitutionalSetupQualityClassifier
from src.strategy.reversal_detectors import LiquiditySweepReversalDetector
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
        self._lifecycle_tracker = SetupLifecycleManager()
        self._structure_engine = DeterministicStructureEngine("1m")
        self._liquidity_engine = LiquidityInterceptionEngine("1m")
        self._session_engine = GoldSessionIntelligenceEngine()

        # In-memory allocation arrays
        self._discovered_setups: Dict[str, SetupOpportunityNode] = {}
        self._cooldown_registry: Dict[str, datetime] = {}
        self._candles_by_timeframe: Dict[str, Deque[CandleNode]] = defaultdict(lambda: deque(maxlen=50))
        self._structural_pivots: List[Any] = []
        self._qualified_candidates: List[Tuple[SetupOpportunityNode, ConfirmationSnapshot]] = []
        self._warmup_sweeps_cleared = 0
        self._setup_counter = 0
        self._diagnostics: Dict[str, int] = {
            "live_ticks_processed": 0,
            "live_sweeps_detected": 0,
            "reversal_candidates_detected": 0,
            "cooldown_blocks": 0,
            "confirmation_blocks": 0,
            "quality_blocks": 0,
            "setup_nodes_finalized": 0,
        }
        self._latest_confirmation_reasons: List[str] = []

    async def bootstrap(self) -> None:
        """Subscribes signal generation workers to the real-time data bus."""
        self._event_bus.subscribe(EngineEventType.MARKET_TICK, self.on_tick_received)
        self._event_bus.subscribe(EngineEventType.CANDLE_CLOSED, self.on_candle_evacuation)
        logger.info("setup_orchestrator.bootstrap_complete", structural_status="TRACKING")

    async def terminate(self) -> None:
        """Gracefully tears down active setup arrays."""
        self._discovered_setups.clear()
        self._cooldown_registry.clear()
        self._candles_by_timeframe.clear()
        self._structural_pivots.clear()
        self._qualified_candidates.clear()
        logger.info("setup_orchestrator.terminated")

    async def on_tick_received(self, event: TickNode) -> None:
        """Processes real-time ticks to evaluate reversals and run active setup invalidation checks."""
        self._diagnostics["live_ticks_processed"] += 1
        current_time = event.timestamp.replace(tzinfo=None)
        session, _, _ = self._session_engine.evaluate_temporal_context(event.timestamp, event.mid)
        await self._state_manager.commit_market_update(
            {
                "last_tick_time": current_time,
                "current_ask": event.ask,
                "current_bid": event.bid,
                "current_mid": event.mid,
                "current_spread": event.spread,
                "accumulated_tick_count": self._state_manager.snapshot.market.accumulated_tick_count + 1,
                "is_synchronized": True,
            },
            event.correlation_id or f"SETUP_TICK_{event.sequence_id}",
        )
        await self._state_manager.commit_session_update(
            {"current_phase": session, "last_phase_transition": current_time},
            event.correlation_id or f"SETUP_SESSION_{event.sequence_id}",
        )
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

        # 2. Evaluate real liquidity sweeps against structural pools learned from 1m candles.
        swept_pools = self._liquidity_engine.evaluate_tick_sweeps(event)
        if not swept_pools or not self._candles_by_timeframe["1m"]:
            return
        self._diagnostics["live_sweeps_detected"] += len(swept_pools)
        is_reversal, direction, entry, sl, tp = self._reversal_detector.evaluate_sweep_reversal(
            event, [pool for pool, _ in swept_pools], self._structural_pivots, state_snap
        )

        if is_reversal:
            self._diagnostics["reversal_candidates_detected"] += 1
            await self._process_discovered_candidate(
                SetupType.LIQUIDITY_SWEEP_REVERSAL,
                direction,
                entry,
                sl,
                tp,
                self._candles_by_timeframe["1m"][-1],
                "1m",
            )

    async def on_candle_evacuation(self, event: CandleNode) -> None:
        """Stores closed bars and converts real 1m structure into liquidity pools."""
        self._candles_by_timeframe[event.timeframe].append(event)
        if event.timeframe != "1m":
            return
        new_pivots, _ = self._structure_engine.ingest_candle_close(event)
        for pivot in new_pivots:
            self._liquidity_engine.register_structural_pivot_pool(pivot)
        self._structural_pivots.extend(new_pivots)

    async def seed_closed_candle(self, event: CandleNode) -> None:
        """Warm analytical state without emitting signals from already completed history."""
        if event.timeframe == "1m":
            close_tick = TickNode(
                symbol=event.symbol,
                timestamp=event.end_time,
                bid=event.close_p,
                ask=event.close_p,
                volume=event.volume,
                sequence_id=event.sequence_id,
                correlation_id="MT5_HISTORY_WARMUP",
            )
            self._warmup_sweeps_cleared += len(self._liquidity_engine.evaluate_tick_sweeps(close_tick))
        await self.on_candle_evacuation(event)

    async def _process_discovered_candidate(self, setup_type: SetupType, direction: OrderDirection, entry: float, sl: float, tp: float, trigger_source: CandleNode, timeframe: str) -> None:
        """Runs the validation sequence, grades setup quality, and publishes approved setup nodes."""
        
        # 1. Enforce Cooldown Protection Gates to Prevent Signal Spam
        cooldown_key = f"{setup_type.value}_{direction.value}"
        current_time = datetime.utcnow()
        if cooldown_key in self._cooldown_registry:
            if current_time < self._cooldown_registry[cooldown_key]:
                self._diagnostics["cooldown_blocks"] += 1
                return  # Block duplicate signals during active cooldown windows

        # 2. Trigger confirmation using directional bias learned from received closed candles.
        directional_bias = self.directional_bias_matrix
        is_confirmed, confirmation_snap = await self._confirmation_engine.process_candidate_setup(
            direction, current_time, trigger_source, directional_bias, 3.0
        )

        if not is_confirmed:
            self._diagnostics["confirmation_blocks"] += 1
            self._latest_confirmation_reasons = list(confirmation_snap.invalidation_reasons)
            return  # Reject candidate nodes that fail confirmation criteria

        # 3. Grade Core Risk and Quality Parameters
        state_snap = self._state_manager.snapshot
        estimated_rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0.0
        
        quality_tier, final_score = self._quality_classifier.classify_setup_quality(
            setup_type, estimated_rr, state_snap, confirmation_snap
        )

        if quality_tier == SetupQualityTier.INVALID_SETUP:
            self._diagnostics["quality_blocks"] += 1
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
        self._qualified_candidates.append((opportunity_node, confirmation_snap))
        self._diagnostics["setup_nodes_finalized"] += 1

        logger.info("setup_orchestrator.signal_finalized", id=setup_id, tier=quality_tier.value, rr=f"{estimated_rr:.2f}")

    @property
    def directional_bias_matrix(self) -> Dict[str, str]:
        """Build directional bias using the oldest/newest closed bar held per timeframe."""
        matrix: Dict[str, str] = {}
        for timeframe in ("4h", "1h", "15m", "1m"):
            candles = self._candles_by_timeframe.get(timeframe, deque())
            if len(candles) < 2:
                matrix[timeframe] = "NEUTRAL"
            elif candles[-1].close_p > candles[0].close_p:
                matrix[timeframe] = "BULLISH"
            elif candles[-1].close_p < candles[0].close_p:
                matrix[timeframe] = "BEARISH"
            else:
                matrix[timeframe] = "NEUTRAL"
        return matrix

    def drain_qualified_candidates(self) -> List[Tuple[SetupOpportunityNode, ConfirmationSnapshot]]:
        """Return newly confirmed setup nodes for downstream scoring and risk processing."""
        candidates = list(self._qualified_candidates)
        self._qualified_candidates.clear()
        return candidates

    @property
    def tracked_structural_pivots(self) -> int:
        return len(self._structural_pivots)

    @property
    def warmup_sweeps_cleared(self) -> int:
        return self._warmup_sweeps_cleared

    @property
    def diagnostic_snapshot(self) -> Dict[str, Any]:
        """Expose live decision funnel counts without changing strategy outcomes."""
        nearest_pool = self._liquidity_engine.nearest_active_pool(self._state_manager.snapshot.market.current_mid)
        return {
            **self._diagnostics,
            "latest_confirmation_reasons": list(self._latest_confirmation_reasons),
            "nearest_active_pool": nearest_pool,
        }
