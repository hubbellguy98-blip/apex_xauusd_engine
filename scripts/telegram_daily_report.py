"""Build and optionally send the Telegram daily intelligence report."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.infrastructure.telemetry.telegram_reporting import TelegramReportingService


async def main() -> int:
    parser = argparse.ArgumentParser(description="Send the Apex daily Telegram report.")
    parser.add_argument("--lookback-hours", type=int, default=None)
    args = parser.parse_args()
    service = TelegramReportingService.from_env_file(ROOT / ".env", ROOT)
    report = await service.send_daily_report(args.lookback_hours)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
