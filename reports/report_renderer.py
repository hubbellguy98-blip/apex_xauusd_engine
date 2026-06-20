from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from reports.report_config import ReportingConfig


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    headers = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def render_markdown(period: str, metrics: dict, verification: dict, ai_summary: str, config: ReportingConfig) -> str:
    return f"""# Weekly Trading Report {period}

## 1. Executive Summary
{ai_summary}

## 2. Data Integrity
- Verification status: {verification["status"]}
- Confidence: {verification["confidence"]}
- Issues found: {verification["issue_count"]}

## 3. Trading Performance
- Trades: {metrics["trade_count"]}
- Net PnL: {metrics["net_pnl"]}
- Profit factor: {metrics["profit_factor"]}
- Win rate: {metrics["win_rate"]}
- Expectancy: {metrics["expectancy"]}

## 4. Risk And RR
- Average RR: {metrics["avg_rr"]}
- Best RR: {metrics["best_rr"]}
- Worst RR: {metrics["worst_rr"]}
- Below configured RR minimum: {metrics["rr_compliance"]["post_cost_rr_below_profile_minimum"]}

## 5. Setup Performance
```json
{json.dumps(metrics["by_setup"], indent=2)}
```

## 6. Session Performance
```json
{json.dumps(metrics["by_session"], indent=2)}
```

## 7. Timeframe Performance
```json
{json.dumps(metrics["by_timeframe"], indent=2)}
```

## 8. Direction Performance
```json
{json.dumps(metrics["by_direction"], indent=2)}
```

## 9. Execution Quality
Execution quality is reported when execution timestamps and broker fields exist in the input CSVs.

## 10. Manual Chart Review Queue
Use the generated manual chart review CSV for exact M1/M3/M5/M15 candle checks.

## 11. Verification Issues
```json
{json.dumps(verification["issue_counts"], indent=2)}
```

## 12. Deployment Note
This report is read-only. It does not change execution, risk, SL/TP, MT5, or strategy behavior.
"""


def write_report_files(
    output_dir: Path,
    period: str,
    metrics: dict,
    verification: dict,
    ai_summary: str,
    manual_rows: list[dict[str, str]],
    trade_rows: list[dict[str, str]],
    manifest: dict,
    config: ReportingConfig,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md = render_markdown(period, metrics, verification, ai_summary, config)
    html_text = f"<html><body><pre>{html.escape(md)}</pre></body></html>"
    paths = {
        "markdown": output_dir / f"weekly_report_{period}.md",
        "html": output_dir / f"weekly_report_{period}.html",
        "ai_summary": output_dir / f"weekly_ai_summary_{period}.txt",
        "metrics": output_dir / f"weekly_metrics_{period}.json",
        "verification": output_dir / f"weekly_verification_{period}.json",
        "manual_chart_review": output_dir / f"weekly_manual_chart_review_{period}.csv",
        "trade_summary": output_dir / f"weekly_trade_summary_{period}.csv",
        "manifest": output_dir / f"report_manifest_{period}.json",
    }
    paths["markdown"].write_text(md, encoding="utf-8")
    paths["html"].write_text(html_text, encoding="utf-8")
    paths["ai_summary"].write_text(ai_summary, encoding="utf-8")
    _write_json(paths["metrics"], metrics)
    _write_json(paths["verification"], verification)
    _write_json(paths["manifest"], manifest)
    _write_csv(paths["manual_chart_review"], manual_rows)
    _write_csv(paths["trade_summary"], trade_rows)
    return paths

