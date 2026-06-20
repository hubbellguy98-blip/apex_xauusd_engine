from __future__ import annotations

from reports.metrics_calculator import as_float
from reports.report_config import ReportingConfig
from reports.schema_validator import validate_trade_rows


def _severity_rank(severity: str) -> int:
    return {"info": 0, "warning": 1, "high": 2, "critical": 3}.get(severity, 0)


def verify_report(
    raw_rows: list[dict[str, str]],
    normalized_rows: list[dict[str, str]],
    config: ReportingConfig,
    timestamp_issues: list[dict],
    input_warnings: list[str],
) -> dict:
    issues = list(timestamp_issues)
    issues.extend(validate_trade_rows(raw_rows, config))
    for warning in input_warnings:
        issues.append({"severity": "info", "category": "input", "code": warning})

    for index, row in enumerate(normalized_rows, start=1):
        trade_id = row.get("trade_id") or row.get("id") or f"row_{index}"
        if row.get("result") and "open" not in row.get("result", "").lower() and not (row.get("exit_price") or row.get("exit")):
            issues.append({"severity": "high", "category": "trade", "code": "closed_trade_missing_exit_price", "trade_id": trade_id})
        if not (row.get("stop_loss") or row.get("stop")):
            issues.append({"severity": "warning", "category": "trade", "code": "missing_stop_loss", "trade_id": trade_id})
        if not (row.get("take_profit") or row.get("target_1") or row.get("tp1")):
            issues.append({"severity": "warning", "category": "trade", "code": "missing_take_profit", "trade_id": trade_id})
        if not (row.get("broker_execution_time") or row.get("entry_time_utc") or row.get("entry_time")):
            issues.append({"severity": "warning", "category": "execution", "code": "missing_broker_execution_time", "trade_id": trade_id})

        direction = (row.get("direction") or "").lower()
        entry = as_float(row.get("entry_price", row.get("entry", "")), None)
        exit_price = as_float(row.get("exit_price", row.get("exit", "")), None)
        stop = as_float(row.get("stop_loss", row.get("stop", "")), None)
        pnl = as_float(row.get("pnl", row.get("profit", "")), None)
        expected_pnl = as_float(row.get("expected_pnl", row.get("pnl_recalculated", "")), None)
        if pnl is not None and expected_pnl is not None:
            if abs(pnl - expected_pnl) > config.pnl_tolerance:
                issues.append({"severity": "warning", "category": "pnl", "code": "pnl_recalculation_mismatch", "trade_id": trade_id})
        if entry is not None and exit_price is not None and stop is not None and row.get("rr_actual"):
            risk = abs(entry - stop)
            expected_rr = 0.0 if risk == 0 else (exit_price - entry if direction.startswith("buy") or direction.startswith("long") else entry - exit_price) / risk
            if abs(as_float(row.get("rr_actual")) - expected_rr) > 0.05:
                issues.append({"severity": "warning", "category": "rr", "code": "rr_recalculation_mismatch", "trade_id": trade_id})

    worst = max((_severity_rank(issue.get("severity", "info")) for issue in issues), default=0)
    status = "FAILED" if worst >= 2 else "WARNING" if issues else "PASSED"
    confidence = "LOW" if status == "FAILED" else "MEDIUM" if status == "WARNING" else "HIGH"
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue["code"]] = counts.get(issue["code"], 0) + 1
    return {
        "status": status,
        "confidence": confidence,
        "row_count": len(raw_rows),
        "issue_count": len(issues),
        "issue_counts": counts,
        "issues": issues,
        "checks": {
            "duplicate_trade_ids": counts.get("duplicate_trade_id", 0),
            "invalid_timestamps": counts.get("invalid_timestamp", 0),
            "exit_before_entry": counts.get("exit_before_entry", 0),
            "pnl_mismatches": counts.get("pnl_recalculation_mismatch", 0),
            "rr_mismatches": counts.get("rr_recalculation_mismatch", 0),
            "missing_optional_inputs": sum(1 for code in counts if code.endswith("_missing_skipped")),
        },
    }
