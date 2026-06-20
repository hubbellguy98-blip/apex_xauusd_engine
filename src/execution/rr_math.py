"""Shared risk/reward and cost-adjusted RR helpers."""

from __future__ import annotations

from typing import Any


def calculate_raw_rr(direction: Any, entry: float, stop: float, target: float) -> float:
    risk = abs(float(entry) - float(stop))
    if risk <= 0:
        return 0.0
    reward = _reward(direction, float(entry), float(target))
    return round(max(0.0, reward / risk), 5)


def calculate_post_cost_rr(
    direction: Any,
    fill_price: float,
    stop: float,
    target: float,
    spread: float = 0.0,
    slippage: float = 0.0,
) -> float:
    """Return executable RR after adding spread/slippage to risk/reward."""

    fill = float(fill_price)
    total_cost = max(0.0, float(spread)) + max(0.0, float(slippage))
    risk = abs(fill - float(stop)) + total_cost
    if risk <= 0:
        return 0.0
    reward = _reward(direction, fill, float(target)) - total_cost
    return round(max(0.0, reward / risk), 5)


def risk_to_cost_ratio(entry: float, stop: float, spread: float = 0.0, slippage: float = 0.0) -> float:
    total_cost = max(0.0, float(spread)) + max(0.0, float(slippage))
    if total_cost <= 0:
        return float("inf")
    return round(abs(float(entry) - float(stop)) / total_cost, 5)


def _reward(direction: Any, entry: float, target: float) -> float:
    text = str(getattr(direction, "value", direction)).upper()
    if text in {"BUY", "BULLISH", "LONG"}:
        return target - entry
    if text in {"SELL", "BEARISH", "SHORT"}:
        return entry - target
    return 0.0
