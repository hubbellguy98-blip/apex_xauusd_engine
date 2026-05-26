"""Run a real-data liquidity-sweep strategy in shadow mode or for one demo order."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.liquidity_engine import LiquidityInterceptionEngine
from src.analytics.session_engine import GoldSessionIntelligenceEngine
from src.analytics.structure_engine import DeterministicStructureEngine
from src.core.domain.constants import OrderDirection
from src.core.domain.execution_models import OrderRequest, OrderStatus
from src.core.domain.market_data import TickNode
from src.core.domain.setup_models import SetupOpportunityNode, SetupQualityTier, SetupType
from src.core.events.event_bus import EventBus
from src.execution.risk_firewall import RiskManagementOrchestrator
from src.infrastructure.broker.mt5_config import load_mt5_config
from src.infrastructure.broker.mt5_gateway import MT5BrokerGateway
from src.strategy.confirmation_core import TradeConfirmationOrchestrator
from src.strategy.reversal_detectors import LiquiditySweepReversalDetector
from src.strategy.scoring_matrix import TradeScoringOrchestrator
from src.strategy.setup_quality import InstitutionalSetupQualityClassifier
from src.strategy.state_manager import CentralRuntimeStateManager

EXECUTION_CONFIRMATION = "ENABLE_ONE_INTELLIGENT_DEMO_TRADE"
MAXIMUM_VOLUME = 0.01


def derive_closed_candle_bias(candles) -> str:
    """Derive a transparent directional bias using completed broker bars."""
    if len(candles) < 2:
        return "NEUTRAL"
    change = candles[-1].close_p - candles[0].close_p
    if change > 0:
        return "BULLISH"
    if change < 0:
        return "BEARISH"
    return "NEUTRAL"


def build_setup(direction: OrderDirection, entry: float, stop_loss: float, take_profit: float) -> SetupOpportunityNode:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    risk = abs(entry - stop_loss)
    reward = abs(take_profit - entry)
    return SetupOpportunityNode(
        id=f"INTELLIGENT_SWEEP_{uuid4().hex[:12]}",
        setup_type=SetupType.LIQUIDITY_SWEEP_REVERSAL,
        direction=direction,
        entry_price=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        estimated_rr=reward / risk if risk else 0.0,
        quality_tier=SetupQualityTier.STANDARD,
        confidence_score=0.0,
        creation_time=now,
        expiration_time=now + timedelta(minutes=15),
        correlation_id="MT5_REAL_DATA_LIQUIDITY_SWEEP",
        timeframe="1m",
    )


async def run_strategy(
    duration_seconds: float,
    poll_seconds: float,
    warmup_bars: int,
    execute_one_demo_trade: bool,
) -> int:
    configured = load_mt5_config(ROOT / ".env")
    if not configured.dry_run:
        raise RuntimeError("Keep APEX_MT5_DRY_RUN=true; execution is allowed only for one confirmed invocation.")
    if not configured.require_demo:
        raise RuntimeError("Refusing intelligent demo runner because APEX_MT5_REQUIRE_DEMO is not true.")
    if configured.max_lot <= 0 or configured.max_lot > MAXIMUM_VOLUME:
        raise RuntimeError("Refusing intelligent demo runner because APEX_MAX_LOT must be 0.01 or lower.")
    execution_volume_cap = min(configured.max_lot, MAXIMUM_VOLUME)

    gateway_config = (
        replace(configured, dry_run=False, max_lot=execution_volume_cap)
        if execute_one_demo_trade
        else configured
    )
    gateway = MT5BrokerGateway(gateway_config)
    state_manager = CentralRuntimeStateManager()
    event_bus = EventBus()
    confirmation = TradeConfirmationOrchestrator(event_bus, state_manager)
    scoring = TradeScoringOrchestrator(event_bus, state_manager)
    risk = RiskManagementOrchestrator(event_bus, state_manager, maximum_lots=execution_volume_cap)
    quality = InstitutionalSetupQualityClassifier()
    structure = DeterministicStructureEngine("1m")
    liquidity = LiquidityInterceptionEngine("1m")
    reversal = LiquiditySweepReversalDetector()
    sessions = GoldSessionIntelligenceEngine()

    await state_manager.bootstrap()
    await gateway.connect()
    try:
        symbol = str(gateway.connection_summary()["symbol"])
        existing_positions = await gateway.query_live_positions()
        candles_1m = gateway.read_recent_closed_candles(1, warmup_bars)
        bias = {
            "1m": derive_closed_candle_bias(candles_1m[-10:]),
            "15m": derive_closed_candle_bias(gateway.read_recent_closed_candles(15, 10)),
            "1h": derive_closed_candle_bias(gateway.read_recent_closed_candles(60, 10)),
            "4h": derive_closed_candle_bias(gateway.read_recent_closed_candles(240, 10)),
        }
        pivots = []
        historical_sweeps = 0
        for candle in candles_1m:
            await confirmation.on_candle_evacuation(candle)
            close_tick = TickNode(symbol=candle.symbol, timestamp=candle.end_time, bid=candle.close_p, ask=candle.close_p)
            historical_sweeps += len(liquidity.evaluate_tick_sweeps(close_tick))
            new_pivots, _ = structure.ingest_candle_close(candle)
            for pivot in new_pivots:
                liquidity.register_structural_pivot_pool(pivot)
            pivots.extend(new_pivots)

        print("MT5 INTELLIGENT DEMO RUNNER")
        print(f"mode={'ONE_DEMO_EXECUTION' if execute_one_demo_trade else 'SHADOW_ONLY_NO_ORDER'}")
        print(f"symbol={symbol}")
        print(f"open_gold_positions_at_start={len(existing_positions)}")
        print(f"historical_bars_processed={len(candles_1m)}")
        print(f"structure_pivots={len(pivots)}")
        print(f"historical_close_sweeps={historical_sweeps}")
        print(f"bias_1m={bias['1m']}; bias_15m={bias['15m']}; bias_1h={bias['1h']}; bias_4h={bias['4h']}")
        if execute_one_demo_trade and existing_positions:
            print("status=BLOCKED_EXISTING_GOLD_POSITION")
            print("No new order sent.")
            return 2

        previous_signature = None
        live_quotes = 0
        live_sweeps = 0
        candidates = 0
        confirmations = 0
        qualified = 0
        latest_rejection = "NO_LIVE_SWEEP_CANDIDATE"
        started = asyncio.get_running_loop().time()
        while asyncio.get_running_loop().time() - started < duration_seconds:
            tick = gateway.read_current_tick()
            signature = (tick.timestamp, tick.bid, tick.ask, tick.volume)
            if signature == previous_signature:
                await asyncio.sleep(poll_seconds)
                continue
            previous_signature = signature
            live_quotes += 1
            session, _, _ = sessions.evaluate_temporal_context(tick.timestamp, tick.mid)
            await state_manager.commit_market_update(
                {
                    "last_tick_time": tick.timestamp.replace(tzinfo=None),
                    "current_ask": tick.ask,
                    "current_bid": tick.bid,
                    "current_mid": tick.mid,
                    "current_spread": tick.spread,
                    "accumulated_tick_count": live_quotes,
                    "is_synchronized": True,
                },
                f"INTELLIGENT_TICK_{live_quotes}",
            )
            await state_manager.commit_session_update(
                {"current_phase": session, "last_phase_transition": tick.timestamp.replace(tzinfo=None)},
                f"INTELLIGENT_SESSION_{live_quotes}",
            )

            swept = liquidity.evaluate_tick_sweeps(tick)
            live_sweeps += len(swept)
            if not swept:
                await asyncio.sleep(poll_seconds)
                continue

            detected, direction, entry, stop_loss, take_profit = reversal.evaluate_sweep_reversal(
                tick, [pool for pool, _ in swept], pivots, state_manager.snapshot
            )
            if not detected:
                await asyncio.sleep(poll_seconds)
                continue
            candidates += 1
            confirmed, confirmation_snapshot = await confirmation.process_candidate_setup(
                direction,
                tick.timestamp.replace(tzinfo=None),
                candles_1m[-1],
                bias,
                1.0 / poll_seconds,
            )
            if not confirmed:
                latest_rejection = ",".join(confirmation_snapshot.invalidation_reasons)
                await asyncio.sleep(poll_seconds)
                continue
            confirmations += 1

            setup = build_setup(direction, entry, stop_loss, take_profit)
            tier, quality_score = quality.classify_setup_quality(
                setup.setup_type, setup.estimated_rr, state_manager.snapshot, confirmation_snapshot
            )
            setup = replace(setup, quality_tier=tier, confidence_score=quality_score)
            if tier == SetupQualityTier.INVALID_SETUP:
                latest_rejection = "SETUP_QUALITY_INVALID"
                await asyncio.sleep(poll_seconds)
                continue
            ranked = await scoring.process_and_rank_setup(setup, confirmation_snapshot)
            approved, risk_snapshot = await risk.evaluate_trade_entry_gate(
                setup, confirmation_snapshot, ranked.execution_multiplier
            )
            if not ranked.is_live_executable or not approved:
                latest_rejection = ",".join(ranked.rejection_payload + risk_snapshot.rejection_reasons)
                await asyncio.sleep(poll_seconds)
                continue
            qualified += 1
            print(f"qualified_direction={direction.value}")
            print(f"qualified_score={ranked.score_breakdown.normalized_final_score:.2f}")
            print(f"qualified_lots={risk_snapshot.sizing.calculated_lots:.2f}")
            if not execute_one_demo_trade:
                print("status=QUALIFIED_SHADOW_SIGNAL_NO_ORDER_SENT")
                return 0
            if await gateway.query_live_positions():
                print("status=BLOCKED_POSITION_OPENED_DURING_MONITOR")
                return 2

            token = uuid4().hex[:12]
            report = await gateway.route_order_submission(
                OrderRequest(
                    client_order_id=f"INTELLIGENT_DEMO_{token}",
                    symbol=symbol,
                    direction=direction,
                    quantity_lots=risk_snapshot.sizing.calculated_lots,
                    entry_price=entry,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    idempotency_key=f"INTELLIGENT_DEMO_ONCE_{token}",
                    timestamp=datetime.now(timezone.utc),
                )
            )
            print(f"status={report.status.value}")
            print(f"broker_order_id={report.broker_order_id}")
            print(f"filled_quantity={report.filled_quantity:.2f}")
            print(f"rejection_reason={report.rejection_reason}")
            return 0 if report.status == OrderStatus.FILLED else 1

        print(f"live_quotes_processed={live_quotes}")
        print(f"live_sweeps={live_sweeps}")
        print(f"candidates={candidates}")
        print(f"confirmed_candidates={confirmations}")
        print(f"qualified_candidates={qualified}")
        print(f"status=NO_QUALIFIED_SIGNAL_BEFORE_TIMEOUT; reason={latest_rejection}")
        print("No order sent.")
        return 0
    finally:
        await gateway.disconnect()
        await state_manager.terminate()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Use real MT5 structure and liquidity signals for demo trading.")
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--warmup-bars", type=int, default=50)
    parser.add_argument("--execute-one-demo", action="store_true")
    parser.add_argument("--confirm-execution")
    args = parser.parse_args()
    if args.duration_seconds <= 0 or args.poll_seconds <= 0 or args.warmup_bars <= 0:
        parser.error("duration, poll interval, and warmup bars must be positive.")
    if args.execute_one_demo and args.confirm_execution != EXECUTION_CONFIRMATION:
        parser.error(f"--confirm-execution must be {EXECUTION_CONFIRMATION} when execution is requested.")
    return args


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(
        asyncio.run(
            run_strategy(
                arguments.duration_seconds,
                arguments.poll_seconds,
                arguments.warmup_bars,
                arguments.execute_one_demo,
            )
        )
    )
