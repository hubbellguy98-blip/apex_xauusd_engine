from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from reports.report_config import ReportingConfig


@dataclass
class LoadedCsv:
    name: str
    path: Path | None
    rows: list[dict[str, str]]
    headers: list[str]
    warnings: list[str]
    exists: bool


def load_csv(name: str, path: Path | None, required: bool = False) -> LoadedCsv:
    warnings: list[str] = []
    if path is None:
        if required:
            warnings.append(f"{name}_path_missing")
        return LoadedCsv(name, path, [], [], warnings, False)
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Required {name} file not found: {path}")
        warnings.append(f"{name}_missing_skipped")
        return LoadedCsv(name, path, [], [], warnings, False)
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = [{k: (v or "") for k, v in row.items()} for row in reader]
        headers = list(reader.fieldnames or [])
    if not rows:
        warnings.append(f"{name}_empty")
    return LoadedCsv(name, path, rows, headers, warnings, True)


def load_report_inputs(config: ReportingConfig) -> dict[str, LoadedCsv]:
    return {
        "trades": load_csv("trade_log", config.trade_log_path, required=True),
        "execution": load_csv("execution_log", config.execution_log_path),
        "broker_history": load_csv("broker_history_export", config.broker_history_path),
        "signals": load_csv("signal_log", config.signal_log_path),
        "risk": load_csv("risk_log", config.risk_log_path),
        "equity": load_csv("equity_csv", config.equity_csv_path),
    }

