from __future__ import annotations

from collections import defaultdict
from statistics import mean

from reports.report_config import ReportingConfig


def as_float(value: object, default: float = 0.0) -> float:
    try:
        text = str(value).strip()
        return float(text) if text else default
    except (TypeError, ValueError):
        return default


def _result(row: dict[str, str]) -> str:
    value = (row.get("result") or row.get("exit_reason") or row.get("final_exit_reason") or "").lower()
    pnl = as_float(row.get("pnl", row.get("profit", row.get("realized_R", 0))))
    if "win" in value or "tp" in value or "profit" in value:
        return "win"
    if "loss" in value or "sl" in value or "stop" in value:
        return "loss"
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "breakeven"


def _rr(row: dict[str, str]) -> float:
    return as_float(row.get("rr_actual", row.get("realized_R", row.get("r_multiple", 0))))


def _pnl(row: dict[str, str]) -> float:
    return as_float(row.get("pnl", row.get("profit", 0)))


def _bucket_post_cost_rr(value: float) -> str:
    if value < 2:
        return "<2R"
    if value < 2.5:
        return "2R-2.49R"
    if value < 3:
        return "2.5R-2.99R"
    return "3R+"


def _bucket_rr(value: float) -> str:
    if value < 2:
        return "below_1_to_2"
    if value < 3:
        return "1_to_2_to_1_to_3"
    if value < 5:
        return "1_to_3_to_1_to_5"
    if value < 10:
        return "1_to_5_to_1_to_10"
    return "above_1_to_10"


def _group(rows: list[dict[str, str]], key: str) -> dict[str, dict]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row.get(key, "") or "unknown"].append(row)
    return {name: _summary(group_rows) for name, group_rows in sorted(groups.items())}


def _summary(rows: list[dict[str, str]]) -> dict:
    pnls = [_pnl(row) for row in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(rows),
        "net_pnl": round(sum(pnls), 4),
        "wins": len(wins),
        "losses": len(losses),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "expectancy": round(sum(pnls) / len(rows), 4) if rows else 0.0,
    }


def calculate_metrics(rows: list[dict[str, str]], config: ReportingConfig, equity_rows: list[dict[str, str]] | None = None) -> dict:
    pnls = [_pnl(row) for row in rows]
    rrs = [_rr(row) for row in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    streak_win = streak_loss = max_win_streak = max_loss_streak = 0
    for row in rows:
        result = _result(row)
        if result == "win":
            streak_win += 1
            streak_loss = 0
        elif result == "loss":
            streak_loss += 1
            streak_win = 0
        else:
            streak_loss = streak_win = 0
        max_win_streak = max(max_win_streak, streak_win)
        max_loss_streak = max(max_loss_streak, streak_loss)

    post_cost_distribution: dict[str, int] = {"<2R": 0, "2R-2.49R": 0, "2.5R-2.99R": 0, "3R+": 0}
    rr_buckets: dict[str, int] = defaultdict(int)
    rr_below_min = 0
    for row in rows:
        post_cost = as_float(row.get("post_cost_rr", row.get("estimated_post_cost_rr", row.get("rr_planned", 0))))
        post_cost_distribution[_bucket_post_cost_rr(post_cost)] += 1
        rr_buckets[_bucket_rr(post_cost)] += 1
        if post_cost < config.minimum_planned_rr:
            rr_below_min += 1

    drawdown = None
    if equity_rows:
        equity_values = [as_float(row.get("equity", row.get("balance", "")), None) for row in equity_rows]
        equity_values = [value for value in equity_values if value is not None]
        peak = None
        max_dd = 0.0
        for value in equity_values:
            peak = value if peak is None else max(peak, value)
            max_dd = max(max_dd, peak - value)
        drawdown = round(max_dd, 4)

    durations = [as_float(row.get("duration_minutes", row.get("duration_min", "")), None) for row in rows]
    durations = [value for value in durations if value is not None]
    metrics = {
        "trade_count": len(rows),
        "closed_trades": len([r for r in rows if r.get("exit_time") or r.get("exit_time_utc_normalized") or r.get("exit_price")]),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(rows) - len(wins) - len(losses),
        "win_rate": round(len(wins) / len(rows), 4) if rows else 0.0,
        "net_pnl": round(sum(pnls), 4),
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "average_win": round(mean(wins), 4) if wins else 0.0,
        "average_loss": round(mean(losses), 4) if losses else 0.0,
        "largest_win": round(max(wins), 4) if wins else 0.0,
        "largest_loss": round(min(losses), 4) if losses else 0.0,
        "expectancy": round(sum(pnls) / len(rows), 4) if rows else 0.0,
        "avg_rr": round(mean(rrs), 4) if rrs else 0.0,
        "best_rr": round(max(rrs), 4) if rrs else 0.0,
        "worst_rr": round(min(rrs), 4) if rrs else 0.0,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "max_drawdown": drawdown,
        "average_duration_minutes": round(mean(durations), 2) if durations else None,
        "rr_buckets": dict(rr_buckets),
        "post_cost_rr_distribution": post_cost_distribution,
        "rr_compliance": {
            "minimum_planned_rr": config.minimum_planned_rr,
            "post_cost_rr_below_profile_minimum": rr_below_min,
        },
        "by_setup": _group(rows, "strategy_setup"),
        "by_session": _group(rows, "session"),
        "by_timeframe": _group(rows, "timeframe"),
        "by_direction": _group(rows, "direction"),
    }
    return metrics

