"""Validate the scoring-to-MT5 path without generating or sending a trade."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.domain.confirmation_models import (
    AlignmentStatus,
    ConfirmationMetrics,
    ConfirmationSnapshot,
    ConfirmationTier,
)
from src.core.domain.constants import OrderDirection
from src.core.domain.execution_models import OrderRequest
from src.core.domain.market_data import TickNode
from src.core.domain.setup_models import SetupOpportunityNode, SetupQualityTier, SetupType
from src.core.events.event_bus import EventBus
from src.execution.position_sizer import InstitutionalPositionSizer
from src.execution.pre_submission_guard import LiveQuoteActivityMonitor, QuoteActivitySnapshot
from src.execution.risk_firewall import RiskManagementOrchestrator
from src.infrastructure.broker.mt5_config import load_mt5_config
from src.infrastructure.broker.mt5_gateway import MT5BrokerGateway
from src.strategy.scoring_matrix import TradeScoringOrchestrator
from src.strategy.state_manager import CentralRuntimeStateManager

VALIDATION_VOLUME_CAP = 0.01
MAXIMUM_ENTRY_SPREAD_PRICE = 0.35
MAXIMUM_LIVE_QUOTE_INACTIVITY_SECONDS = 5.0


async def wait_for_live_quote_activity(
    gateway: MT5BrokerGateway, timeout_seconds: float = 8.0
) -> tuple[TickNode | None, QuoteActivitySnapshot | None]:
    """Wait for at least one arriving quote change before testing the order boundary."""
    monitor = LiveQuoteActivityMonitor(MAXIMUM_LIVE_QUOTE_INACTIVITY_SECONDS)
    latest_tick = None
    latest_snapshot = None
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        latest_tick = gateway.read_current_tick()
        latest_snapshot = monitor.observe(latest_tick)
        if latest_snapshot.is_fresh:
            return latest_tick, latest_snapshot
        await asyncio.sleep(0.25)
    return None, latest_snapshot


def _build_validation_candidate(entry: float, now: datetime) -> tuple[SetupOpportunityNode, ConfirmationSnapshot]:
    """Create a synthetic high-quality candidate for integration verification only."""
    setup = SetupOpportunityNode(
        id=f"DRY_RUN_SETUP_{uuid4().hex[:12]}",
        setup_type=SetupType.LIQUIDITY_SWEEP_REVERSAL,
        direction=OrderDirection.BUY,
        entry_price=entry,
        stop_loss=entry - 5.0,
        take_profit=entry + 10.0,
        estimated_rr=2.0,
        quality_tier=SetupQualityTier.ELITE_INSTITUTIONAL,
        confidence_score=96.0,
        creation_time=now,
        expiration_time=now + timedelta(minutes=1),
        correlation_id="MT5_DRY_RUN_PIPELINE_VALIDATION",
        timeframe="INTEGRATION_CHECK_ONLY",
    )
    confirmation = ConfirmationSnapshot(
        timestamp=now,
        overall_tier=ConfirmationTier.HIGH_CONVICTION,
        confidence_score=96.0,
        is_validated=True,
        alignment=AlignmentStatus.FULLY_ALIGNED,
        metrics=ConfirmationMetrics(
            momentum_velocity_score=96.0,
            displacement_ratio=1.5,
            wick_rejection_pct=80.0,
            mtf_alignment_score=96.0,
            volatility_expansion_factor=1.2,
            session_efficiency_index=95.0,
        ),
        validated_components=["SYNTHETIC_PIPELINE_VALIDATION"],
    )
    return setup, confirmation


async def main() -> int:
    config = load_mt5_config(ROOT / ".env")
    if not config.dry_run:
        raise RuntimeError("Refusing pipeline validation because APEX_MT5_DRY_RUN is not true.")
    if not config.require_demo:
        raise RuntimeError("Refusing pipeline validation because APEX_MT5_REQUIRE_DEMO is not true.")

    state_manager = CentralRuntimeStateManager()
    event_bus = EventBus()
    gateway = MT5BrokerGateway(config)
    await state_manager.bootstrap()
    await gateway.connect()
    try:
        summary = gateway.connection_summary()
        symbol = str(summary["symbol"])
        tick, activity_snapshot = await wait_for_live_quote_activity(gateway)
        if tick is None or activity_snapshot is None:
            print("MT5 pipeline validation - DRY RUN ONLY; NO ORDER WILL BE SENT")
            print(f"symbol={symbol}")
            print("market_data_status=NO_RECENT_TICK_ACTIVITY")
            return 1
        ask = tick.ask
        bid = tick.bid
        if ask <= 0.0 or bid <= 0.0:
            raise RuntimeError(f"Invalid quote received for {symbol}")

        now = datetime.now(timezone.utc)
        await state_manager.commit_market_update(
            {
                "last_tick_time": now.replace(tzinfo=None),
                "current_ask": ask,
                "current_bid": bid,
                "current_mid": (ask + bid) / 2.0,
                "current_spread": ask - bid,
                "accumulated_tick_count": 1,
                "is_synchronized": True,
            },
            "MT5_DRY_RUN_QUOTE_SYNC",
        )

        setup, confirmation = _build_validation_candidate(ask, now.replace(tzinfo=None))
        scorer = TradeScoringOrchestrator(event_bus, state_manager)
        risk_limit_lots = min(config.max_lot, VALIDATION_VOLUME_CAP)
        sizing_specification = gateway.read_sizing_specification()
        risk_manager = RiskManagementOrchestrator(
            event_bus,
            state_manager,
            position_sizer=InstitutionalPositionSizer(
                account_equity=sizing_specification.account_equity,
                maximum_lots=min(risk_limit_lots, sizing_specification.volume_max),
                minimum_lots=sizing_specification.volume_min,
                volume_step=sizing_specification.volume_step,
                loss_per_lot_calculator=lambda candidate: gateway.calculate_stop_loss_currency_per_lot(
                    candidate.direction,
                    candidate.entry_price,
                    candidate.stop_loss,
                ),
            ),
        )
        ranked = await scorer.process_and_rank_setup(setup, confirmation)

        print("MT5 pipeline validation - DRY RUN ONLY; NO ORDER WILL BE SENT")
        print(f"symbol={symbol}")
        print(f"risk_sizing_source=MT5_ACCOUNT_CURRENCY_{sizing_specification.account_currency}")
        print(f"broker_volume_step={sizing_specification.volume_step:.2f}")
        print(f"spread={ask - bid:.5f}")
        print(f"score={ranked.score_breakdown.normalized_final_score:.2f}")
        print(f"score_approved={ranked.is_live_executable}")
        if not ranked.is_live_executable:
            print(f"blocked_by_scoring={','.join(ranked.rejection_payload)}")
            return 1

        approved, risk_snapshot = await risk_manager.evaluate_trade_entry_gate(
            setup,
            confirmation,
            ranked.execution_multiplier,
        )
        print(f"risk_approved={approved}")
        print(f"calculated_lots={risk_snapshot.sizing.calculated_lots:.4f}")
        print(f"applied_currency_risk={risk_snapshot.sizing.currency_risk:.2f}")
        print(f"applied_risk_pct={risk_snapshot.sizing.risk_percentage_applied:.4f}")
        if not approved:
            print(f"blocked_by_risk={','.join(risk_snapshot.rejection_reasons)}")
            return 1

        volume = risk_snapshot.sizing.calculated_lots
        request = OrderRequest(
            client_order_id=f"PIPELINE_CHECK_{uuid4().hex[:12]}",
            symbol=symbol,
            direction=setup.direction,
            quantity_lots=volume,
            entry_price=setup.entry_price,
            stop_loss=setup.stop_loss,
            take_profit=setup.take_profit,
            idempotency_key=f"PIPELINE_DRY_RUN_{uuid4().hex}",
            timestamp=now,
        )
        report, pre_submission = await gateway.route_revalidated_order_submission(
            request,
            maximum_currency_risk=risk_snapshot.sizing.currency_risk,
            maximum_spread_price=MAXIMUM_ENTRY_SPREAD_PRICE,
            observed_quote_age_seconds=activity_snapshot.quote_age_seconds,
        )
        print(f"checked_lots={volume:.4f}")
        print(f"pre_submission_approved={pre_submission.is_approved}")
        print(f"pre_submission_live_currency_risk={pre_submission.currency_risk:.2f}")
        print(f"pre_submission_quote_age_seconds={pre_submission.quote_age_seconds:.3f}")
        if pre_submission.rejection_reasons:
            print(f"pre_submission_rejection={','.join(pre_submission.rejection_reasons)}")
        print(f"mt5_check_status={report.status.value}")
        print(f"rejection_reason={report.rejection_reason}")
        return 0 if report.rejection_reason is None else 1
    finally:
        await gateway.disconnect()
        await state_manager.terminate()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
