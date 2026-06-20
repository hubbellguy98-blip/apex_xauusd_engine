from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from reports.ai.ai_report_writer import write_ai_summary
from reports.cross_verifier import verify_report
from reports.data_loader import load_report_inputs
from reports.delivery.delivery_logger import append_delivery_log
from reports.delivery.telegram_report_sender import send_weekly_report
from reports.manual_chart_review import build_manual_chart_review
from reports.metrics_calculator import calculate_metrics
from reports.report_config import ReportingConfig
from reports.report_renderer import write_report_files
from reports.timestamp_normalizer import normalize_trade_timestamps


def _iso_week_period(day: date) -> tuple[str, date, date]:
    start = day - timedelta(days=day.weekday())
    end = start + timedelta(days=6)
    iso = start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}", start, end


def resolve_period(args: argparse.Namespace, display_timezone: str) -> tuple[str, datetime, datetime]:
    tz = ZoneInfo(display_timezone)
    today = datetime.now(tz).date()
    if args.start and args.end:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
        period = f"{start.isoformat()}_to_{end.isoformat()}"
    elif args.week == "previous":
        period, start, end = _iso_week_period(today - timedelta(days=7))
    else:
        period, start, end = _iso_week_period(today)
    return (
        period,
        datetime.combine(start, time.min, tzinfo=tz).astimezone(timezone.utc),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc),
    )


def _entry_utc(row: dict[str, str]) -> datetime | None:
    value = row.get("entry_time_utc_normalized", "")
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _filter_period(rows: list[dict[str, str]], start_utc: datetime, end_utc: datetime) -> list[dict[str, str]]:
    filtered: list[dict[str, str]] = []
    for row in rows:
        entry = _entry_utc(row)
        if entry is None or start_utc <= entry < end_utc:
            filtered.append(row)
    return filtered


def run_weekly_report(config: ReportingConfig, period: str, start_utc: datetime, end_utc: datetime) -> dict:
    if not config.reporting_enabled:
        return {"success": False, "exit_code": 2, "error": "reporting_disabled"}

    loaded = load_report_inputs(config)
    input_warnings = [warning for item in loaded.values() for warning in item.warnings]
    timestamp_issues: list[dict] = []
    normalized_rows: list[dict[str, str]] = []
    for row in loaded["trades"].rows:
        normalized, issues = normalize_trade_timestamps(row, config)
        normalized_rows.append(normalized)
        timestamp_issues.extend(issues)

    normalized_rows = _filter_period(normalized_rows, start_utc, end_utc)
    metrics = calculate_metrics(normalized_rows, config, loaded["equity"].rows)
    verification = verify_report(loaded["trades"].rows, normalized_rows, config, timestamp_issues, input_warnings)
    manual_rows = build_manual_chart_review(normalized_rows)
    ai_summary, ai_result = write_ai_summary(period, metrics, verification, config)
    output_dir = config.output_dir / "weekly" / period
    manifest = {
        "period": period,
        "start_utc": start_utc.isoformat(),
        "end_utc": end_utc.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {name: {"path": str(item.path) if item.path else "", "exists": item.exists, "rows": len(item.rows)} for name, item in loaded.items()},
        "config": {
            "display_timezone": config.display_timezone,
            "broker_timezone": config.broker_timezone,
            "symbol_filter": config.symbol_filter,
            "minimum_planned_rr": config.minimum_planned_rr,
            "ai_enabled": config.ai_enabled,
            "telegram_enabled": config.telegram_enabled,
        },
        "ai": ai_result,
        "read_only": True,
    }
    paths = write_report_files(output_dir, period, metrics, verification, ai_summary, manual_rows, normalized_rows, manifest, config)
    telegram_result = send_weekly_report(period, paths, ai_summary, config)
    delivery_log = append_delivery_log(config.output_dir, period, "telegram", telegram_result, output_dir)
    manifest["telegram"] = telegram_result
    manifest["delivery_log"] = str(delivery_log)
    paths["manifest"].write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "success": True,
        "exit_code": 0,
        "period": period,
        "output_dir": output_dir,
        "paths": paths,
        "metrics": metrics,
        "verification": verification,
        "ai": ai_result,
        "telegram": telegram_result,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a read-only weekly Apex trading report.")
    parser.add_argument("--week", choices=["current", "previous"], default="current")
    parser.add_argument("--start", help="Start date YYYY-MM-DD, display timezone.")
    parser.add_argument("--end", help="End date YYYY-MM-DD, display timezone.")
    parser.add_argument("--trade-log", help="Required trade log CSV path.")
    parser.add_argument("--execution-log", help="Optional execution log CSV path.")
    parser.add_argument("--broker-history", help="Optional broker history CSV path.")
    parser.add_argument("--signal-log", help="Optional signal log CSV path.")
    parser.add_argument("--risk-log", help="Optional risk log CSV path.")
    parser.add_argument("--equity-csv", help="Optional equity CSV path.")
    parser.add_argument("--output-dir", help="Report output directory.")
    parser.add_argument("--env-file", default=".env", help="Env file to read before process env.")
    parser.add_argument("--send-telegram", action="store_true", help="Send Telegram summary/files for this run.")
    parser.add_argument("--no-ai", action="store_true", help="Disable Gemini narrative for this run.")
    parser.add_argument("--no-telegram", action="store_true", help="Disable Telegram delivery for this run.")
    return parser


def config_from_args(args: argparse.Namespace) -> ReportingConfig:
    overrides = {
        "REPORT_TRADE_LOG": args.trade_log,
        "REPORT_EXECUTION_LOG": args.execution_log,
        "REPORT_BROKER_HISTORY_EXPORT": args.broker_history,
        "REPORT_SIGNAL_LOG": args.signal_log,
        "REPORT_RISK_LOG": args.risk_log,
        "REPORT_EQUITY_CSV": args.equity_csv,
        "REPORT_OUTPUT_DIR": args.output_dir,
    }
    config = ReportingConfig.from_env(Path(args.env_file) if args.env_file else None, overrides)
    if args.no_ai:
        config = replace(config, ai_enabled=False)
    if args.send_telegram:
        config = replace(config, telegram_enabled=True)
    if args.no_telegram:
        config = replace(config, telegram_enabled=False)
    return config


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = config_from_args(args)
    try:
        period, start_utc, end_utc = resolve_period(args, config.display_timezone)
        result = run_weekly_report(config, period, start_utc, end_utc)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if result["success"]:
        print(f"Weekly report generated: {result['output_dir']}")
    else:
        print(result.get("error", "weekly_report_failed"), file=sys.stderr)
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())

