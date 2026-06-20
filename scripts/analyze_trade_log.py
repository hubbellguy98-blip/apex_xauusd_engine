"""Analyze Apex backtest trade CSV exports.

The analyzer accepts both current rich CSVs and older Gemini/Codex exports with
slightly different column names. It reports performance, profile compliance,
score calibration, and exclusion simulations without hardcoding any single run.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Mapping, Sequence


ALIASES = {
    "entry_time": ("entry_time", "entry_dt", "time"),
    "exit_reason": ("final_exit_reason", "exit_reason"),
    "target_1": ("target_1", "tp1"),
    "target_2": ("target_2", "tp2"),
    "entry_price": ("entry_price", "entry"),
    "exit_price": ("exit_price", "exit"),
    "stop_loss": ("stop_loss", "stop"),
    "score": ("confidence_score", "score", "setup_score"),
}


def analyze_trade_log(path: Path) -> dict[str, Any]:
    rows, headers = _read_rows(path)
    values = [_float(row.get("realized_R")) or 0.0 for row in rows]
    return {
        "source": str(path),
        "overall": _metrics(rows),
        "by_direction": _group_metrics(rows, "direction"),
        "by_session": _group_metrics(rows, "session_name"),
        "by_killzone": _group_metrics(rows, "killzone_name"),
        "by_hour_utc": _group_metrics(rows, "_entry_hour_utc"),
        "by_component_token": _component_token_metrics(rows),
        "by_component_combo": _component_combo_metrics(rows),
        "by_score_bucket": _score_bucket_metrics(rows),
        "score_calibration": _score_calibration(rows),
        "by_duration_bucket": _duration_bucket_metrics(rows),
        "early_exit_0_15m": _metrics([row for row in rows if (_float(row.get("duration_min")) or 0.0) <= 15.0]),
        "post_cost_rr_distribution": _rr_distribution(rows),
        "displacement_breakdown": _displacement_metrics(rows),
        "profile_compliance": _profile_compliance(rows, headers),
        "strict_profile_violations": _strict_profile_violations(rows),
        "top_losing_combinations": _top_losing_combinations(rows),
        "exclusion_simulations": {
            "exclude_london_open": _exclude_simulation(rows, lambda row: str(row.get("killzone_name") or "").lower() == "london open"),
            "exclude_displacement_tagged": _exclude_simulation(rows, _has_displacement_tag),
            "exclude_0_15m": _exclude_simulation(rows, lambda row: (_float(row.get("duration_min")) or 0.0) <= 15.0),
        },
        "max_drawdown_R": _max_drawdown(values),
        "max_win_streak": _streak(values, positive=True),
        "max_loss_streak": _streak(values, positive=False),
        "warnings": _warnings(rows, headers),
    }


def write_outputs(analysis: Mapping[str, Any], output_prefix: Path) -> dict[str, Path]:
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = output_prefix.with_suffix(".json")
    md_path = output_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(analysis, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_markdown(analysis), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}


def _read_rows(path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = set(reader.fieldnames or [])
        for raw in reader:
            data = dict(raw)
            for canonical, names in ALIASES.items():
                data.setdefault(canonical, _first_value(data, *names))
            data["_score"] = _float(_first_value(data, *ALIASES["score"]))
            data["_entry_hour_utc"] = _hour_utc(data.get("entry_time"))
            rows.append(data)
    return rows, headers


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


def _component_token_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        components = _component_text(row)
        for token in ("sweep", "mss", "fvg", "displacement", "order_block", "breaker", "news"):
            if token in components:
                groups[token].append(row)
    return {name: _metrics(items) for name, items in sorted(groups.items())}


def _component_combo_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_component_text(row) or "None"].append(row)
    return {name: _metrics(items) for name, items in sorted(groups.items())}


def _score_bucket_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        score = _float(row.get("_score"))
        if score is None:
            continue
        lower = int(score // 5 * 5)
        groups[f"{lower}-{lower + 4}"].append(row)
    return {name: _metrics(items) for name, items in sorted(groups.items())}


def _score_calibration(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    buckets = _score_bucket_metrics(rows)
    ordered = sorted((int(name.split("-", 1)[0]), metrics) for name, metrics in buckets.items())
    non_monotonic = any(
        ordered[index][1]["expectancy_R"] + 0.05 < ordered[index - 1][1]["expectancy_R"]
        for index in range(1, len(ordered))
    )
    return {"buckets": buckets, "non_monotonic": non_monotonic}


def _duration_bucket_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        minutes = _float(row.get("duration_min")) or 0.0
        if minutes <= 15:
            bucket = "0-15m"
        elif minutes <= 30:
            bucket = "16-30m"
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


def _rr_distribution(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = [value for value in (_float(row.get("post_cost_rr")) for row in rows) if value is not None]
    values_sorted = sorted(values)
    return {
        "count": len(values),
        "below_3_count": sum(1 for value in values if value < 3.0),
        "average": round(mean(values), 5) if values else None,
        "min": values_sorted[0] if values_sorted else None,
        "max": values_sorted[-1] if values_sorted else None,
    }


def _displacement_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups = {"displacement_pass": [], "displacement_fail": [], "displacement_missing": [], "component_contains_displacement": []}
    for row in rows:
        diagnostics = str(row.get("displacement_diagnostics") or "")
        lower = diagnostics.lower()
        if _has_displacement_tag(row):
            groups["component_contains_displacement"].append(row)
        if not diagnostics:
            groups["displacement_missing"].append(row)
        elif "fail" in lower or "weak" in lower or "false" in lower:
            groups["displacement_fail"].append(row)
        else:
            groups["displacement_pass"].append(row)
    return {name: _metrics(items) for name, items in groups.items()}


def _profile_compliance(rows: Sequence[Mapping[str, Any]], headers: set[str]) -> dict[str, Any]:
    return {
        "has_profile_name": "profile_name" in headers,
        "profiles": sorted({str(row.get("profile_name")) for row in rows if row.get("profile_name")}),
        "has_run_id": "run_id" in headers,
        "has_active_profile_hash": "active_profile_hash" in headers,
        "has_selector_config_hash": "selector_config_hash" in headers,
    }


def _strict_profile_violations(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    strict_rows = [row for row in rows if row.get("profile_name") == "strict_intraday_xauusd"]
    target = strict_rows or rows
    return {
        "post_cost_rr_below_3": [row.get("trade_id") for row in target if (_float(row.get("post_cost_rr")) or 0.0) < 3.0],
        "no_killzone": [row.get("trade_id") for row in strict_rows if str(row.get("killzone_active")).lower() not in {"true", "1"}],
        "disabled_killzone": [row.get("trade_id") for row in strict_rows if str(row.get("killzone_name") or "").lower() == "silver bullet pm"],
        "duration_over_180": [row.get("trade_id") for row in strict_rows if (_float(row.get("duration_min")) or 0.0) > 181.0],
    }


def _top_losing_combinations(rows: Sequence[Mapping[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        key = f"{row.get('_entry_hour_utc') or 'None'}|{row.get('killzone_name') or 'None'}|{_component_text(row) or 'None'}"
        groups[key].append(row)
    ranked = [{"combo": key, **_metrics(items)} for key, items in groups.items()]
    return sorted(ranked, key=lambda item: item["net_R"])[:limit]


def _exclude_simulation(rows: Sequence[Mapping[str, Any]], predicate: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
    kept = [row for row in rows if not predicate(row)]
    excluded = [row for row in rows if predicate(row)]
    return {"kept": _metrics(kept), "excluded": _metrics(excluded)}


def _warnings(rows: Sequence[Mapping[str, Any]], headers: set[str]) -> list[str]:
    warnings: list[str] = []
    overall = _metrics(rows)
    if "profile_name" not in headers:
        warnings.append("legacy_or_unprofiled_trade_log")
    if overall["profit_factor"] is None or overall["profit_factor"] < 1.2:
        warnings.append("profit_factor_below_1_2")
    if overall["expectancy_R"] <= 0:
        warnings.append("expectancy_not_positive")
    if any((_float(row.get("post_cost_rr")) or 0.0) < 3.0 for row in rows):
        warnings.append("post_cost_rr_below_3_present")
    if any((_float(row.get("duration_min")) or 0.0) > 180 for row in rows):
        warnings.append("duration_outliers_present")
    if _score_calibration(rows)["non_monotonic"]:
        warnings.append("score_buckets_non_monotonic")
    return warnings


def _markdown(analysis: Mapping[str, Any]) -> str:
    lines = ["# Apex Trade Log Analysis", "", "## Overall", _metrics_line(analysis.get("overall", {})), ""]
    for title, key in (
        ("Direction", "by_direction"),
        ("Session", "by_session"),
        ("Killzone", "by_killzone"),
        ("Hour UTC", "by_hour_utc"),
        ("Component Combo", "by_component_combo"),
        ("Score Bucket", "by_score_bucket"),
        ("Duration Bucket", "by_duration_bucket"),
    ):
        lines.extend([f"## By {title}"])
        for name, metrics in dict(analysis.get(key, {})).items():
            lines.append(f"- {name}: {_metrics_line(metrics)}")
        lines.append("")
    lines.extend(
        [
            "## Compliance",
            f"- Profile compliance: {analysis.get('profile_compliance', {})}",
            f"- Strict profile violations: {analysis.get('strict_profile_violations', {})}",
            f"- Post-cost RR distribution: {analysis.get('post_cost_rr_distribution', {})}",
            "",
            "## Exclusion Simulations",
        ]
    )
    for name, data in dict(analysis.get("exclusion_simulations", {})).items():
        lines.append(f"- {name}: kept={_metrics_line(data.get('kept', {}))}; excluded={_metrics_line(data.get('excluded', {}))}")
    lines.extend(["", "## Warnings", *(f"- {item}" for item in analysis.get("warnings", []))])
    return "\n".join(lines) + "\n"


def _metrics_line(metrics: Mapping[str, Any]) -> str:
    return (
        f"trades={metrics.get('trades', 0)} wins={metrics.get('wins', 0)} "
        f"losses={metrics.get('losses', 0)} net_R={metrics.get('net_R', 0.0)} "
        f"PF={metrics.get('profit_factor')} expectancy={metrics.get('expectancy_R', 0.0)} "
        f"DD={metrics.get('max_drawdown_R', 0.0)}"
    )


def _component_text(row: Mapping[str, Any]) -> str:
    return str(row.get("components") or row.get("components_detected") or "").strip().lower()


def _has_displacement_tag(row: Mapping[str, Any]) -> bool:
    return "displacement" in f"{row.get('components', '')} {row.get('components_detected', '')}".lower()


def _first_value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value not in {None, ""}:
            return value
    return None


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
