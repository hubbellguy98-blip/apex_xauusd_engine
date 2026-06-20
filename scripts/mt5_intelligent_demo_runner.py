"""Drive the core Apex strategy pipeline from live MT5 demo-market data."""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.domain.execution_models import OrderRequest, OrderStatus
from src.core.domain.constants import OrderDirection
from src.execution.rr_math import calculate_post_cost_rr, risk_to_cost_ratio
from src.core.events.event_bus import EventBus
from src.execution.position_tracker import (
    InstitutionalTradeLifecycleManager,
    ManagedTradePlan,
    ManagedTradePlanReconciler,
    ManagedTradePlanStore,
)
from src.execution.position_sizer import InstitutionalPositionSizer
from src.execution.pre_submission_guard import LiveQuoteActivityMonitor
from src.execution.risk_firewall import RiskManagementOrchestrator
from src.execution.stop_loss_engine import DynamicStructuralStopEngine
from src.infrastructure.broker.mt5_config import load_mt5_config
from src.infrastructure.broker.mt5_gateway import MT5BrokerGateway
from src.infrastructure.telemetry.telegram_reporting import TelegramReportingService
from src.strategy.confirmation_orchestrator import TradeConfirmationOrchestrator
from src.strategy.scoring_matrix import TradeScoringOrchestrator
from src.strategy.setup_detector import MarketSetupOrchestrator
from src.strategy.selector_profile import DEFAULT_SHADOW_PROFILE, load_selector_profile, normalize_selector_profile
from src.strategy.state_manager import CentralRuntimeStateManager

EXECUTION_CONFIRMATION = "ENABLE_ONE_INTELLIGENT_DEMO_TRADE"
MANAGEMENT_CONFIRMATION = "ENABLE_BUFFERED_DEMO_TRAILING"
MAXIMUM_VOLUME = 0.05
MAXIMUM_ENTRY_SPREAD_PRICE = 0.35
MAXIMUM_LIVE_QUOTE_INACTIVITY_SECONDS = 5.0
MANAGED_PLAN_PATH = ROOT / ".apex_runtime" / "managed_gold_trade.json"


def resolve_runner_mode(execute_one_demo_trade: bool, manage_open_demo_trade: bool) -> str:
    if execute_one_demo_trade and manage_open_demo_trade:
        return "ONE_DEMO_EXECUTION_AND_MANAGEMENT"
    if manage_open_demo_trade:
        return "DEMO_POSITION_MANAGEMENT"
    if execute_one_demo_trade:
        return "ONE_DEMO_EXECUTION"
    return "SHADOW_ONLY_NO_ORDER"


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


async def connect_with_retry(gateway: MT5BrokerGateway, attempts: int = 3, retry_delay_seconds: float = 2.0) -> int:
    """Retry temporary MT5 terminal IPC timeouts during startup."""
    retries = 0
    for attempt in range(attempts):
        try:
            await gateway.connect()
            return retries
        except RuntimeError as exc:
            if "IPC timeout" not in str(exc) or attempt == attempts - 1:
                raise
            retries += 1
            await gateway.disconnect()
            await asyncio.sleep(retry_delay_seconds)
    return retries


async def refresh_closed_candles(
    gateway: MT5BrokerGateway,
    detector: MarketSetupOrchestrator,
    confirmation: TradeConfirmationOrchestrator,
    latest_candle_end_by_timeframe: dict[str, datetime],
) -> tuple[int, list]:
    """Feed each newly completed MT5 candle once into the live strategy state."""
    requested_timeframes = {"1m": 1, "15m": 15, "1h": 60, "4h": 240}
    newly_ingested = 0
    newly_closed_1m = []
    for timeframe, timeframe_minutes in requested_timeframes.items():
        candles = gateway.read_recent_closed_candles(timeframe_minutes, 5)
        previous_end = latest_candle_end_by_timeframe[timeframe]
        for candle in candles:
            if candle.end_time <= previous_end:
                continue
            await detector.on_candle_evacuation(candle)
            if timeframe == "1m":
                await confirmation.on_candle_evacuation(candle)
                newly_closed_1m.append(candle)
            latest_candle_end_by_timeframe[timeframe] = candle.end_time
            newly_ingested += 1
    return newly_ingested, newly_closed_1m


async def manage_protected_position(
    gateway: MT5BrokerGateway,
    plan_store: ManagedTradePlanStore,
    lifecycle: InstitutionalTradeLifecycleManager,
    plan: ManagedTradePlan,
    newly_closed_1m: list,
    apply_updates: bool,
) -> tuple[ManagedTradePlan | None, int]:
    """Evaluate or apply buffered stop advances for the single recorded trade."""
    positions = await gateway.query_live_positions()
    position = next((item for item in positions if item.ticket == plan.ticket), None)
    if position is None:
        plan_store.clear()
        print("protection_status=MANAGED_POSITION_CLOSED_PLAN_CLEARED")
        return None, 0

    updates = 0
    observation_milestone = plan.last_confirmed_milestone
    for candle in newly_closed_1m:
        decision = lifecycle.evaluate_candle_confirmed_trail(
            plan.direction,
            plan.entry,
            plan.initial_stop_loss,
            position.stop_loss,
            plan.final_take_profit,
            candle,
            observation_milestone,
        )
        observation_milestone = max(observation_milestone, decision.confirmed_milestone)
        if not decision.should_modify or decision.stop_loss is None:
            continue
        print(
            f"trailing_proposal=TP{decision.protected_milestone}_BUFFERED "
            f"confirmed_milestone=TP{decision.confirmed_milestone} "
            f"new_stop_loss={decision.stop_loss:.2f}"
        )
        if not apply_updates:
            print("trailing_status=SHADOW_ONLY_NO_STOP_MODIFICATION")
            continue
        report = await gateway.route_position_stop_update(position, decision.stop_loss)
        print(f"trailing_status={'APPLIED' if report.applied else 'REJECTED'}")
        print(f"trailing_rejection_reason={report.rejection_reason}")
        if report.applied:
            updates += 1
            plan = replace(plan, last_confirmed_milestone=decision.confirmed_milestone)
            plan_store.save(plan)
            position = replace(position, stop_loss=decision.stop_loss)
    return plan, updates


async def run_strategy(
    duration_seconds: float,
    poll_seconds: float,
    warmup_bars: int,
    execute_one_demo_trade: bool,
    manage_open_demo_trade: bool,
    profile_name: str | None = None,
) -> int:
    configured = load_mt5_config(ROOT / ".env")
    mode = resolve_runner_mode(execute_one_demo_trade, manage_open_demo_trade)
    selected_profile_name = profile_name or os.getenv("APEX_SELECTOR_PROFILE") or DEFAULT_SHADOW_PROFILE
    selected_profile = load_selector_profile(selected_profile_name)
    selector_config = normalize_selector_profile(selected_profile)
    try:
        reporting = TelegramReportingService.from_env_file(ROOT / ".env", ROOT)
    except RuntimeError as exc:
        print(f"telegram_reporting_status=DISABLED_CONFIG_ERROR reason={exc}")
        reporting = TelegramReportingService.disabled(ROOT)
        reporting.record("TELEGRAM_CONFIG_ERROR", "WARNING", reason=str(exc), mode=mode)
    if not configured.dry_run:
        raise RuntimeError("Keep APEX_MT5_DRY_RUN=true; live sending requires one explicit demo invocation.")
    if not configured.require_demo:
        raise RuntimeError("Refusing strategy run because APEX_MT5_REQUIRE_DEMO is not true.")
    if configured.max_lot <= 0 or configured.max_lot > MAXIMUM_VOLUME:
        raise RuntimeError("Refusing strategy run because APEX_MAX_LOT must be 0.05 or lower.")

    volume_cap = min(configured.max_lot, MAXIMUM_VOLUME)
    gateway_config = (
        replace(configured, dry_run=False, max_lot=volume_cap)
        if execute_one_demo_trade or manage_open_demo_trade
        else configured
    )
    gateway = MT5BrokerGateway(gateway_config)
    event_bus = EventBus()
    state_manager = CentralRuntimeStateManager()
    confirmation = TradeConfirmationOrchestrator(event_bus, state_manager)
    detector = MarketSetupOrchestrator(
        event_bus,
        state_manager,
        confirmation,
        selector_config=selector_config,
        profile_name=selected_profile_name,
    )
    scoring = TradeScoringOrchestrator(event_bus, state_manager)
    risk = None
    lifecycle = InstitutionalTradeLifecycleManager()
    stop_hardener = DynamicStructuralStopEngine()
    plan_reconciler = ManagedTradePlanReconciler()
    plan_store = ManagedTradePlanStore(MANAGED_PLAN_PATH)
    managed_plan = plan_store.load()
    startup_entry_block = False
    reconciliation_status = "NOT_RECONCILED"

    await reporting.record_and_notify(
        "RUN_STARTED",
        "INFO",
        notify=True,
        mode=mode,
        configured_symbol=configured.symbol,
        dry_run=configured.dry_run,
        require_demo=configured.require_demo,
        max_lot=configured.max_lot,
        duration_seconds=duration_seconds,
        poll_seconds=poll_seconds,
        warmup_bars=warmup_bars,
        selector_profile=selected_profile_name,
        selector_config_hash=detector.diagnostic_snapshot["selector_config_hash"],
    )

    await state_manager.bootstrap()
    try:
        connection_retries = await connect_with_retry(gateway)
        symbol = str(gateway.connection_summary()["symbol"])
        sizing_specification = gateway.read_sizing_specification()
        risk = RiskManagementOrchestrator(
            event_bus,
            state_manager,
            position_sizer=InstitutionalPositionSizer(
                account_equity=sizing_specification.account_equity,
                maximum_lots=min(volume_cap, sizing_specification.volume_max),
                minimum_lots=sizing_specification.volume_min,
                volume_step=sizing_specification.volume_step,
                loss_per_lot_calculator=lambda setup: gateway.calculate_stop_loss_currency_per_lot(
                    setup.direction,
                    setup.entry_price,
                    setup.stop_loss,
                ),
            ),
        )
        await risk.bootstrap()
        existing_positions = await synchronize_positions(state_manager, gateway)
        reconciliation = plan_reconciler.reconcile(managed_plan, existing_positions)
        if reconciliation.clear_stale_plan:
            plan_store.clear()
        managed_plan = reconciliation.active_plan
        startup_entry_block = reconciliation.blocks_new_entries
        reconciliation_status = reconciliation.status

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
        latest_candle_end_by_timeframe = {
            timeframe: candles[-1].end_time for timeframe, candles in histories.items()
        }
        recent_closed_1m = deque(histories["1m"], maxlen=50)

        bias = detector.directional_bias_matrix
        print("MT5 CORE STRATEGY RUNNER")
        print(f"mode={mode}")
        print(f"symbol={symbol}")
        print(f"risk_sizing_source=MT5_ACCOUNT_CURRENCY_{sizing_specification.account_currency}")
        print(
            f"broker_volume_min={sizing_specification.volume_min:.2f}; "
            f"broker_volume_step={sizing_specification.volume_step:.2f}; "
            f"configured_volume_cap={volume_cap:.2f}"
        )
        print(f"connection_retries={connection_retries}")
        print(f"open_gold_positions_at_start={len(existing_positions)}")
        print(f"startup_reconciliation={reconciliation_status}")
        print(f"selector_profile={selected_profile_name}")
        print(f"selector_config_hash={detector.diagnostic_snapshot['selector_config_hash']}")
        print(f"historical_bars_processed={len(histories['1m'])}")
        print(f"structure_pivots={detector.tracked_structural_pivots}")
        print(f"historical_sweeps_cleared={detector.warmup_sweeps_cleared}")
        print(f"bias_1m={bias['1m']}; bias_15m={bias['15m']}; bias_1h={bias['1h']}; bias_4h={bias['4h']}")
        await reporting.record_and_notify(
            "RUN_CONNECTED",
            "INFO",
            notify=False,
            mode=mode,
            symbol=symbol,
            risk_sizing_source=f"MT5_ACCOUNT_CURRENCY_{sizing_specification.account_currency}",
            broker_volume_min=sizing_specification.volume_min,
            broker_volume_step=sizing_specification.volume_step,
            configured_volume_cap=volume_cap,
            connection_retries=connection_retries,
            open_positions_at_start=len(existing_positions),
            startup_reconciliation=reconciliation_status,
            selector_profile=selected_profile_name,
            selector_config_hash=detector.diagnostic_snapshot["selector_config_hash"],
            historical_bars_processed=len(histories["1m"]),
            structure_pivots=detector.tracked_structural_pivots,
            historical_sweeps_cleared=detector.warmup_sweeps_cleared,
            bias_1m=bias["1m"],
            bias_15m=bias["15m"],
            bias_1h=bias["1h"],
            bias_4h=bias["4h"],
        )
        if existing_positions and managed_plan is None:
            print("protection_status=OPEN_POSITION_NOT_SAFELY_MATCHED_NO_AUTO_TRAILING")
        if execute_one_demo_trade and existing_positions:
            print("status=BLOCKED_EXISTING_GOLD_POSITION")
            print("No new order sent.")
            reporting.record(
                "RUN_BLOCKED",
                "WARNING",
                mode=mode,
                symbol=symbol,
                status="BLOCKED_EXISTING_GOLD_POSITION",
                open_positions=len(existing_positions),
            )
            if not manage_open_demo_trade:
                return 2

        previous_signature = None
        quote_activity = LiveQuoteActivityMonitor(MAXIMUM_LIVE_QUOTE_INACTIVITY_SECONDS)
        live_quotes = 0
        live_closed_candles = 0
        tick_read_failures = 0
        inactive_quote_reads = 0
        qualified = 0
        stop_updates = 0
        entry_submitted_this_run = False
        started = asyncio.get_running_loop().time()

        async def emit_run_summary(status: str, severity: str = "INFO") -> None:
            diagnostics = detector.diagnostic_snapshot
            nearest_pool = diagnostics["nearest_active_pool"]
            nearest_liquidity_level = (
                {
                    "side": nearest_pool["side"],
                    "price": nearest_pool["level_price"],
                    "distance": nearest_pool["distance"],
                    "active_pool_count": nearest_pool["active_pool_count"],
                }
                if nearest_pool and live_quotes > 0
                else None
            )
            latest_confirmation_rejection = (
                ",".join(diagnostics["latest_confirmation_reasons"])
                if diagnostics["latest_confirmation_reasons"]
                else None
            )
            summary = {
                "mode": mode,
                "symbol": symbol,
                "status": status,
                "dry_run": gateway_config.dry_run,
                "require_demo": gateway_config.require_demo,
                "max_lot": gateway_config.max_lot,
                "live_quotes_processed": live_quotes,
                "live_closed_candles_ingested": live_closed_candles,
                "temporary_tick_gaps": tick_read_failures,
                "quote_updates_confirming_live_feed": quote_activity.updates_observed,
                "inactive_quote_reads_discarded": inactive_quote_reads,
                "stop_updates_applied": stop_updates,
                "qualified_candidates": qualified,
                "live_sweeps_detected": diagnostics["live_sweeps_detected"],
                "reversal_candidates_detected": diagnostics["reversal_candidates_detected"],
                "confirmation_blocks": diagnostics["confirmation_blocks"],
                "quality_blocks": diagnostics["quality_blocks"],
                "cooldown_blocks": diagnostics["cooldown_blocks"],
                "nearest_liquidity_level": nearest_liquidity_level,
                "latest_confirmation_rejection": latest_confirmation_rejection,
            }
            reporting.record("RUN_SUMMARY", severity, **summary)
            await reporting.send_session_summary(summary)

        while asyncio.get_running_loop().time() - started < duration_seconds:
            try:
                tick = gateway.read_current_tick()
            except RuntimeError as exc:
                if "No tick available" not in str(exc):
                    raise
                tick_read_failures += 1
                await asyncio.sleep(poll_seconds)
                continue
            activity_snapshot = quote_activity.observe(tick)
            if not activity_snapshot.is_fresh:
                inactive_quote_reads += 1
                await asyncio.sleep(poll_seconds)
                continue
            signature = (tick.timestamp, tick.bid, tick.ask, tick.volume)
            if signature == previous_signature:
                await asyncio.sleep(poll_seconds)
                continue
            previous_signature = signature
            live_quotes += 1
            ingested_count, newly_closed_1m = await refresh_closed_candles(
                gateway,
                detector,
                confirmation,
                latest_candle_end_by_timeframe,
            )
            live_closed_candles += ingested_count
            recent_closed_1m.extend(newly_closed_1m)
            if managed_plan is not None and newly_closed_1m:
                managed_plan, applied_updates = await manage_protected_position(
                    gateway,
                    plan_store,
                    lifecycle,
                    managed_plan,
                    newly_closed_1m,
                    apply_updates=manage_open_demo_trade,
                )
                stop_updates += applied_updates
            await detector.on_tick_received(tick)

            for setup, confirmation_snapshot in detector.drain_qualified_candidates():
                if startup_entry_block or managed_plan is not None or entry_submitted_this_run:
                    print("candidate_status=BLOCKED_SINGLE_ACTIVE_OR_ALREADY_SUBMITTED_TRADE")
                    reporting.record(
                        "CANDIDATE_BLOCKED",
                        "INFO",
                        mode=mode,
                        symbol=symbol,
                        setup_id=setup.id,
                        direction=setup.direction.value,
                        reasons=["SINGLE_ACTIVE_OR_ALREADY_SUBMITTED_TRADE"],
                    )
                    continue
                ranked = await scoring.process_and_rank_setup(setup, confirmation_snapshot)
                approved, risk_snapshot = await risk.evaluate_trade_entry_gate(
                    setup, confirmation_snapshot, ranked.execution_multiplier
                )
                if not ranked.is_live_executable or not approved:
                    reasons = ranked.rejection_payload + risk_snapshot.rejection_reasons
                    print(f"candidate_status=BLOCKED_BY_SCORING_OR_RISK; reason={','.join(reasons)}")
                    reporting.record(
                        "CANDIDATE_BLOCKED",
                        "INFO",
                        mode=mode,
                        symbol=symbol,
                        setup_id=setup.id,
                        direction=setup.direction.value,
                        setup_type=setup.setup_type.value,
                        timeframe=setup.timeframe,
                        final_score=ranked.score_breakdown.normalized_final_score,
                        risk_approved=approved,
                        reasons=reasons,
                    )
                    continue
                hardening = stop_hardener.harden_for_demo_execution(setup, tuple(recent_closed_1m), tick.spread)
                if hardening.adjusted:
                    setup = hardening.setup
                    approved, risk_snapshot = await risk.evaluate_trade_entry_gate(
                        setup, confirmation_snapshot, ranked.execution_multiplier
                    )
                    print("execution_stop_hardening=APPLIED")
                    print(f"original_stop_distance={hardening.original_stop_distance:.2f}")
                    print(f"hardened_stop_distance={hardening.hardened_stop_distance:.2f}")
                    print(f"hardened_rr={hardening.hardened_rr:.2f}")
                    print(f"hardening_reasons={','.join(hardening.reasons)}")
                    reporting.record(
                        "EXECUTION_STOP_HARDENED",
                        "INFO",
                        mode=mode,
                        symbol=symbol,
                        setup_id=setup.id,
                        direction=setup.direction.value,
                        original_stop_distance=hardening.original_stop_distance,
                        hardened_stop_distance=hardening.hardened_stop_distance,
                        original_rr=hardening.original_rr,
                        hardened_rr=hardening.hardened_rr,
                        stop_loss=setup.stop_loss,
                        take_profit=setup.take_profit,
                        reasons=hardening.reasons,
                    )
                    if not approved:
                        print("candidate_status=BLOCKED_BY_RISK_AFTER_STOP_HARDENING")
                        reporting.record(
                            "CANDIDATE_BLOCKED",
                            "WARNING",
                            mode=mode,
                            symbol=symbol,
                            setup_id=setup.id,
                            direction=setup.direction.value,
                            reasons=["RISK_REJECTED_AFTER_STOP_HARDENING", *risk_snapshot.rejection_reasons],
                        )
                        continue
                expected_fill = tick.ask if setup.direction is OrderDirection.BUY else tick.bid
                expected_post_cost_rr = calculate_post_cost_rr(
                    setup.direction.value,
                    expected_fill,
                    setup.stop_loss,
                    setup.take_profit,
                    tick.spread,
                    float(selected_profile.get("slippage_price", 0.0)),
                )
                risk_cost_ratio = risk_to_cost_ratio(
                    expected_fill,
                    setup.stop_loss,
                    tick.spread,
                    float(selected_profile.get("slippage_price", 0.0)),
                )
                minimum_rr = float(selector_config.get("minimum_rr", selected_profile.get("minimum_rr", 3.0)))
                minimum_risk_cost = float(selector_config.get("minimum_risk_to_cost_ratio", 0.0))
                if risk_cost_ratio < minimum_risk_cost:
                    print("candidate_status=BLOCKED_RISK_TOO_SMALL_VS_COST")
                    reporting.record(
                        "CANDIDATE_BLOCKED",
                        "WARNING",
                        mode=mode,
                        symbol=symbol,
                        setup_id=setup.id,
                        direction=setup.direction.value,
                        estimated_post_cost_rr=expected_post_cost_rr,
                        risk_to_cost_ratio=risk_cost_ratio,
                        minimum_risk_to_cost_ratio=minimum_risk_cost,
                        reasons=["risk_too_small_vs_cost"],
                    )
                    continue
                if expected_post_cost_rr < minimum_rr:
                    print("candidate_status=BLOCKED_POST_COST_RR_BELOW_MINIMUM")
                    reporting.record(
                        "CANDIDATE_BLOCKED",
                        "WARNING",
                        mode=mode,
                        symbol=symbol,
                        setup_id=setup.id,
                        direction=setup.direction.value,
                        estimated_post_cost_rr=expected_post_cost_rr,
                        minimum_rr=minimum_rr,
                        reasons=["post_cost_rr_below_minimum"],
                    )
                    continue
                qualified += 1
                print(f"qualified_direction={setup.direction.value}")
                print(f"qualified_score={ranked.score_breakdown.normalized_final_score:.2f}")
                print(f"qualified_lots={risk_snapshot.sizing.calculated_lots:.2f}")
                print(f"estimated_post_cost_rr={expected_post_cost_rr:.2f}")
                await reporting.record_and_notify(
                    "RISK_APPROVED",
                    "INFO",
                    notify=not execute_one_demo_trade,
                    mode=mode,
                    symbol=symbol,
                    setup_id=setup.id,
                    direction=setup.direction.value,
                    setup_type=setup.setup_type.value,
                    timeframe=setup.timeframe,
                    entry_price=setup.entry_price,
                    stop_loss=setup.stop_loss,
                    take_profit=setup.take_profit,
                    estimated_rr=setup.estimated_rr,
                    estimated_post_cost_rr=expected_post_cost_rr,
                    risk_to_cost_ratio=risk_cost_ratio,
                    confidence_score=setup.confidence_score,
                    final_score=ranked.score_breakdown.normalized_final_score,
                    calculated_lots=risk_snapshot.sizing.calculated_lots,
                    currency_risk=risk_snapshot.sizing.currency_risk,
                    risk_pct=risk_snapshot.sizing.risk_percentage_applied,
                    status="QUALIFIED_SHADOW_SIGNAL_NO_ORDER_SENT"
                    if not execute_one_demo_trade
                    else "QUALIFIED_FOR_DEMO_EXECUTION",
                )
                if not execute_one_demo_trade:
                    print("status=QUALIFIED_SHADOW_SIGNAL_NO_ORDER_SENT")
                    await emit_run_summary("QUALIFIED_SHADOW_SIGNAL_NO_ORDER_SENT")
                    return 0

                if await synchronize_positions(state_manager, gateway):
                    print("status=BLOCKED_POSITION_OPENED_DURING_MONITOR")
                    print("No new order sent.")
                    reporting.record(
                        "RUN_BLOCKED",
                        "WARNING",
                        mode=mode,
                        symbol=symbol,
                        status="BLOCKED_POSITION_OPENED_DURING_MONITOR",
                    )
                    await emit_run_summary("BLOCKED_POSITION_OPENED_DURING_MONITOR", "WARNING")
                    return 2

                token = uuid4().hex[:12]
                report, pre_submission = await gateway.route_revalidated_order_submission(
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
                    ),
                    maximum_currency_risk=risk_snapshot.sizing.currency_risk,
                    maximum_spread_price=MAXIMUM_ENTRY_SPREAD_PRICE,
                    observed_quote_age_seconds=activity_snapshot.quote_age_seconds,
                    adaptive_lot_sizing=True,
                    demo_observation_minimum_lot=execute_one_demo_trade,
                )
                print(f"pre_submission_approved={pre_submission.is_approved}")
                print(f"pre_submission_requested_lots={pre_submission.requested_lots:.2f}")
                print(f"pre_submission_normalized_lots={pre_submission.normalized_lots:.2f}")
                print(f"pre_submission_live_entry={pre_submission.live_entry_price:.2f}")
                print(f"pre_submission_currency_risk={pre_submission.currency_risk:.2f}")
                print(f"pre_submission_quote_age_seconds={pre_submission.quote_age_seconds:.3f}")
                if pre_submission.adapted_to_fit_risk:
                    print("pre_submission_adaptive_lot_sizing=APPLIED")
                    reporting.record(
                        "ADAPTIVE_LOT_REDUCTION",
                        "INFO",
                        mode=mode,
                        symbol=symbol,
                        setup_id=setup.id,
                        requested_lots=pre_submission.requested_lots,
                        adjusted_lots=pre_submission.normalized_lots,
                        live_currency_risk=pre_submission.currency_risk,
                        approved_currency_risk=risk_snapshot.sizing.currency_risk,
                    )
                if pre_submission.demo_minimum_lot_override:
                    print("pre_submission_demo_observation_override=MINIMUM_LOT")
                    reporting.record(
                        "DEMO_MIN_LOT_OBSERVATION_OVERRIDE",
                        "WARNING",
                        mode=mode,
                        symbol=symbol,
                        setup_id=setup.id,
                        requested_lots=pre_submission.requested_lots,
                        adjusted_lots=pre_submission.normalized_lots,
                        live_currency_risk=pre_submission.currency_risk,
                        approved_currency_risk=risk_snapshot.sizing.currency_risk,
                    )
                if pre_submission.rejection_reasons:
                    print(f"pre_submission_rejection={','.join(pre_submission.rejection_reasons)}")
                print(f"status={report.status.value}")
                print(f"broker_order_id={report.broker_order_id}")
                print(f"filled_quantity={report.filled_quantity:.2f}")
                print(f"rejection_reason={report.rejection_reason}")
                reporting.record(
                    "PRE_SUBMISSION_CHECK",
                    "INFO" if pre_submission.is_approved else "WARNING",
                    mode=mode,
                    symbol=symbol,
                    setup_id=setup.id,
                    pre_submission_approved=pre_submission.is_approved,
                    requested_lots=pre_submission.requested_lots,
                    normalized_lots=pre_submission.normalized_lots,
                    adapted_to_fit_risk=pre_submission.adapted_to_fit_risk,
                    demo_minimum_lot_override=pre_submission.demo_minimum_lot_override,
                    live_entry_price=pre_submission.live_entry_price,
                    currency_risk=pre_submission.currency_risk,
                    quote_age_seconds=pre_submission.quote_age_seconds,
                    rejection_reasons=pre_submission.rejection_reasons,
                )
                await reporting.record_and_notify(
                    "ORDER_RESULT",
                    "INFO" if report.status == OrderStatus.FILLED else "WARNING",
                    notify=True,
                    mode=mode,
                    symbol=symbol,
                    setup_id=setup.id,
                    order_status=report.status.value,
                    broker_order_id=report.broker_order_id,
                    filled_quantity=report.filled_quantity,
                    rejection_reason=report.rejection_reason,
                )
                if report.status != OrderStatus.FILLED:
                    await emit_run_summary(report.status.value, "WARNING")
                    return 1
                entry_submitted_this_run = True
                filled_positions = await gateway.query_live_positions()
                if len(filled_positions) == 1 and filled_positions[0].direction is not None:
                    filled = filled_positions[0]
                    managed_plan = ManagedTradePlan(
                        symbol=filled.symbol,
                        ticket=filled.ticket,
                        direction=filled.direction,
                        entry=filled.average_entry_price,
                        initial_stop_loss=setup.stop_loss,
                        final_take_profit=setup.take_profit,
                    )
                    plan_store.save(managed_plan)
                    print(f"protection_plan_saved_for_ticket={filled.ticket}")
                if not manage_open_demo_trade:
                    print("Rerun with explicitly authorized position management to trail this managed trade.")
                    await emit_run_summary("DEMO_ORDER_FILLED_MANAGEMENT_NOT_ENABLED")
                    return 0

            await asyncio.sleep(poll_seconds)

        print(f"live_quotes_processed={live_quotes}")
        print(f"live_closed_candles_ingested={live_closed_candles}")
        print(f"temporary_tick_gaps={tick_read_failures}")
        print(f"quote_updates_confirming_live_feed={quote_activity.updates_observed}")
        print(f"inactive_quote_reads_discarded={inactive_quote_reads}")
        print(f"stop_updates_applied={stop_updates}")
        print(f"qualified_candidates={qualified}")
        diagnostics = detector.diagnostic_snapshot
        print(f"live_sweeps_detected={diagnostics['live_sweeps_detected']}")
        print(f"reversal_candidates_detected={diagnostics['reversal_candidates_detected']}")
        print(f"confirmation_blocks={diagnostics['confirmation_blocks']}")
        print(f"quality_blocks={diagnostics['quality_blocks']}")
        print(f"cooldown_blocks={diagnostics['cooldown_blocks']}")
        nearest_pool = diagnostics["nearest_active_pool"]
        nearest_liquidity_level = None
        if live_quotes == 0 and inactive_quote_reads > 0:
            print("market_data_status=NO_RECENT_TICK_ACTIVITY")
            print("nearest_liquidity_level=NOT_REPORTED_WITHOUT_FRESH_MARKET_PRICE")
        elif nearest_pool:
            nearest_liquidity_level = {
                "side": nearest_pool["side"],
                "price": nearest_pool["level_price"],
                "distance": nearest_pool["distance"],
                "active_pool_count": nearest_pool["active_pool_count"],
            }
            print(f"active_liquidity_pools={nearest_pool['active_pool_count']}")
            print(
                f"nearest_liquidity_level={nearest_pool['side']} "
                f"price={nearest_pool['level_price']:.2f} "
                f"distance={nearest_pool['distance']:.2f}"
            )
        else:
            print("active_liquidity_pools=0")
        latest_confirmation_rejection = None
        if diagnostics["latest_confirmation_reasons"]:
            latest_confirmation_rejection = ",".join(diagnostics["latest_confirmation_reasons"])
            print(f"latest_confirmation_rejection={latest_confirmation_rejection}")
        if live_quotes == 0 and inactive_quote_reads > 0:
            print("status=SHADOW_TEST_INVALID_NO_RECENT_TICK_ACTIVITY")
            print("No order sent.")
            summary = {
                "mode": mode,
                "symbol": symbol,
                "status": "SHADOW_TEST_INVALID_NO_RECENT_TICK_ACTIVITY",
                "dry_run": gateway_config.dry_run,
                "require_demo": gateway_config.require_demo,
                "max_lot": gateway_config.max_lot,
                "live_quotes_processed": live_quotes,
                "live_closed_candles_ingested": live_closed_candles,
                "temporary_tick_gaps": tick_read_failures,
                "quote_updates_confirming_live_feed": quote_activity.updates_observed,
                "inactive_quote_reads_discarded": inactive_quote_reads,
                "stop_updates_applied": stop_updates,
                "qualified_candidates": qualified,
                "live_sweeps_detected": diagnostics["live_sweeps_detected"],
                "reversal_candidates_detected": diagnostics["reversal_candidates_detected"],
                "confirmation_blocks": diagnostics["confirmation_blocks"],
                "quality_blocks": diagnostics["quality_blocks"],
                "cooldown_blocks": diagnostics["cooldown_blocks"],
                "nearest_liquidity_level": nearest_liquidity_level,
                "latest_confirmation_rejection": latest_confirmation_rejection,
            }
            reporting.record("RUN_SUMMARY", "WARNING", **summary)
            await reporting.send_session_summary(summary)
            return 3
        print("status=NO_QUALIFIED_SIGNAL_BEFORE_TIMEOUT")
        print("No order sent.")
        summary = {
            "mode": mode,
            "symbol": symbol,
            "status": "NO_QUALIFIED_SIGNAL_BEFORE_TIMEOUT",
            "dry_run": gateway_config.dry_run,
            "require_demo": gateway_config.require_demo,
            "max_lot": gateway_config.max_lot,
            "live_quotes_processed": live_quotes,
            "live_closed_candles_ingested": live_closed_candles,
            "temporary_tick_gaps": tick_read_failures,
            "quote_updates_confirming_live_feed": quote_activity.updates_observed,
            "inactive_quote_reads_discarded": inactive_quote_reads,
            "stop_updates_applied": stop_updates,
            "qualified_candidates": qualified,
            "live_sweeps_detected": diagnostics["live_sweeps_detected"],
            "reversal_candidates_detected": diagnostics["reversal_candidates_detected"],
            "confirmation_blocks": diagnostics["confirmation_blocks"],
            "quality_blocks": diagnostics["quality_blocks"],
            "cooldown_blocks": diagnostics["cooldown_blocks"],
            "nearest_liquidity_level": nearest_liquidity_level,
            "latest_confirmation_rejection": latest_confirmation_rejection,
        }
        reporting.record("RUN_SUMMARY", "INFO", **summary)
        await reporting.send_session_summary(summary)
        return 0
    except Exception as exc:
        reporting.record("RUN_ERROR", "ERROR", mode=mode, error=str(exc))
        await reporting.notify("Apex Runner Error", {"mode": mode, "error": str(exc)}, "ERROR")
        raise
    finally:
        if risk is not None:
            await risk.terminate()
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
    parser.add_argument("--manage-open-demo", action="store_true")
    parser.add_argument("--confirm-management")
    parser.add_argument("--profile", default=None, help="Selector profile name. Defaults to APEX_SELECTOR_PROFILE or v3_candidate_safety.")
    args = parser.parse_args()
    if args.duration_seconds <= 0 or args.poll_seconds <= 0 or args.warmup_bars <= 0:
        parser.error("duration, poll interval, and warmup bars must be positive.")
    if args.execute_one_demo and args.confirm_execution != EXECUTION_CONFIRMATION:
        parser.error(f"--confirm-execution must be {EXECUTION_CONFIRMATION} when execution is requested.")
    if args.manage_open_demo and args.confirm_management != MANAGEMENT_CONFIRMATION:
        parser.error(f"--confirm-management must be {MANAGEMENT_CONFIRMATION} when management is requested.")
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
                arguments.manage_open_demo,
                arguments.profile,
            )
        )
    )
