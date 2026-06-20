from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path


FIELDS = [
    "run_at",
    "period",
    "channel",
    "enabled",
    "success",
    "sent_files",
    "errors",
    "output_dir",
]


def append_delivery_log(base_output_dir: Path, period: str, channel: str, result: dict, output_dir: Path) -> Path:
    log_dir = base_output_dir / "delivery_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "report_delivery_log.csv"
    exists = path.exists()
    row = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "period": period,
        "channel": channel,
        "enabled": str(result.get("enabled", False)),
        "success": str(result.get("success", False)),
        "sent_files": ";".join(result.get("sent_files", [])),
        "errors": ";".join(result.get("errors", [])),
        "output_dir": str(output_dir),
    }
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)
    return path

