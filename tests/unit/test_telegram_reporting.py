from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.infrastructure.telemetry.runtime_event_log import JsonlRuntimeEventLog
from src.infrastructure.telemetry.telegram_reporting import (
    DailyReportBuilder,
    TelegramReportingConfig,
    format_compact_message,
    split_telegram_message,
)


def test_telegram_config_is_disabled_by_default(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    config = TelegramReportingConfig.from_env_file(env_file, tmp_path)

    assert config.enabled is False
    assert config.bot_token is None
    assert config.chat_id is None
    assert config.event_log_path == tmp_path / ".apex_runtime" / "notifications" / "telegram_events.jsonl"


def test_telegram_config_requires_token_and_chat_when_enabled(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APEX_TELEGRAM_ENABLED=true\nAPEX_TELEGRAM_BOT_TOKEN=abc\n", encoding="utf-8")

    try:
        TelegramReportingConfig.from_env_file(env_file, tmp_path)
    except RuntimeError as exc:
        assert "chat id" in str(exc).lower()
    else:
        raise AssertionError("partial Telegram configuration should fail")


def test_runtime_event_log_appends_and_reads_recent_events(tmp_path) -> None:
    event_log = JsonlRuntimeEventLog(tmp_path / "events.jsonl")
    event_log.append("RUN_STARTED", "INFO", password="hidden", symbol="GOLD.i#")
    event_log.append("RUN_SUMMARY", "INFO", live_quotes_processed=10)

    events = event_log.read_since(datetime.now(timezone.utc) - timedelta(minutes=1))

    assert [event.event_type for event in events] == ["RUN_STARTED", "RUN_SUMMARY"]
    assert events[0].payload["password"] == "***"
    assert events[1].payload["live_quotes_processed"] == 10


def test_daily_report_summarizes_key_runtime_metrics() -> None:
    now = datetime.now(timezone.utc)
    events = [
        _event(now, "CANDIDATE_BLOCKED", "INFO", reasons=["LOW_SCORE"]),
        _event(now, "RUN_SUMMARY", "INFO", symbol="GOLD.i#", mode="SHADOW_ONLY_NO_ORDER", live_quotes_processed=25),
    ]

    report = DailyReportBuilder(events, lookback_hours=24).build_text_report()

    assert "Apex XAUUSD Daily Intelligence Report" in report
    assert "GOLD.i#" in report
    assert "Live quotes processed: 25" in report
    assert "LOW_SCORE: 1" in report


def test_compact_message_escapes_html_and_hides_sensitive_fields() -> None:
    message = format_compact_message(
        "Risk <Check>",
        {"symbol": "GOLD.i#", "bot_token": "secret", "status": "SAFE & READY"},
    )

    assert "Risk &lt;Check&gt;" in message
    assert "SAFE &amp; READY" in message
    assert "secret" not in message


def test_split_telegram_message_keeps_chunks_under_limit() -> None:
    chunks = split_telegram_message("\n".join(f"line {index}" for index in range(1000)))

    assert len(chunks) > 1
    assert all(len(chunk) <= 3900 for chunk in chunks)


def _event(timestamp: datetime, event_type: str, severity: str, **payload):
    from src.infrastructure.telemetry.runtime_event_log import RuntimeEvent

    return RuntimeEvent(timestamp, event_type, severity, payload)
