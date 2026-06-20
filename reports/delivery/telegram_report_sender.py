from __future__ import annotations

from pathlib import Path

from reports.delivery.telegram_client import TelegramClient
from reports.report_config import ReportingConfig


def send_weekly_report(
    period: str,
    paths: dict[str, Path],
    summary: str,
    config: ReportingConfig,
    client: TelegramClient | None = None,
) -> dict:
    if not config.telegram_enabled:
        return {"enabled": False, "success": False, "sent_files": [], "errors": ["telegram_disabled"]}
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return {"enabled": True, "success": False, "sent_files": [], "errors": ["telegram_credentials_missing"]}
    telegram = client or TelegramClient(config.telegram_bot_token, config.telegram_chat_id)
    sent_files: list[str] = []
    errors: list[str] = []
    if config.telegram_send_summary:
        result = telegram.send_message(f"Weekly trading report {period}\n\n{summary[:3500]}")
        if not result.get("success"):
            errors.append(f"summary_send_failed:{result.get('error')}")
    if config.telegram_send_files:
        max_bytes = int(config.telegram_max_file_size_mb * 1024 * 1024)
        for key in ("markdown", "html", "metrics", "verification", "manual_chart_review", "trade_summary"):
            path = paths.get(key)
            if not path or not path.exists():
                continue
            if path.stat().st_size > max_bytes:
                errors.append(f"file_too_large:{path.name}")
                continue
            result = telegram.send_document(path, caption=f"{period} {key}")
            if result.get("success"):
                sent_files.append(path.name)
            else:
                errors.append(f"file_send_failed:{path.name}:{result.get('error')}")
    return {"enabled": True, "success": not errors, "sent_files": sent_files, "errors": errors}

