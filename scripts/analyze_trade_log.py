"""Analyze Apex backtest trade CSV exports.

The script is intentionally dependency-light so a Gemini/Codex backtest artifact
can be audited on any VPS with the project virtualenv.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence


def analyze_trade_log(path: Path) -> dict[str, Any]:
    rows = _read_rows(path)
    values = [_float(row.get("realized_R")) or 0.0 for row in rows]
    return {
        "source": str(path),
        "overall": _metrics(rows),
        "by_direction": _group_metrics(rows, "direction"),
        "by_session": _group_metrics(rows, "session_name"),
        "by_killzone": _group_metrics(rows, "killzone_name"),
        "by_hour_utc": _group_metrics(rows, "_entry_hour_utc"),
        "by_component": _component_metrics(rows),
        "by_score_bucket": _score_bucket_metrics(rows),
        "by_duration_bucket": _duration_bucket_metrics(rows),
        "displacement_vs_no_displacement": _displacement_metrics(rows),
        "max_drawdown_R": _max_drawdown(values),
        "max_win_streak": _streak(values, positive=True),
        "max_loss_streak": _streak(values, positive=False),
        "warnings": _warnings(rows),
    }


def write_outputs(analysis: Mapping[str, Any], output_prefix: Path) -> dict[str, Path]:
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = output_prefix.with_suffix(".json")
    md_path = output_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(analysis, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_markdown(analysis), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}


def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            data = dict(row)
            data["_entry_hour_utc"] = _hour_utc(data.get("entry_time"))
            rows.append(data)
    return rows


def _metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = [_float(row.get("realized_R")) or 0.0 for row in rows]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(values), 5) if values else 0.0,
        "net_R": round(sum(values), 5),
        "expectancy_R": round(mean(values), 5) if values else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 5) if gross_loss else None,
        "max_drawdown_R": _max_drawdown(values),
    }


def _group_metrics(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "None")].append(row)
    return {name: _metrics(items) for name, items in sorted(groups.items())}


def _component_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        components = str(row.get("components") or "")
        for token in ("sweep", "mss", "fvg", "displacement", "order_block", "breaker", "news"):
            if token in components.lower():
                groups[token].append(row)
    return {name: _metrics(items) for name, items in sorted(groups.items())}


def _score_bucket_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        score = _float(row.get("confidence_score"))
        if score is None:
            continue
        lower = int(score // 5 * 5)
        groups[f"{lower}-{lower + 4}"].append(row)
    return {name: _metrics(items) for name, items in sorted(groups.items())}


def _duration_bucket_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        minutes = _float(row.get("duration_min")) or 0.0
        if minutes <= 30:
            bucket = "0-30m"
        elif minutes <= 90:
            bucket = "31-90m"
        elif minutes <= 180:
            bucket = "91-180m"
        elif minutes <= 1440:
            bucket = "181m-1d"
        else:
            bucket = "over_1d"
        groups[bucket].append(row)
    return {name: _metrics(items) for name, items in sorted(groups.items())}


def _displacement_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups = {"displacement": [], "no_displacement": []}
    for row in rows:
        text = f"{row.get('components', '')} {row.get('displacement_diagnostics', '')}".lower()
        groups["displacement" if "displacement" in text else "no_displacement"].append(row)
    return {name: _metrics(items) for name, items in groups.items()}


def _warnings(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    warnings: list[str] = []
    overall = _metrics(rows)
    if overall["profit_factor"] is None or overall["profit_factor"] < 1.2:
        warnings.append("profit_factor_below_1_2")
    if overall["expectancy_R"] <= 0:
        warnings.append("expectancy_not_positive")
    if any((_float(row.get("post_cost_rr")) or 0.0) < 3.0 for row in rows):
        warnings.append("post_cost_rr_below_3_present")
    if any((_float(row.get("duration_min")) or 0.0) > 240 for row in rows):
        warnings.append("duration_outliers_present")
    score_buckets = _score_bucket_metrics(rows)
    ordered = sorted((int(name.split("-", 1)[0]), data["expectancy_R"]) for name, data in score_buckets.items())
    if any(ordered[index][1] + 0.05 < ordered[index - 1][1] for index in range(1, len(ordered))):
        warnings.append("score_buckets_non_monotonic")
    return warnings


def _markdown(analysis: Mapping[str, Any]) -> str:
    lines = ["# Apex Trade Log Analysis", "", "## Overall", _metrics_line(analysis.get("overall", {})), ""]
    for title, key in (
        ("Direction", "by_direction"),
        ("Session", "by_session"),
        ("Killzone", "by_killzone"),
        ("Hour UTC", "by_hour_utc"),
        ("Score Bucket", "by_score_bucket"),
        ("Duration Bucket", "by_duration_bucket"),
        ("Displacement", "displacement_vs_no_displacement"),
    ):
        lines.extend([f"## By {title}"])
        for name, metrics in dict(analysis.get(key, {})).items():
            lines.append(f"- {name}: {_metrics_line(metrics)}")
        lines.append("")
    lines.extend(["## Warnings", *(f"- {item}" for item in analysis.get("warnings", []))])
    return "\n".join(lines) + "\n"


def _metrics_line(metrics: Mapping[str, Any]) -> str:
    return (
        f"trades={metrics.get('trades', 0)} wins={metrics.get('wins', 0)} "
        f"losses={metrics.get('losses', 0)} net_R={metrics.get('net_R', 0.0)} "
        f"PF={metrics.get('profit_factor')} expectancy={metrics.get('expectancy_R', 0.0)} "
        f"DD={metrics.get('max_drawdown_R', 0.0)}"
    )


def _hour_utc(value: Any) -> str:
    if not value:
        return "None"
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return "None"
    return f"{parsed.hour:02d}:00"


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _max_drawdown(values: Sequence[float]) -> float:
    equity = peak = max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return round(abs(max_dd), 5)


def _streak(values: Sequence[float], *, positive: bool) -> int:
    best = current = 0
    for value in values:
        hit = value > 0 if positive else value < 0
        current = current + 1 if hit else 0
        best = max(best, current)
    return best


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze an Apex backtest trade CSV.")
    parser.add_argument("trade_csv")
    parser.add_argument("--output-prefix")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.trade_csv)
    analysis = analyze_trade_log(path)
    prefix = Path(args.output_prefix) if args.output_prefix else path.with_name(f"{path.stem}_analysis")
    outputs = write_outputs(analysis, prefix)
    print(f"analysis_json={outputs['json']}")
    print(f"analysis_markdown={outputs['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
