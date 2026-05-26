"""Submit one explicitly confirmed, minimum-size trade to an MT5 demo account."""

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

CONFIRMATION_TEXT = "EXECUTE_ONE_DEMO_ORDER"
SMOKE_TEST_VOLUME = 0.01


async def submit_demo_smoke_trade(direction: OrderDirection) -> int:
    configured = load_mt5_config(ROOT / ".env")
    if not configured.require_demo:
        raise RuntimeError("Refusing demo trade because APEX_MT5_REQUIRE_DEMO is not true.")
    if configured.max_lot > SMOKE_TEST_VOLUME:
        raise RuntimeError("Refusing demo smoke trade because APEX_MAX_LOT must be 0.01 or lower.")

    # Enable sending for this explicitly confirmed invocation only; .env remains in safe dry-run mode.
    live_demo_config = replace(configured, dry_run=False, max_lot=min(configured.max_lot, SMOKE_TEST_VOLUME))
    gateway = MT5BrokerGateway(live_demo_config)
    await gateway.connect()
    try:
        summary = gateway.connection_summary()
        symbol = str(summary["symbol"])
        tick = gateway.read_current_tick()
        entry = tick.ask if direction == OrderDirection.BUY else tick.bid
        stop_loss = entry - 5.0 if direction == OrderDirection.BUY else entry + 5.0
        take_profit = entry + 10.0 if direction == OrderDirection.BUY else entry - 10.0
        order_id = uuid4().hex[:12]
        request = OrderRequest(
            client_order_id=f"DEMO_SMOKE_{order_id}",
            symbol=symbol,
            direction=direction,
            quantity_lots=live_demo_config.max_lot,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            idempotency_key=f"DEMO_SMOKE_ONCE_{order_id}",
            timestamp=datetime.now(timezone.utc),
        )
        report = await gateway.route_order_submission(request)
        print("MT5 DEMO TRADE SMOKE TEST")
        print("demo_account_required=True")
        print(f"symbol={symbol}")
        print(f"direction={direction.value}")
        print(f"quantity_lots={live_demo_config.max_lot:.2f}")
        print(f"status={report.status.value}")
        print(f"broker_order_id={report.broker_order_id}")
        print(f"filled_quantity={report.filled_quantity:.2f}")
        print(f"entry_price={report.last_fill_price:.2f}")
        print(f"stop_loss={stop_loss:.2f}")
        print(f"take_profit={take_profit:.2f}")
        print(f"rejection_reason={report.rejection_reason}")
        if report.status != OrderStatus.FILLED:
            return 1

        positions = await gateway.query_live_positions()
        print(f"visible_open_gold_positions={len(positions)}")
        return 0
    finally:
        await gateway.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute exactly one minimum-size MT5 demo Gold trade.")
    parser.add_argument("--confirm-demo-order", required=True)
    parser.add_argument("--direction", choices=["BUY", "SELL"], default="BUY")
    args = parser.parse_args()
    if args.confirm_demo_order != CONFIRMATION_TEXT:
        parser.error(f"--confirm-demo-order must be {CONFIRMATION_TEXT}")
    return args


if __name__ == "__main__":
    parsed = parse_args()
    raise SystemExit(asyncio.run(submit_demo_smoke_trade(OrderDirection(parsed.direction))))
