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
from src.strategy.setup_lifecycle import SetupLifecycleManager
from src.strategy.ict_smc_strategy_selector import ICTSMCStrategySelector

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
        self._lifecycle_tracker = SetupLifecycleManager()
        self._structure_engine = DeterministicStructureEngine("1m")
        self._liquidity_engine = LiquidityInterceptionEngine("1m")
        self._session_engine = GoldSessionIntelligenceEngine()
        self._strategy_selector = ICTSMCStrategySelector()

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
            "ict_strategy_evaluations": 0,
            "ict_strategy_selected": 0,
            "ict_strategy_blocks": 0,
        }
        self._latest_confirmation_reasons: List[str] = []
        self._latest_strategy_selection: Dict[str, Any] = {}

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
        session, is_killzone, _ = self._session_engine.evaluate_temporal_context(event.timestamp, event.mid)
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
            {
                "current_phase": session,
                "last_phase_transition": current_time,
                "killzone_active": is_killzone,
            },
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

        # 2. Evaluate the ICT/SMC strategy library against the live market context.
        swept_pools = self._liquidity_engine.evaluate_tick_sweeps(event)
        if swept_pools:
            self._diagnostics["live_sweeps_detected"] += len(swept_pools)
        if not self._candles_by_timeframe["1m"]:
            return
        context = self._build_ict_strategy_context(event, swept_pools, session)
        selection = self._strategy_selector.evaluate(context)
        self._latest_strategy_selection = selection.diagnostics
        self._diagnostics["ict_strategy_evaluations"] += selection.diagnostics["ict_selector_evaluated"]

        if not selection.selected:
            self._diagnostics["ict_strategy_blocks"] += 1
            self._latest_confirmation_reasons = [
                item["reason"]
                for item in selection.diagnostics.get("ict_selector_rejections", [])
                if item.get("reason")
            ][:5]
            return

        self._diagnostics["reversal_candidates_detected"] += 1
        self._diagnostics["ict_strategy_selected"] += 1
        await self._accept_ict_strategy_selection(selection.selected, event, "1m")

    async def on_candle_evacuation(self, event: CandleNode) -> None:
        """Stores closed bars and converts real 1m structure into liquidity pools."""
        self._candles_by_timeframe[event.timeframe].append(event)
        if event.timeframe != "1m":
            return
        new_pivots, _ = self._structure_engine.ingest_candle_close(event)
        for pivot in new_pivots:
            self._liquidity_engine.register_structural_pivot_pool(pivot)
        self._structural_pivots.extend(new_pivots)

    async def _accept_ict_strategy_selection(self, selection: Any, event: TickNode, timeframe: str) -> None:
        """Route a selected ICT/SMC strategy into the existing risk/execution pipeline."""
        direction = selection.direction
        cooldown_key = f"{selection.definition.key}_{direction.value if direction else 'UNKNOWN'}"
        current_time = event.timestamp.replace(tzinfo=None)
        if cooldown_key in self._cooldown_registry and current_time < self._cooldown_registry[cooldown_key]:
            self._diagnostics["cooldown_blocks"] += 1
            return

        if selection.normalized_score < 60.0:
            self._diagnostics["quality_blocks"] += 1
            return

        self._setup_counter += 1
        setup_id = f"STP_{selection.definition.key.upper()}_{self._setup_counter}_{int(current_time.timestamp())}"
        opportunity_node = self._strategy_selector.build_setup_node(
            selection,
            setup_id=setup_id,
            now=current_time,
            correlation_id=event.correlation_id or f"ICT_SELECTOR_{event.sequence_id}",
            timeframe=timeframe,
        )
        confirmation_snap = self._strategy_selector.build_confirmation_snapshot(selection, now=current_time)

        self._discovered_setups[setup_id] = opportunity_node
        self._cooldown_registry[cooldown_key] = current_time + timedelta(minutes=15)
        self._qualified_candidates.append((opportunity_node, confirmation_snap))
        self._diagnostics["setup_nodes_finalized"] += 1
        self._latest_confirmation_reasons = []

        logger.info(
            "setup_orchestrator.ict_signal_finalized",
            id=setup_id,
            strategy=selection.definition.key,
            tier=opportunity_node.quality_tier.value,
            rr=f"{opportunity_node.estimated_rr:.2f}",
            score=f"{opportunity_node.confidence_score:.2f}",
        )

    def _build_ict_strategy_context(
        self,
        event: TickNode,
        swept_pools: List[Tuple[Any, float]],
        session: str,
    ) -> Dict[str, Any]:
        """Adapt live engine state into the strategy-library context contract."""
        candles_by_tf = {
            timeframe: [self._candle_payload(candle, idx) for idx, candle in enumerate(candles)]
            for timeframe, candles in self._candles_by_timeframe.items()
        }
        candles_1m = candles_by_tf.get("1m", [])
        session_value = str(getattr(session, "value", session))
        active_pools = self._liquidity_engine.active_pools_snapshot()
        swept_payloads = [self._swept_pool_payload(pool, depth) for pool, depth in swept_pools]
        liquidity_pools = active_pools + swept_payloads
        swings = [self._swing_payload(pivot, idx) for idx, pivot in enumerate(self._structural_pivots[-80:])]
        bias = self.directional_bias_matrix
        latest_sweep = swept_payloads[-1] if swept_payloads else None
        target_liquidity = self._select_target_liquidity(event.mid, liquidity_pools, latest_sweep)

        return {
            "symbol": event.symbol,
            "timestamp": event.timestamp,
            "current_price": event.mid,
            "current_bid": event.bid,
            "current_ask": event.ask,
            "candles": candles_1m,
            "setup_df": candles_1m,
            "entry_df": candles_1m,
            "ltf_df": candles_1m,
            "m15_df": candles_by_tf.get("15m", candles_1m),
            "htf_df": candles_by_tf.get("1h", candles_by_tf.get("4h", candles_1m)),
            "candles_by_timeframe": candles_by_tf,
            "liquidity_pools": liquidity_pools,
            "ltf_liquidity_pools": liquidity_pools,
            "htf_liquidity_targets": liquidity_pools,
            "target_liquidity": target_liquidity,
            "latest_sweep_event": latest_sweep,
            "starting_liquidity_event": latest_sweep,
            "swings": swings,
            "structure_swings": swings,
            "ltf_swings": swings,
            "htf_bias": {
                "bias_direction": self._bias_to_strategy_text(bias.get("1h") or bias.get("4h")),
                "timeframe_bias": bias,
                "confidence_score": 7.5,
            },
            "higher_timeframe_bias": self._bias_to_strategy_text(bias.get("4h") or bias.get("1h")),
            "session_context": {"session": session_value, "killzone_active": self._state_manager.snapshot.session.killzone_active},
            "session": session_value,
            "spread_status": {
                "spread_points": event.spread,
                "spread": event.spread,
                "average_spread": max(event.spread, 0.01),
            },
            "news_status": {"restricted": False, "high_impact_recent": False, "post_news_window_active": False},
            "price_location": self._price_location(event.mid, candles_1m),
        }

    @staticmethod
    def _candle_payload(candle: CandleNode, index: int) -> Dict[str, Any]:
        return {
            "symbol": candle.symbol,
            "timeframe": candle.timeframe,
            "timestamp": candle.end_time,
            "time": candle.end_time,
            "index": index,
            "position": index,
            "open": candle.open_p,
            "high": candle.high_p,
            "low": candle.low_p,
            "close": candle.close_p,
            "volume": candle.volume,
            "tick_volume": candle.ticks_count,
            "is_closed": candle.is_closed,
        }

    @staticmethod
    def _swing_payload(pivot: Any, index: int) -> Dict[str, Any]:
        is_high = "HIGH" in str(getattr(pivot, "point_type", "")).upper()
        return {
            "swing_id": getattr(pivot, "id", f"SWING_{index}"),
            "id": getattr(pivot, "id", f"SWING_{index}"),
            "kind": "high" if is_high else "low",
            "type": "high" if is_high else "low",
            "price": float(getattr(pivot, "price", 0.0)),
            "timestamp": getattr(pivot, "timestamp", None),
            "index": index,
            "position": index,
            "timeframe": getattr(pivot, "timeframe", "1m"),
            "confidence_score": float(getattr(pivot, "confidence", 7.0) or 7.0),
        }

    @staticmethod
    def _swept_pool_payload(pool: Any, depth: float) -> Dict[str, Any]:
        side = "buy_side" if pool.is_buy_side else "sell_side"
        reversal_direction = "bearish" if pool.is_buy_side else "bullish"
        return {
            "id": pool.id,
            "liquidity_id": pool.id,
            "swept_liquidity_id": pool.id,
            "timeframe": pool.timeframe,
            "side": side,
            "direction": side,
            "price": pool.ceiling_price if pool.is_buy_side else pool.floor_price,
            "zone_low": pool.floor_price,
            "zone_high": pool.ceiling_price,
            "quality_score": min(10.0, 6.0 + pool.accumulated_touches),
            "target_priority_score": min(10.0, 6.0 + pool.accumulated_touches),
            "touches": pool.accumulated_touches,
            "is_equal_structure": pool.is_equal_structure,
            "swept_status": "swept_rejected",
            "swept": True,
            "sweep_depth": depth,
            "sweep_timestamp": pool.sweep_timestamp,
            "direction_bias": reversal_direction,
            "direction_candidate": reversal_direction,
            "reclaim_status": "swept_rejected",
            "sweep_low": pool.floor_price,
            "sweep_high": pool.ceiling_price,
        }

    @staticmethod
    def _bias_to_strategy_text(value: Any) -> str:
        text = str(value or "").upper()
        if "BULL" in text:
            return "bullish"
        if "BEAR" in text:
            return "bearish"
        return "neutral"

    @staticmethod
    def _price_location(price: float, candles: List[Dict[str, Any]]) -> str:
        if not candles:
            return "unknown"
        recent = candles[-30:]
        high = max(float(candle["high"]) for candle in recent)
        low = min(float(candle["low"]) for candle in recent)
        if high <= low:
            return "equilibrium"
        position = (price - low) / (high - low)
        if position >= 0.65:
            return "premium"
        if position <= 0.35:
            return "discount"
        return "equilibrium"

    @staticmethod
    def _select_target_liquidity(
        price: float,
        pools: List[Dict[str, Any]],
        latest_sweep: Dict[str, Any] | None,
    ) -> Dict[str, Any] | None:
        if not pools:
            return None
        if latest_sweep and latest_sweep.get("side") == "sell_side":
            candidates = [pool for pool in pools if pool.get("side") == "buy_side" and float(pool.get("zone_high", price)) > price]
            return min(candidates, key=lambda pool: abs(float(pool.get("zone_high", price)) - price), default=None)
        if latest_sweep and latest_sweep.get("side") == "buy_side":
            candidates = [pool for pool in pools if pool.get("side") == "sell_side" and float(pool.get("zone_low", price)) < price]
            return min(candidates, key=lambda pool: abs(float(pool.get("zone_low", price)) - price), default=None)
        return min(pools, key=lambda pool: abs(float(pool.get("price", price)) - price), default=None)

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
            "latest_ict_strategy_selection": dict(self._latest_strategy_selection),
        }
