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
    assert config.notify_run_started is False
    assert config.notify_session_summary is False
    assert config.notify_qualified_signal is False
    assert config.notify_order_result is True
    assert config.notify_order_rejection is False


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


def test_notification_policy_is_quiet_except_filled_orders_and_errors(tmp_path) -> None:
    from src.infrastructure.telemetry.telegram_reporting import TelegramReportingService

    service = TelegramReportingService.disabled(tmp_path)

    assert not service.should_notify_event("RUN_STARTED", "INFO", {})
    assert not service.should_notify_event("RISK_APPROVED", "INFO", {})
    assert not service.should_notify_event("ORDER_RESULT", "WARNING", {"order_status": "REJECTED"})
    assert service.should_notify_event("ORDER_RESULT", "INFO", {"order_status": "FILLED"})
    assert service.should_notify_event("MT5_CONNECTION_FAILED", "ERROR", {})


def test_notification_policy_can_opt_into_noisy_operator_messages(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "APEX_TELEGRAM_NOTIFY_RUN_STARTED=true",
                "APEX_TELEGRAM_NOTIFY_SESSION_SUMMARY=true",
                "APEX_TELEGRAM_NOTIFY_QUALIFIED_SIGNAL=true",
                "APEX_TELEGRAM_NOTIFY_ORDER_REJECTION=true",
            ]
        ),
        encoding="utf-8",
    )

    config = TelegramReportingConfig.from_env_file(env_file, tmp_path)

    assert config.notify_run_started is True
    assert config.notify_session_summary is True
    assert config.notify_qualified_signal is True
    assert config.notify_order_rejection is True


def test_daily_report_summarizes_key_runtime_metrics() -> None:
    now = datetime.now(timezone.utc)
    events = [
        _event(now, "CANDIDATE_BLOCKED", "INFO", reasons=["LOW_SCORE"]),
        _event(now, "RUN_SUMMARY", "INFO", symbol="GOLD.i#", mode="SHADOW_ONLY_NO_ORDER", live_quotes_processed=25),
    ]

    report = DailyReportBuilder(events, lookback_hours=24).build_text_report()

    assert "Apex XAUUSD Daily Intelligence Report" in report
    assert "GOLD.i#" in report
    assert "24h Market Data Health" in report
    assert "Live quotes processed: 25" in report
    assert "LOW_SCORE: 1" in report
    assert "Session Breakdown" in report


def test_daily_report_uses_full_day_totals_not_only_latest_run() -> None:
    now = datetime.now(timezone.utc)
    events = [
        _event(
            now - timedelta(hours=2),
            "RUN_SUMMARY",
            "INFO",
            symbol="GOLD.i#",
            mode="ONE_DEMO_EXECUTION_AND_MANAGEMENT",
            status="NO_QUALIFIED_SIGNAL_BEFORE_TIMEOUT",
            live_quotes_processed=100,
            qualified_candidates=3,
            live_sweeps_detected=5,
        ),
        _event(
            now,
            "RUN_SUMMARY",
            "WARNING",
            symbol="GOLD.i#",
            mode="ONE_DEMO_EXECUTION_AND_MANAGEMENT",
            status="SHADOW_TEST_INVALID_NO_RECENT_TICK_ACTIVITY",
            live_quotes_processed=0,
            inactive_quote_reads_discarded=30,
            qualified_candidates=0,
        ),
        _event(now, "RISK_APPROVED", "INFO"),
        _event(now, "ORDER_RESULT", "WARNING", order_status="REJECTED"),
    ]

    report = DailyReportBuilder(events, lookback_hours=24).build_text_report()

    assert "Status: SHADOW_TEST_INVALID_NO_RECENT_TICK_ACTIVITY" in report
    assert "24h Market Data Health" in report
    assert "Live quotes processed: 100" in report
    assert "Qualified candidates: 3" in report
    assert "Order fills: 0" in report
    assert "Order rejections: 1" in report
    assert "The latest run had no fresh quotes, but the full day had live data" in report
    assert "No qualified candidate completed the full pipeline" not in report


def test_daily_report_groups_run_summaries_by_session() -> None:
    events = [
        _event(
            datetime(2026, 5, 29, 22, 10, tzinfo=timezone.utc),
            "RUN_SUMMARY",
            "INFO",
            symbol="GOLD.i#",
            mode="SHADOW_ONLY_NO_ORDER",
            live_quotes_processed=10,
            live_sweeps_detected=2,
        ),
        _event(
            datetime(2026, 5, 29, 12, 10, tzinfo=timezone.utc),
            "RUN_SUMMARY",
            "INFO",
            symbol="GOLD.i#",
            mode="SHADOW_ONLY_NO_ORDER",
            live_quotes_processed=5,
            confirmation_blocks=1,
        ),
    ]

    report = DailyReportBuilder(events, lookback_hours=24).build_text_report()

    assert "ASIAN_ACCUMULATION: runs=1 quotes=10 sweeps=2" in report
    assert "NEWYORK_KILLZONE: runs=1 quotes=5 sweeps=0" in report


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
