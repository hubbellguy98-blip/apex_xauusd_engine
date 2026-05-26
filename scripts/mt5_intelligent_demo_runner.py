"""Drive the core Apex strategy pipeline from live MT5 demo-market data."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.domain.execution_models import OrderRequest, OrderStatus
from src.core.events.event_bus import EventBus
from src.execution.risk_firewall import RiskManagementOrchestrator
from src.infrastructure.broker.mt5_config import load_mt5_config
from src.infrastructure.broker.mt5_gateway import MT5BrokerGateway
from src.strategy.confirmation_orchestrator import TradeConfirmationOrchestrator
from src.strategy.scoring_matrix import TradeScoringOrchestrator
from src.strategy.setup_detector import MarketSetupOrchestrator
from src.strategy.state_manager import CentralRuntimeStateManager

EXECUTION_CONFIRMATION = "ENABLE_ONE_INTELLIGENT_DEMO_TRADE"
MAXIMUM_VOLUME = 0.01


async def synchronize_positions(state_manager: CentralRuntimeStateManager, gateway: MT5BrokerGateway) -> list:
    """Reflect the broker's current Gold exposure in central strategy state."""
    positions = await gateway.query_live_positions()
    await state_manager.commit_position_update(
        {
            "net_exposure_lots": sum(item.net_quantity_lots for item in positions),
            "floating_pnl_pips": sum(item.floating_pnl_pips for item in positions),
            "active_position_count": len(positions),
        },
        "MT5_POSITION_SYNC",
    )
    return positions


async def run_strategy(
    duration_seconds: float,
    poll_seconds: float,
    warmup_bars: int,
    execute_one_demo_trade: bool,
) -> int:
    configured = load_mt5_config(ROOT / ".env")
    if not configured.dry_run:
        raise RuntimeError("Keep APEX_MT5_DRY_RUN=true; live sending requires one explicit demo invocation.")
    if not configured.require_demo:
        raise RuntimeError("Refusing strategy run because APEX_MT5_REQUIRE_DEMO is not true.")
    if configured.max_lot <= 0 or configured.max_lot > MAXIMUM_VOLUME:
        raise RuntimeError("Refusing strategy run because APEX_MAX_LOT must be 0.01 or lower.")

    volume_cap = min(configured.max_lot, MAXIMUM_VOLUME)
    gateway_config = replace(configured, dry_run=False, max_lot=volume_cap) if execute_one_demo_trade else configured
    gateway = MT5BrokerGateway(gateway_config)
    event_bus = EventBus()
    state_manager = CentralRuntimeStateManager()
    confirmation = TradeConfirmationOrchestrator(event_bus, state_manager)
    detector = MarketSetupOrchestrator(event_bus, state_manager, confirmation)
    scoring = TradeScoringOrchestrator(event_bus, state_manager)
    risk = RiskManagementOrchestrator(event_bus, state_manager, maximum_lots=volume_cap)

    await state_manager.bootstrap()
    await gateway.connect()
    try:
        symbol = str(gateway.connection_summary()["symbol"])
        existing_positions = await synchronize_positions(state_manager, gateway)

        histories = {
            "1m": gateway.read_recent_closed_candles(1, warmup_bars),
            "15m": gateway.read_recent_closed_candles(15, 10),
            "1h": gateway.read_recent_closed_candles(60, 10),
            "4h": gateway.read_recent_closed_candles(240, 10),
        }
        for timeframe, candles in histories.items():
            for candle in candles:
                await detector.seed_closed_candle(candle)
                if timeframe == "1m":
                    await confirmation.on_candle_evacuation(candle)

        bias = detector.directional_bias_matrix
        print("MT5 CORE STRATEGY RUNNER")
        print(f"mode={'ONE_DEMO_EXECUTION' if execute_one_demo_trade else 'SHADOW_ONLY_NO_ORDER'}")
        print(f"symbol={symbol}")
        print(f"open_gold_positions_at_start={len(existing_positions)}")
        print(f"historical_bars_processed={len(histories['1m'])}")
        print(f"structure_pivots={detector.tracked_structural_pivots}")
        print(f"historical_sweeps_cleared={detector.warmup_sweeps_cleared}")
        print(f"bias_1m={bias['1m']}; bias_15m={bias['15m']}; bias_1h={bias['1h']}; bias_4h={bias['4h']}")
        if execute_one_demo_trade and existing_positions:
            print("status=BLOCKED_EXISTING_GOLD_POSITION")
            print("No new order sent.")
            return 2

        previous_signature = None
        live_quotes = 0
        qualified = 0
        started = asyncio.get_running_loop().time()
        while asyncio.get_running_loop().time() - started < duration_seconds:
            tick = gateway.read_current_tick()
            signature = (tick.timestamp, tick.bid, tick.ask, tick.volume)
            if signature == previous_signature:
                await asyncio.sleep(poll_seconds)
                continue
            previous_signature = signature
            live_quotes += 1
            await detector.on_tick_received(tick)

            for setup, confirmation_snapshot in detector.drain_qualified_candidates():
                ranked = await scoring.process_and_rank_setup(setup, confirmation_snapshot)
                approved, risk_snapshot = await risk.evaluate_trade_entry_gate(
                    setup, confirmation_snapshot, ranked.execution_multiplier
                )
                if not ranked.is_live_executable or not approved:
                    reasons = ranked.rejection_payload + risk_snapshot.rejection_reasons
                    print(f"candidate_status=BLOCKED_BY_SCORING_OR_RISK; reason={','.join(reasons)}")
                    continue
                qualified += 1
                print(f"qualified_direction={setup.direction.value}")
                print(f"qualified_score={ranked.score_breakdown.normalized_final_score:.2f}")
                print(f"qualified_lots={risk_snapshot.sizing.calculated_lots:.2f}")
                if not execute_one_demo_trade:
                    print("status=QUALIFIED_SHADOW_SIGNAL_NO_ORDER_SENT")
                    return 0

                if await synchronize_positions(state_manager, gateway):
                    print("status=BLOCKED_POSITION_OPENED_DURING_MONITOR")
                    print("No new order sent.")
                    return 2

                token = uuid4().hex[:12]
                report = await gateway.route_order_submission(
                    OrderRequest(
                        client_order_id=f"CORE_DEMO_{token}",
                        symbol=symbol,
                        direction=setup.direction,
                        quantity_lots=risk_snapshot.sizing.calculated_lots,
                        entry_price=setup.entry_price,
                        stop_loss=setup.stop_loss,
                        take_profit=setup.take_profit,
                        idempotency_key=f"CORE_DEMO_ONCE_{token}",
                        timestamp=datetime.now(timezone.utc),
                    )
                )
                print(f"status={report.status.value}")
                print(f"broker_order_id={report.broker_order_id}")
                print(f"filled_quantity={report.filled_quantity:.2f}")
                print(f"rejection_reason={report.rejection_reason}")
                return 0 if report.status == OrderStatus.FILLED else 1

            await asyncio.sleep(poll_seconds)

        print(f"live_quotes_processed={live_quotes}")
        print(f"qualified_candidates={qualified}")
        diagnostics = detector.diagnostic_snapshot
        print(f"live_sweeps_detected={diagnostics['live_sweeps_detected']}")
        print(f"reversal_candidates_detected={diagnostics['reversal_candidates_detected']}")
        print(f"confirmation_blocks={diagnostics['confirmation_blocks']}")
        print(f"quality_blocks={diagnostics['quality_blocks']}")
        print(f"cooldown_blocks={diagnostics['cooldown_blocks']}")
        nearest_pool = diagnostics["nearest_active_pool"]
        if nearest_pool:
            print(f"active_liquidity_pools={nearest_pool['active_pool_count']}")
            print(
                f"nearest_liquidity_level={nearest_pool['side']} "
                f"price={nearest_pool['level_price']:.2f} "
                f"distance={nearest_pool['distance']:.2f}"
            )
        else:
            print("active_liquidity_pools=0")
        if diagnostics["latest_confirmation_reasons"]:
            print(f"latest_confirmation_rejection={','.join(diagnostics['latest_confirmation_reasons'])}")
        print("status=NO_QUALIFIED_SIGNAL_BEFORE_TIMEOUT")
        print("No order sent.")
        return 0
    finally:
        await detector.terminate()
        await confirmation.terminate()
        await gateway.disconnect()
        await state_manager.terminate()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the core Apex strategy from real MT5 demo-market data.")
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
