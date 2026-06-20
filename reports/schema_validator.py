from __future__ import annotations

from reports.report_config import ReportingConfig

CRITICAL_FIELDS = {
    "trade_id": ("trade_id", "id"),
    "symbol": ("symbol",),
    "direction": ("direction",),
    "entry_time": ("entry_time", "entry_time_utc", "entry_time_broker", "entry_time_ist", "broker_execution_time"),
}


def validate_trade_rows(rows: list[dict[str, str]], config: ReportingConfig) -> list[dict]:
    issues: list[dict] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        trade_id = row.get("trade_id") or row.get("id") or f"row_{index}"
        for field, aliases in CRITICAL_FIELDS.items():
            if not any((row.get(alias) or "").strip() for alias in aliases):
                issues.append(
                    {
                        "severity": "high" if field in {"trade_id", "entry_time"} else "warning",
                        "category": "schema",
                        "code": "missing_required_trade_field",
                        "field": field,
                        "trade_id": trade_id,
                    }
                )
        if trade_id in seen:
            issues.append(
                {
                    "severity": "high",
                    "category": "schema",
                    "code": "duplicate_trade_id",
                    "trade_id": trade_id,
                }
            )
        seen.add(trade_id)
        if config.symbol_filter and row.get("symbol") and row.get("symbol") != config.symbol_filter:
            issues.append(
                {
                    "severity": "warning",
                    "category": "schema",
                    "code": "symbol_filter_mismatch",
                    "trade_id": trade_id,
                    "symbol": row.get("symbol"),
                    "expected": config.symbol_filter,
                }
            )
    return issues
