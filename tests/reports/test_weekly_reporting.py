from __future__ import annotations

import csv
import json
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

from reports.ai.ai_report_writer import FALLBACK_SUMMARY, write_ai_summary
from reports.candle_mapper import map_time_to_candles
from reports.cross_verifier import verify_report
from reports.delivery.delivery_logger import append_delivery_log
from reports.delivery.telegram_client import TelegramClient
from reports.delivery.telegram_report_sender import send_weekly_report
from reports.metrics_calculator import calculate_metrics
from reports.report_config import ReportingConfig
from reports.timestamp_normalizer import normalize_trade_timestamps
from reports.weekly_report_runner import config_from_args, resolve_period, run_weekly_report


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _sample_rows() -> list[dict[str, str]]:
    return [
        {
            "trade_id": "T1",
            "symbol": "GOLD.i#",
            "direction": "buy",
            "entry_time": "2026-06-16 10:00:00",
            "exit_time": "2026-06-16 10:30:00",
            "entry_price": "2300",
            "exit_price": "2302",
            "stop_loss": "2299",
            "take_profit": "2303",
            "lot_size": "1",
            "result": "win",
            "exit_reason": "tp",
            "pnl": "2",
            "rr_planned": "3",
            "rr_actual": "2",
            "post_cost_rr": "3.0",
            "strategy_setup": "sweep_mss_fvg",
            "session": "NY Open",
            "timeframe": "M5",
        },
        {
            "trade_id": "T2",
            "symbol": "GOLD.i#",
            "direction": "sell",
            "entry_time": "2026-06-17 11:00:00",
            "exit_time": "2026-06-17 11:10:00",
            "entry_price": "2310",
            "exit_price": "2311",
            "stop_loss": "2312",
            "take_profit": "2307",
            "lot_size": "1",
            "result": "loss",
            "exit_reason": "sl",
            "pnl": "-1",
            "rr_planned": "2",
            "rr_actual": "-0.5",
            "post_cost_rr": "1.8",
            "strategy_setup": "sweep_mss_fvg",
            "session": "London Open",
            "timeframe": "M1",
        },
    ]


def test_candle_mapping_exact_floors() -> None:
    mapped = map_time_to_candles(datetime(2026, 6, 20, 15, 42, 11, tzinfo=timezone.utc))
    assert mapped["M1"].endswith("15:42:00+00:00")
    assert mapped["M3"].endswith("15:42:00+00:00")
    assert mapped["M5"].endswith("15:40:00+00:00")
    assert mapped["M15"].endswith("15:30:00+00:00")


def test_timestamp_normalization_naive_warning_and_display_timezone() -> None:
    config = ReportingConfig(display_timezone="Asia/Kolkata", broker_timezone="UTC")
    row, issues = normalize_trade_timestamps(_sample_rows()[0], config)
    assert row["entry_time_display"].endswith("+05:30")
    assert any(issue["code"] == "naive_timestamp_assumed_source_timezone" for issue in issues)
    assert row["duration_minutes"] == "30.00"


def test_metrics_include_rr_distribution_and_compliance() -> None:
    metrics = calculate_metrics(_sample_rows(), ReportingConfig(minimum_planned_rr=2.0))
    assert metrics["trade_count"] == 2
    assert metrics["wins"] == 1
    assert metrics["losses"] == 1
    assert metrics["post_cost_rr_distribution"]["<2R"] == 1
    assert metrics["post_cost_rr_distribution"]["3R+"] == 1
    assert metrics["rr_compliance"]["post_cost_rr_below_profile_minimum"] == 1
    assert metrics["by_session"]["London Open"]["trades"] == 1


def test_verifier_detects_duplicate_invalid_exit_and_mismatches() -> None:
    rows = _sample_rows()
    bad = dict(rows[0])
    bad.update(
        {
            "trade_id": "T1",
            "entry_time": "not-a-time",
            "exit_time": "2026-06-15 09:00:00",
            "pnl": "999",
            "expected_pnl": "2",
            "rr_actual": "99",
        }
    )
    normalized = []
    issues = []
    config = ReportingConfig()
    for row in rows + [bad]:
        normalized_row, timestamp_issues = normalize_trade_timestamps(row, config)
        normalized.append(normalized_row)
        issues.extend(timestamp_issues)
    verification = verify_report(rows + [bad], normalized, config, issues, ["execution_log_missing_skipped"])
    assert verification["status"] == "FAILED"
    assert verification["issue_counts"]["duplicate_trade_id"] == 1
    assert verification["issue_counts"]["invalid_timestamp"] >= 1
    assert verification["issue_counts"]["pnl_recalculation_mismatch"] >= 1
    assert verification["issue_counts"]["rr_recalculation_mismatch"] >= 1
    assert verification["checks"]["missing_optional_inputs"] == 1


def test_weekly_runner_generates_required_files_without_ai_or_telegram(tmp_path: Path) -> None:
    trade_log = tmp_path / "trade_log.csv"
    _write_csv(trade_log, _sample_rows())
    output_dir = tmp_path / "out"
    config = ReportingConfig(
        trade_log_path=trade_log,
        output_dir=output_dir,
        broker_timezone="UTC",
        display_timezone="Asia/Kolkata",
        ai_enabled=False,
        telegram_enabled=False,
    )
    result = run_weekly_report(
        config,
        "2026-W25",
        datetime(2026, 6, 15, tzinfo=timezone.utc),
        datetime(2026, 6, 22, tzinfo=timezone.utc),
    )
    assert result["success"] is True
    expected = {
        "markdown",
        "html",
        "ai_summary",
        "metrics",
        "verification",
        "manual_chart_review",
        "trade_summary",
        "manifest",
    }
    assert expected.issubset(result["paths"])
    for key in expected:
        assert result["paths"][key].exists()
    manifest = json.loads(result["paths"]["manifest"].read_text(encoding="utf-8"))
    assert manifest["read_only"] is True
    assert manifest["telegram"]["errors"] == ["telegram_disabled"]
    assert (output_dir / "delivery_logs" / "report_delivery_log.csv").exists()


def test_manual_chart_review_contains_candle_columns(tmp_path: Path) -> None:
    trade_log = tmp_path / "trade_log.csv"
    _write_csv(trade_log, _sample_rows())
    result = run_weekly_report(
        ReportingConfig(trade_log_path=trade_log, output_dir=tmp_path / "out", ai_enabled=False, telegram_enabled=False),
        "2026-W25",
        datetime(2026, 6, 15, tzinfo=timezone.utc),
        datetime(2026, 6, 22, tzinfo=timezone.utc),
    )
    with result["paths"]["manual_chart_review"].open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["entry_m1_candle"]
    assert rows[0]["entry_m3_candle"]
    assert rows[0]["entry_m5_candle"]
    assert rows[0]["entry_m15_candle"]
    assert rows[0]["review_status"] == "pending_manual_review"


def test_ai_writer_uses_fallback_without_key_and_fake_success() -> None:
    summary, result = write_ai_summary("2026-W25", {"trade_count": 1}, {"status": "PASSED"}, ReportingConfig(ai_enabled=True))
    assert summary == FALLBACK_SUMMARY
    assert result["error"] == "missing_gemini_api_key"

    class FakeClient:
        def generate(self, prompt: str) -> dict:
            assert "Do not calculate" in prompt
            return {"success": True, "text": "Verified narrative only.", "error": None}

    summary, result = write_ai_summary(
        "2026-W25",
        {"trade_count": 1},
        {"status": "PASSED"},
        ReportingConfig(ai_enabled=True, gemini_api_key="key"),
        client=FakeClient(),
    )
    assert summary == "Verified narrative only."
    assert result["success"] is True


def test_telegram_sender_handles_missing_credentials_and_fake_delivery(tmp_path: Path) -> None:
    report = tmp_path / "weekly_report.md"
    report.write_text("report", encoding="utf-8")
    missing = send_weekly_report("2026-W25", {"markdown": report}, "summary", ReportingConfig(telegram_enabled=True))
    assert missing["errors"] == ["telegram_credentials_missing"]

    client = TelegramClient(
        "token",
        "chat",
        transport=lambda url, body, headers, timeout: {"success": True, "response": {"ok": True}, "error": None},
    )
    sent = send_weekly_report(
        "2026-W25",
        {"markdown": report},
        "summary",
        ReportingConfig(telegram_enabled=True, telegram_bot_token="token", telegram_chat_id="chat"),
        client=client,
    )
    assert sent["success"] is True
    assert sent["sent_files"] == ["weekly_report.md"]


def test_telegram_sender_logs_file_too_large(tmp_path: Path) -> None:
    report = tmp_path / "large.md"
    report.write_text("too large", encoding="utf-8")
    sent = send_weekly_report(
        "2026-W25",
        {"markdown": report},
        "summary",
        ReportingConfig(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
            telegram_send_summary=False,
            telegram_max_file_size_mb=0.000001,
        ),
    )
    assert sent["success"] is False
    assert sent["errors"] == ["file_too_large:large.md"]


def test_delivery_log_appends_expected_row(tmp_path: Path) -> None:
    path = append_delivery_log(
        tmp_path,
        "2026-W25",
        "telegram",
        {"enabled": True, "success": False, "sent_files": ["a.md"], "errors": ["boom"]},
        tmp_path / "weekly" / "2026-W25",
    )
    rows = list(csv.DictReader(path.open("r", newline="", encoding="utf-8")))
    assert rows[0]["period"] == "2026-W25"
    assert rows[0]["errors"] == "boom"


def test_config_from_args_and_period_resolution(tmp_path: Path) -> None:
    args = Namespace(
        trade_log=str(tmp_path / "trades.csv"),
        execution_log=None,
        broker_history=None,
        signal_log=None,
        risk_log=None,
        equity_csv=None,
        output_dir=str(tmp_path / "out"),
        env_file=None,
        no_ai=True,
        send_telegram=True,
        no_telegram=True,
        week="previous",
        start="2026-06-15",
        end="2026-06-21",
    )
    config = config_from_args(args)
    assert config.trade_log_path == tmp_path / "trades.csv"
    assert config.ai_enabled is False
    assert config.telegram_enabled is False
    period, start_utc, end_utc = resolve_period(args, "Asia/Kolkata")
    assert period == "2026-06-15_to_2026-06-21"
    assert start_utc < end_utc
