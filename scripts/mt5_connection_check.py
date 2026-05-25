"""Check MT5 demo account connectivity without placing trades."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.infrastructure.broker.mt5_config import load_mt5_config
from src.infrastructure.broker.mt5_gateway import MT5BrokerGateway


async def main() -> int:
    config = load_mt5_config(ROOT / ".env")
    gateway = MT5BrokerGateway(config)
    await gateway.connect()
    try:
        summary = gateway.connection_summary()
        print("MT5 connection OK")
        print(f"login={summary['login']}")
        print(f"server={summary['server']}")
        print(f"symbol={summary['symbol']}")
        print(f"dry_run={summary['dry_run']}")
        print(f"max_lot={summary['max_lot']}")
        return 0
    finally:
        await gateway.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
