"""Run a safe MT5 order_check using dry-run defaults."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.domain.constants import OrderDirection
from src.core.domain.execution_models import OrderRequest
from src.infrastructure.broker.mt5_config import load_mt5_config
from src.infrastructure.broker.mt5_gateway import MT5BrokerGateway


async def main() -> int:
    config = load_mt5_config(ROOT / ".env")
    gateway = MT5BrokerGateway(config)
    await gateway.connect()
    try:
        summary = gateway.connection_summary()
        symbol = str(summary["symbol"])
        mt5 = gateway._mt5
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"No tick available for {symbol}")

        entry = float(tick.ask)
        request = OrderRequest(
            client_order_id=f"MT5_CHECK_{int(datetime.now(timezone.utc).timestamp())}",
            symbol=symbol,
            direction=OrderDirection.BUY,
            quantity_lots=min(config.max_lot, 0.05),
            entry_price=entry,
            stop_loss=entry - 5.0,
            take_profit=entry + 10.0,
            idempotency_key="MT5_ORDER_CHECK_ONLY",
            timestamp=datetime.now(timezone.utc),
        )
        report = await gateway.route_order_submission(request)
        print("MT5 order_check completed")
        print(f"status={report.status.value}")
        print(f"broker_order_id={report.broker_order_id}")
        print(f"filled_quantity={report.filled_quantity}")
        print(f"remaining_quantity={report.remaining_quantity}")
        print(f"rejection_reason={report.rejection_reason}")
        return 0
    finally:
        await gateway.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
