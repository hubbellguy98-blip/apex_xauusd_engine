"""Run one simple price-movement trigger that may submit one MT5 demo Gold trade."""

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

from src.core.domain.constants import OrderDirection
from src.core.domain.execution_models import OrderRequest, OrderStatus
from src.infrastructure.broker.mt5_config import load_mt5_config
from src.infrastructure.broker.mt5_gateway import MT5BrokerGateway

CONFIRMATION_TEXT = "ENABLE_ONE_DEMO_AUTO_TRADE"
MAXIMUM_VOLUME = 0.05


async def run_auto_trigger(timeout_seconds: float, trigger_distance: float, poll_seconds: float) -> int:
    configured = load_mt5_config(ROOT / ".env")
    if not configured.dry_run:
        raise RuntimeError("Keep APEX_MT5_DRY_RUN=true; this command enables only one confirmed demo trade.")
    if not configured.require_demo:
        raise RuntimeError("Refusing automatic trade because APEX_MT5_REQUIRE_DEMO is not true.")
    if configured.max_lot > MAXIMUM_VOLUME:
        raise RuntimeError("Refusing automatic trade because APEX_MAX_LOT must be 0.05 or lower.")

    single_trade_config = replace(configured, dry_run=False, max_lot=min(configured.max_lot, MAXIMUM_VOLUME))
    gateway = MT5BrokerGateway(single_trade_config)
    await gateway.connect()
    try:
        summary = gateway.connection_summary()
        symbol = str(summary["symbol"])
        existing_positions = await gateway.query_live_positions()
        if existing_positions:
            print("MT5 DEMO AUTOMATIC TRIGGER")
            print("status=BLOCKED_EXISTING_GOLD_POSITION")
            print(f"symbol={symbol}")
            print(f"open_gold_positions={len(existing_positions)}")
            print("No new order sent. Close the existing Gold demo position before running this trigger.")
            return 2

        baseline_tick = gateway.read_current_tick()
        baseline_mid = baseline_tick.mid
        started = asyncio.get_running_loop().time()
        previous_signature = None
        unique_quotes = 0
        print("MT5 DEMO AUTOMATIC TRIGGER ARMED")
        print(f"symbol={symbol}")
        print(f"quantity_lots={single_trade_config.max_lot:.2f}")
        print(f"baseline_price={baseline_mid:.2f}")
        print(f"trigger_distance={trigger_distance:.2f}")

        while asyncio.get_running_loop().time() - started < timeout_seconds:
            tick = gateway.read_current_tick()
            signature = (tick.timestamp, tick.bid, tick.ask)
            if signature == previous_signature:
                await asyncio.sleep(poll_seconds)
                continue
            previous_signature = signature
            unique_quotes += 1

            direction = None
            if tick.mid >= baseline_mid + trigger_distance:
                direction = OrderDirection.BUY
            elif tick.mid <= baseline_mid - trigger_distance:
                direction = OrderDirection.SELL

            if direction is None:
                await asyncio.sleep(poll_seconds)
                continue

            # Recheck immediately before submitting to avoid duplicate positions.
            if await gateway.query_live_positions():
                print("status=BLOCKED_POSITION_OPENED_DURING_MONITOR")
                print("No new order sent.")
                return 2

            entry = tick.ask if direction == OrderDirection.BUY else tick.bid
            stop_loss = entry - 5.0 if direction == OrderDirection.BUY else entry + 5.0
            take_profit = entry + 10.0 if direction == OrderDirection.BUY else entry - 10.0
            token = uuid4().hex[:12]
            request = OrderRequest(
                client_order_id=f"DEMO_AUTO_{token}",
                symbol=symbol,
                direction=direction,
                quantity_lots=single_trade_config.max_lot,
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                idempotency_key=f"DEMO_AUTO_ONCE_{token}",
                timestamp=datetime.now(timezone.utc),
            )
            report = await gateway.route_order_submission(request)
            print(f"trigger_direction={direction.value}")
            print(f"unique_quotes_seen={unique_quotes}")
            print(f"status={report.status.value}")
            print(f"broker_order_id={report.broker_order_id}")
            print(f"filled_quantity={report.filled_quantity:.2f}")
            print(f"entry_price={report.last_fill_price:.2f}")
            print(f"stop_loss={stop_loss:.2f}")
            print(f"take_profit={take_profit:.2f}")
            print(f"rejection_reason={report.rejection_reason}")
            return 0 if report.status == OrderStatus.FILLED else 1

        print("status=NO_TRIGGER_BEFORE_TIMEOUT")
        print(f"unique_quotes_seen={unique_quotes}")
        print("No order sent.")
        return 0
    finally:
        await gateway.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Allow one simple automatic trade on the logged-in MT5 demo account.")
    parser.add_argument("--confirm-demo-auto", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--trigger-distance", type=float, default=0.20)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    args = parser.parse_args()
    if args.confirm_demo_auto != CONFIRMATION_TEXT:
        parser.error(f"--confirm-demo-auto must be {CONFIRMATION_TEXT}")
    if args.timeout_seconds <= 0 or args.trigger_distance <= 0 or args.poll_seconds <= 0:
        parser.error("timeout, trigger distance, and polling interval must be positive.")
    return args


if __name__ == "__main__":
    options = parse_args()
    raise SystemExit(
        asyncio.run(
            run_auto_trigger(options.timeout_seconds, options.trigger_distance, options.poll_seconds)
        )
    )
