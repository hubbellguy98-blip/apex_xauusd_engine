from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reports.report_config import ReportingConfig


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def parse_timestamp(value: str, source_tz: str, field: str, trade_id: str = "") -> tuple[datetime | None, list[dict]]:
    issues: list[dict] = []
    text = (value or "").strip()
    if not text:
        return None, issues
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            issues.append(
                {
                    "severity": "high",
                    "category": "timestamp",
                    "code": "invalid_timestamp",
                    "trade_id": trade_id,
                    "field": field,
                    "value": value,
                }
            )
            return None, issues
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_zone(source_tz))
        issues.append(
            {
                "severity": "warning",
                "category": "timestamp",
                "code": "naive_timestamp_assumed_source_timezone",
                "trade_id": trade_id,
                "field": field,
                "source_timezone": source_tz,
            }
        )
    return dt.astimezone(timezone.utc), issues


def normalize_trade_timestamps(row: dict[str, str], config: ReportingConfig) -> tuple[dict, list[dict]]:
    trade_id = row.get("trade_id") or row.get("id") or ""
    display_tz = _zone(config.display_timezone)
    broker_tz = _zone(config.broker_timezone)
    issues: list[dict] = []

    entry_raw = row.get("entry_time_utc") or row.get("entry_time") or row.get("broker_execution_time") or row.get("order_send_time")
    exit_raw = row.get("exit_time_utc") or row.get("exit_time")
    entry_utc, entry_issues = parse_timestamp(entry_raw, config.broker_timezone, "entry_time", trade_id)
    exit_utc, exit_issues = parse_timestamp(exit_raw, config.broker_timezone, "exit_time", trade_id)
    issues.extend(entry_issues)
    issues.extend(exit_issues)
    if entry_utc and exit_utc and exit_utc < entry_utc:
        issues.append(
            {
                "severity": "high",
                "category": "timestamp",
                "code": "exit_before_entry",
                "trade_id": trade_id,
            }
        )

    normalized = dict(row)
    if entry_utc:
        normalized["entry_time_utc_normalized"] = entry_utc.isoformat()
        normalized["entry_time_broker_normalized"] = entry_utc.astimezone(broker_tz).isoformat()
        normalized["entry_time_display"] = entry_utc.astimezone(display_tz).isoformat()
    if exit_utc:
        normalized["exit_time_utc_normalized"] = exit_utc.isoformat()
        normalized["exit_time_broker_normalized"] = exit_utc.astimezone(broker_tz).isoformat()
        normalized["exit_time_display"] = exit_utc.astimezone(display_tz).isoformat()
    if entry_utc and exit_utc:
        normalized["duration_minutes"] = f"{(exit_utc - entry_utc).total_seconds() / 60.0:.2f}"
    return normalized, issues

