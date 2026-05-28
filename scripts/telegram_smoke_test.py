"""Send a safe Telegram test message using local .env settings."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.infrastructure.telemetry.telegram_reporting import TelegramReportingService


async def main() -> int:
    service = TelegramReportingService.from_env_file(ROOT / ".env", ROOT)
    if not service.config.enabled:
        print("Telegram reporting is disabled. Set APEX_TELEGRAM_ENABLED=true after adding bot token and chat id.")
        return 1
    await service.record_and_notify(
        "TELEGRAM_SMOKE_TEST",
        "INFO",
        notify=True,
        message="Apex Telegram reporting is connected.",
        mode="SAFE_TEST_NO_TRADING",
    )
    print("Telegram smoke test sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
