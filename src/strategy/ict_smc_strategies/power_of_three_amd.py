"""Power of Three / AMD strategy model for ICT/SMC research.

AMD is only tradable after all three phases are confirmed in order:

accumulation range -> manipulation sweep -> reclaim/rejection ->
distribution MSS/displacement -> target/RR/safety checks.

This module is deterministic, closed-candle only, and never places orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence


class AMDStatus(str, Enum):
    VALID = "valid"
    REJECTED = "rejected"
    CONTEXT_ONLY = "context_only"
    WAITING_FOR_RETEST = "waiting_for_retest"


class AMDDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class _Candle:
    position: int
    index: int
    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    is_closed: bool = True

    @property
    def range(self) -> float:
        return max(self.high - self.low, 1e-9)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_to_range(self) -> float:
        return self.body / self.range

    @property
    def bullish_close_position(self) -> float:
        return (self.close - self.low) / self.range

    @property
    def bearish_close_position(self) -> float:
        return (self.high - self.close) / self.range


def _value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _rows(data: Any) -> list[Any]:
    if data is None:
        return []
    if hasattr(data, "to_dict"):
        try:
            return list(data.to_dict("records"))
        except TypeError:
            pass
    return list(data)


def _candles(data: Any, *, closed_only: bool = True) -> list[_Candle]:
    candles: list[_Candle] = []
    for position, row in enumerate(_rows(data)):
        is_closed = bool(_value(row, "is_closed", True))
        if closed_only and not is_closed:
            continue
        try:
            candles.append(
                _Candle(
                    position=position,
                    index=int(_value(row, "index", position)),
                    timestamp=_value(row, "timestamp", _value(row, "time", position)),
                    open=float(_value(row, "open")),
                    high=float(_value(row, "high")),
                    low=float(_value(row, "low")),
                    close=float(_value(row, "close")),
                    volume=float(_value(row, "volume", 0.0) or 0.0),
                    is_closed=is_closed,
                )
            )
        except (TypeError, ValueError):
            continue
    return candles


def _cfg(config: Mapping[str, Any] | None, key: str, default: Any) -> Any:
    return default if config is None else config.get(key, default)


def _direction(value: Any) -> str:
    raw = str(value.value if isinstance(value, Enum) else value or "unknown").lower()
    if raw in {"bull", "buy", "long", "buy_side", "buyside", "bullish"}:
        return "bullish"
    if raw in {"bear", "sell", "short", "sell_side", "sellside", "bearish"}:
        return "bearish"
    return "unknown"


def _atr(candles: Sequence[_Candle], period: int = 14, end_position: int | None = None) -> float:
    if not candles:
        return 1.0
    end = len(candles) if end_position is None else max(1, min(len(candles), end_position + 1))
    window = candles[max(0, end - max(1, period)) : end]
    return max(mean([c.range for c in window]), 1e-9) if window else 1.0


def _window_positions(
    candles: Sequence[_Candle],
    window: Mapping[str, Any] | None,
    default_start: int,
    default_end: int,
) -> tuple[int, int]:
    if not candles:
        return 0, -1
    window = window or {}
    start = int(window.get("start_position", window.get("start_index", default_start)))
    end = int(window.get("end_position", window.get("end_index", default_end)))
    start = max(0, min(start, len(candles) - 1))
    end = max(start, min(end, len(candles) - 1))
    return start, end


def _target_side(direction: str) -> str:
    return "buy_side" if direction == "bullish" else "sell_side"


def _pool_side(pool: Mapping[str, Any]) -> str:
    side = str(pool.get("side", pool.get("type", ""))).lower()
    if side in {"bsl", "buy_side", "buyside", "buy-side", "high", "equal_highs"}:
        return "buy_side"
    if side in {"ssl", "sell_side", "sellside", "sell-side", "low", "equal_lows"}:
        return "sell_side"
    return side


def _pool_level(pool: Mapping[str, Any]) -> float | None:
    for key in ("price", "level", "liquidity_level", "target_price"):
        if key in pool:
            try:
                return float(pool[key])
            except (TypeError, ValueError):
                return None
    return None


def score_accumulation_quality(
    accumulation_range: Mapping[str, Any],
    df: Any,
    atr: float | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score whether a range is clean enough to be true accumulation."""
    candles = _candles(df)
    start = int(accumulation_range.get("range_start_position", 0))
    end = int(accumulation_range.get("range_end_position", max(0, len(candles) - 1)))
    window = candles[start : end + 1]
    reasons: list[str] = []
    if not window:
        return {
            "quality_score": 0.0,
            "clean_range": False,
            "component_scores": {},
            "rejection_reasons": ["insufficient_accumulation_candles"],
        }

    range_size = float(accumulation_range["range_size"])
    atr_value = atr or _atr(candles, int(_cfg(config, "atr_period", 14)), end)
    range_to_atr = range_size / max(atr_value, 1e-9)
    largest_candle_ratio = max(c.range for c in window) / max(range_size, 1e-9)
    net_move = abs(window[-1].close - window[0].open)
    trend_efficiency = net_move / max(sum(c.range for c in window), 1e-9)
    boundary_buffer = range_size * 0.18
    boundary_touches = sum(
        1
        for candle in window
        if candle.high >= float(accumulation_range["range_high"]) - boundary_buffer
        or candle.low <= float(accumulation_range["range_low"]) + boundary_buffer
    )
    overlap_score = min(10.0, boundary_touches / max(len(window), 1) * 12.0)

    if range_size < float(_cfg(config, "min_accumulation_range_size", 0.5)):
        reasons.append("accumulation_range_too_small")
    if range_size > float(_cfg(config, "max_accumulation_range_size", 80.0)):
        reasons.append("accumulation_range_too_wide")
    if range_to_atr < float(_cfg(config, "min_accumulation_range_atr", 0.6)):
        reasons.append("accumulation_range_too_small")
    if range_to_atr > float(_cfg(config, "max_accumulation_range_atr", 8.0)):
        reasons.append("accumulation_range_too_wide")
    if largest_candle_ratio > float(_cfg(config, "max_dominant_candle_ratio", 0.72)):
        reasons.append("dominant_spike_inside_accumulation")
    if trend_efficiency > float(_cfg(config, "max_accumulation_trend_efficiency", 0.42)):
        reasons.append("accumulation_trended_too_much")
    if boundary_touches < int(_cfg(config, "min_boundary_touches", 2)):
        reasons.append("unclear_accumulation_boundaries")

    components = {
        "range_size": 9.0 if not any("range_too" in r for r in reasons) else 4.0,
        "dominant_candle_control": max(0.0, 10.0 - largest_candle_ratio * 10.0),
        "trend_balance": max(0.0, 10.0 - trend_efficiency * 18.0),
        "boundary_clarity": overlap_score,
        "candle_count": min(10.0, len(window) / max(int(_cfg(config, "min_accumulation_candles", 4)), 1) * 7.5),
    }
    score = sum(components.values()) / len(components)
    clean = score >= float(_cfg(config, "min_accumulation_quality", 6.5)) and not reasons
    return {
        "quality_score": round(max(0.0, min(10.0, score)), 2),
        "clean_range": clean,
        "component_scores": {key: round(max(0.0, min(10.0, value)), 2) for key, value in components.items()},
        "range_to_atr": round(range_to_atr, 4),
        "dominant_candle_ratio": round(largest_candle_ratio, 4),
        "trend_efficiency": round(trend_efficiency, 4),
        "boundary_touch_count": boundary_touches,
        "rejection_reasons": reasons,
    }


def detect_accumulation_range(
    df: Any,
    accumulation_window: Mapping[str, Any] | None = None,
    timezone: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect a completed accumulation range from a configured candle window."""
    candles = _candles(df)
    min_candles = int(_cfg(config, "min_accumulation_candles", 4))
    if len(candles) < min_candles:
        return {
            "valid_status": False,
            "clean_range": False,
            "rejection_reasons": ["insufficient_accumulation_candles"],
        }

    default_end = min(len(candles) - 1, min_candles - 1)
    start, end = _window_positions(candles, accumulation_window, 0, default_end)
    window = candles[start : end + 1]
    if len(window) < min_candles:
        return {
            "valid_status": False,
            "clean_range": False,
            "rejection_reasons": ["insufficient_accumulation_candles"],
            "candle_count": len(window),
        }

    range_high = max(c.high for c in window)
    range_low = min(c.low for c in window)
    range_size = range_high - range_low
    high_candle = max(window, key=lambda c: c.high)
    low_candle = min(window, key=lambda c: c.low)
    result = {
        "range_type": (accumulation_window or {}).get("range_type", _cfg(config, "range_type", "session_range")),
        "timezone": timezone or _cfg(config, "timezone", "UTC"),
        "range_high": round(range_high, 8),
        "range_low": round(range_low, 8),
        "range_mid": round((range_high + range_low) / 2.0, 8),
        "range_size": round(range_size, 8),
        "range_start_time": window[0].timestamp,
        "range_end_time": window[-1].timestamp,
        "range_start_index": window[0].index,
        "range_end_index": window[-1].index,
        "range_start_position": start,
        "range_end_position": end,
        "high_time": high_candle.timestamp,
        "low_time": low_candle.timestamp,
        "candle_count": len(window),
        "window_complete": True,
        "uses_closed_candles": all(c.is_closed for c in window),
    }
    quality = score_accumulation_quality(result, candles, None, config)
    result.update(
        {
            "quality_score": quality["quality_score"],
            "clean_range": quality["clean_range"],
            "valid_status": quality["clean_range"],
            "quality_details": quality,
            "rejection_reasons": quality["rejection_reasons"],
        }
    )
    return result


def _find_reclaim(
    candles: Sequence[_Candle],
    direction: str,
    start_position: int,
    accumulation_range: Mapping[str, Any],
    config: Mapping[str, Any] | None,
) -> tuple[_Candle | None, str]:
    max_reclaim = int(_cfg(config, "max_reclaim_candles", 3))
    range_low = float(accumulation_range["range_low"])
    range_high = float(accumulation_range["range_high"])
    for pos in range(start_position, min(len(candles), start_position + max_reclaim + 1)):
        candle = candles[pos]
        if direction == "bullish" and candle.close > range_low:
            return candle, "reclaimed_above_range_low"
        if direction == "bearish" and candle.close < range_high:
            return candle, "rejected_below_range_high"
    return None, "no_reclaim_or_rejection"


def detect_manipulation_sweep(
    df: Any,
    accumulation_range: Mapping[str, Any],
    manipulation_window: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect the post-accumulation liquidity raid and reclaim/rejection."""
    candles = _candles(df)
    if not accumulation_range.get("valid_status", False):
        return {
            "manipulation_detected": False,
            "rejection_reasons": ["invalid_accumulation_range"],
        }
    start_default = int(accumulation_range.get("range_end_position", 0)) + 1
    start, end = _window_positions(candles, manipulation_window, start_default, len(candles) - 1)
    range_low = float(accumulation_range["range_low"])
    range_high = float(accumulation_range["range_high"])
    sweep_buffer = float(_cfg(config, "sweep_buffer", 0.05))
    min_depth = float(_cfg(config, "min_sweep_depth", 0.05))
    max_sweep_atr = float(_cfg(config, "max_sweep_atr", 3.5))
    candidates: list[dict[str, Any]] = []
    side_seen: set[str] = set()

    for pos in range(start, end + 1):
        candle = candles[pos]
        possible = []
        if candle.low < range_low - sweep_buffer:
            possible.append(("bullish", "range_low_sell_side", range_low - candle.low, candle.low))
        if candle.high > range_high + sweep_buffer:
            possible.append(("bearish", "range_high_buy_side", candle.high - range_high, candle.high))
        for direction, swept_side, depth, extreme in possible:
            side_seen.add(swept_side)
            reclaim_candle, reclaim_status = _find_reclaim(candles, direction, pos, accumulation_range, config)
            atr_value = _atr(candles, int(_cfg(config, "atr_period", 14)), pos)
            reasons = []
            if depth < min_depth:
                reasons.append("sweep_too_small")
            if depth / max(atr_value, 1e-9) > max_sweep_atr:
                reasons.append("sweep_too_large_likely_news_spike")
            if reclaim_candle is None:
                reasons.append("real_breakout_not_manipulation")
            reclaim_score = 0.0
            if reclaim_candle is not None:
                if direction == "bullish":
                    reclaim_score = min(3.0, (reclaim_candle.close - range_low) / max(atr_value, 1e-9) * 2.0)
                    trapped = "breakdown_sellers"
                else:
                    reclaim_score = min(3.0, (range_high - reclaim_candle.close) / max(atr_value, 1e-9) * 2.0)
                    trapped = "breakout_buyers"
            else:
                trapped = "unknown"
            quality = 5.5 + min(2.0, depth / max(atr_value, 1e-9) * 2.0) + reclaim_score
            candidates.append(
                {
                    "manipulation_detected": not reasons,
                    "manipulation_direction_bias": direction,
                    "swept_side": swept_side,
                    "sweep_time": candle.timestamp,
                    "sweep_index": candle.index,
                    "sweep_position": pos,
                    "sweep_extreme": round(extreme, 8),
                    "sweep_depth": round(depth, 8),
                    "reclaim_or_rejection_status": reclaim_status,
                    "reclaim_index": reclaim_candle.index if reclaim_candle else None,
                    "reclaim_position": reclaim_candle.position if reclaim_candle else None,
                    "reclaim_time": reclaim_candle.timestamp if reclaim_candle else None,
                    "trapped_side": trapped,
                    "manipulation_quality_score": round(max(0.0, min(10.0, quality if not reasons else 3.0)), 2),
                    "both_sides_swept": False,
                    "rejection_reasons": reasons,
                }
            )

    valid_candidates = [item for item in candidates if item["manipulation_detected"]]
    earliest_valid_position = min((int(item["sweep_position"]) for item in valid_candidates), default=None)
    earliest_valid_sides = {
        item["swept_side"] for item in valid_candidates if int(item["sweep_position"]) == earliest_valid_position
    }
    true_double_sweep = len(earliest_valid_sides) > 1 or (not valid_candidates and len(side_seen) > 1)
    if true_double_sweep:
        for item in candidates:
            item["both_sides_swept"] = True
            if not bool(_cfg(config, "allow_double_sided_sweep", False)):
                item["rejection_reasons"].append("double_sided_sweep_no_clear_direction")
                item["manipulation_detected"] = False
    if valid_candidates and not true_double_sweep:
        valid_candidates.sort(key=lambda item: item["manipulation_quality_score"], reverse=True)
        return valid_candidates[0]
    if candidates:
        candidates.sort(key=lambda item: item["manipulation_quality_score"], reverse=True)
        return candidates[0]
    return {
        "manipulation_detected": False,
        "rejection_reasons": ["no_manipulation_sweep"],
    }


def _post_manipulation_level(candles: Sequence[_Candle], start: int, pos: int, direction: str) -> float:
    lookback = candles[start:pos]
    if not lookback:
        return candles[start].high if direction == "bullish" else candles[start].low
    return max(c.high for c in lookback) if direction == "bullish" else min(c.low for c in lookback)


def _displacement_ok(candle: _Candle, candles: Sequence[_Candle], config: Mapping[str, Any] | None) -> bool:
    return candle.body_to_range >= float(_cfg(config, "min_body_to_range", 0.55)) and candle.range / _atr(
        candles, int(_cfg(config, "atr_period", 14)), candle.position
    ) >= float(_cfg(config, "displacement_min_range_to_atr", 0.65))


def detect_distribution_shift(
    df: Any,
    manipulation_event: Mapping[str, Any],
    swings: Sequence[Mapping[str, Any]] | None = None,
    atr: float | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Confirm MSS/displacement after manipulation reclaim or rejection."""
    candles = _candles(df)
    if not manipulation_event.get("manipulation_detected"):
        return {
            "distribution_confirmed": False,
            "rejection_reasons": ["no_manipulation_sweep"],
        }
    direction = _direction(manipulation_event.get("manipulation_direction_bias"))
    start = int(manipulation_event.get("reclaim_position", manipulation_event.get("sweep_position", 0))) + 1
    max_wait = int(_cfg(config, "max_distribution_wait_candles", 8))
    break_buffer = float(_cfg(config, "break_buffer", 0.05))

    for pos in range(start, min(len(candles), start + max_wait + 1)):
        candle = candles[pos]
        level = _post_manipulation_level(candles, int(manipulation_event["sweep_position"]), pos, direction)
        if direction == "bullish":
            mss_confirmed = candle.close > level + break_buffer
            displacement = (
                candle.bullish and _displacement_ok(candle, candles, config) and candle.bullish_close_position >= 0.62
            )
            rejection_reason = "no_bullish_mss_after_manipulation"
        else:
            mss_confirmed = candle.close < level - break_buffer
            displacement = (
                candle.bearish and _displacement_ok(candle, candles, config) and candle.bearish_close_position >= 0.62
            )
            rejection_reason = "no_bearish_mss_after_manipulation"
        if mss_confirmed and displacement:
            fvg_created = False
            if pos >= 2 and direction == "bullish":
                fvg_created = candles[pos - 2].high < candle.low
            elif pos >= 2 and direction == "bearish":
                fvg_created = candles[pos - 2].low > candle.high
            strength = (
                6.5
                + min(2.0, candle.range / max(atr or _atr(candles, int(_cfg(config, "atr_period", 14)), pos), 1e-9))
                + (0.7 if fvg_created else 0.0)
            )
            return {
                "distribution_confirmed": True,
                "distribution_direction": direction,
                "mss_confirmed": True,
                "broken_level": round(level, 8),
                "confirmation_index": candle.index,
                "confirmation_position": pos,
                "confirmation_time": candle.timestamp,
                "displacement_confirmed": True,
                "range_to_atr_ratio": round(
                    candle.range / max(_atr(candles, int(_cfg(config, "atr_period", 14)), pos), 1e-9), 4
                ),
                "body_to_range_ratio": round(candle.body_to_range, 4),
                "fvg_created": fvg_created,
                "ob_created": True,
                "distribution_strength_score": round(max(0.0, min(10.0, strength)), 2),
                "rejection_reasons": [],
            }

    reasons = ["no_distribution_confirmation", rejection_reason, "displacement_not_confirmed"]
    return {
        "distribution_confirmed": False,
        "distribution_direction": direction,
        "mss_confirmed": False,
        "displacement_confirmed": False,
        "distribution_strength_score": 0.0,
        "rejection_reasons": reasons,
    }


def _select_target(
    direction: str, entry_price: float, accumulation: Mapping[str, Any], pools: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    target_side = _target_side(direction)
    candidates: list[tuple[bool, bool, float, dict[str, Any], float]] = []
    internal_price = float(accumulation["range_high"] if direction == "bullish" else accumulation["range_low"])
    if (direction == "bullish" and internal_price > entry_price) or (
        direction == "bearish" and internal_price < entry_price
    ):
        candidates.append(
            (
                False,
                False,
                abs(internal_price - entry_price),
                {"id": "accumulation_opposite_boundary", "side": target_side},
                internal_price,
            )
        )
    for pool in pools or []:
        price = _pool_level(pool)
        if price is None or _pool_side(pool) != target_side:
            continue
        if direction == "bullish" and price <= entry_price:
            continue
        if direction == "bearish" and price >= entry_price:
            continue
        swept = bool(pool.get("swept", pool.get("already_swept", False)))
        candidates.append((swept, True, abs(price - entry_price), dict(pool), price))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], not item[1], -item[2]))
    swept, _, _, source, price = candidates[0]
    return {
        "side": target_side,
        "price": round(price, 8),
        "reference": source.get("id", source.get("name", "liquidity_pool")),
        "already_swept": swept,
    }


def _risk(
    direction: str,
    entry_price: float,
    manipulation: Mapping[str, Any],
    candles: Sequence[_Candle],
    config: Mapping[str, Any] | None,
    spread_status: Mapping[str, Any] | None,
) -> dict[str, Any]:
    spread_status = spread_status or {}
    atr_value = _atr(candles, int(_cfg(config, "atr_period", 14)))
    spread_points = float(spread_status.get("spread_points", spread_status.get("spread", 0.0)) or 0.0)
    buffer = max(float(_cfg(config, "stop_buffer", 0.10)), atr_value * float(_cfg(config, "stop_atr_buffer", 0.08)))
    buffer += spread_points * float(_cfg(config, "spread_point_value", 0.01))
    extreme = float(manipulation["sweep_extreme"])
    stop = extreme - buffer if direction == "bullish" else extreme + buffer
    return {
        "stop_loss": round(stop, 8),
        "stop_reference": (
            "below_manipulation_low_with_atr_and_spread_buffer"
            if direction == "bullish"
            else "above_manipulation_high_with_atr_and_spread_buffer"
        ),
        "stop_buffer": round(buffer, 8),
    }


def _rr(direction: str, entry_price: float, stop_loss: float, target_price: float) -> tuple[float, float, float]:
    risk_distance = abs(entry_price - stop_loss)
    reward_distance = target_price - entry_price if direction == "bullish" else entry_price - target_price
    if risk_distance <= 0:
        return risk_distance, reward_distance, 0.0
    return risk_distance, reward_distance, reward_distance / risk_distance


def score_amd_setup(
    setup: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score complete AMD setup quality from 0 to 10."""
    context = context or {}
    accumulation = setup.get("accumulation", {}) or {}
    manipulation = setup.get("manipulation", {}) or {}
    distribution = setup.get("distribution", {}) or {}
    htf_bias = context.get("htf_bias", {}) or {}
    direction = _direction(setup.get("direction"))
    htf_direction = _direction(htf_bias.get("bias_direction", htf_bias.get("direction")))
    rr_value = float(setup.get("rr", setup.get("risk", {}).get("rr", 0.0)) or 0.0)
    min_rr = float(_cfg(config, "min_rr", 2.0))
    components = {
        "accumulation_quality": float(accumulation.get("quality_score", 0.0) or 0.0),
        "manipulation_sweep": float(manipulation.get("manipulation_quality_score", 0.0) or 0.0),
        "reclaim_rejection": (
            8.5 if manipulation.get("reclaim_or_rejection_status") not in {None, "no_reclaim_or_rejection"} else 0.0
        ),
        "distribution_mss": 8.5 if distribution.get("mss_confirmed") else 0.0,
        "displacement_strength": float(distribution.get("distribution_strength_score", 0.0) or 0.0),
        "entry_poi_quality": float(setup.get("entry_poi", {}).get("quality_score", 7.5) or 0.0),
        "htf_alignment": 8.5 if htf_direction in {"unknown", direction} else 4.0,
        "target_rr": min(10.0, rr_value / max(min_rr, 1e-9) * 8.0),
        "xauusd_safety": 9.0,
        "session_timing": float(context.get("session_score", _cfg(config, "default_session_score", 8.0))),
    }
    spread_status = context.get("spread_status", {}) or {}
    if spread_status.get("spread_safe") is False or str(spread_status.get("status", "")).lower() in {"unsafe", "high"}:
        components["xauusd_safety"] -= 4.0
    if (context.get("news_status", {}) or {}).get("restricted", False):
        components["xauusd_safety"] -= 4.0
    components = {key: round(max(0.0, min(10.0, value)), 2) for key, value in components.items()}
    weights = {
        "accumulation_quality": 1.1,
        "manipulation_sweep": 1.1,
        "reclaim_rejection": 1.0,
        "distribution_mss": 1.2,
        "displacement_strength": 1.1,
        "entry_poi_quality": 0.8,
        "htf_alignment": 0.7,
        "target_rr": 1.0,
        "xauusd_safety": 0.9,
        "session_timing": 0.7,
    }
    total = sum(components[key] * weights[key] for key in components) / sum(weights.values())
    failures = list(setup.get("rejection_reasons", []))
    trade_allowed = total >= float(_cfg(config, "minimum_setup_score", _cfg(config, "min_score", 7.0))) and not failures
    grade = (
        "A+"
        if total >= 9
        else "A" if total >= 8 else "B" if total >= 7 else "C" if total >= 6 else "D" if total >= 5 else "F"
    )
    return {
        "total_score": round(total, 2),
        "component_scores": components,
        "grade": grade,
        "trade_allowed": trade_allowed,
        "hard_filter_failures": failures,
    }


def _decision(
    status: AMDStatus, symbol: str, reasons: Sequence[str], details: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    clean = []
    for reason in reasons:
        if reason and reason not in clean:
            clean.append(reason)
    return {
        "strategy": "Power of Three / AMD",
        "symbol": symbol,
        "signal_status": status.value,
        "trade_allowed": False,
        "rejection_reasons": clean,
        "details": dict(details or {}),
    }


def generate_amd_signal(context: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Generate a complete AMD signal or a no-trade/context-only decision."""
    symbol = str(context.get("symbol", "XAUUSD"))
    df = context.get("setup_df", context.get("m15_df", context.get("candles", context.get("df", []))))
    candles = _candles(df)
    if len(candles) < int(_cfg(config, "min_total_candles", 8)):
        return _decision(AMDStatus.REJECTED, symbol, ["insufficient_closed_candles"])

    safety_reasons: list[str] = []
    news_status = context.get("news_status", {}) or {}
    if news_status.get("restricted", news_status.get("is_restricted", False)):
        safety_reasons.append("news_restricted")
    spread_status = context.get("spread_status", {}) or {}
    if spread_status.get("spread_safe") is False or str(spread_status.get("status", "")).lower() in {"unsafe", "high"}:
        safety_reasons.append("spread_too_high")

    accumulation = context.get("accumulation_range") or detect_accumulation_range(
        df,
        context.get("accumulation_window"),
        context.get("timezone"),
        config,
    )
    if not accumulation.get("valid_status"):
        return _decision(
            AMDStatus.REJECTED,
            symbol,
            safety_reasons + ["invalid_accumulation_range"] + list(accumulation.get("rejection_reasons", [])),
            {"accumulation": accumulation},
        )

    manipulation = detect_manipulation_sweep(df, accumulation, context.get("manipulation_window"), config)
    if not manipulation.get("manipulation_detected"):
        reasons = safety_reasons + list(manipulation.get("rejection_reasons", []))
        status = AMDStatus.REJECTED
        return _decision(
            status,
            symbol,
            reasons or ["no_manipulation_sweep"],
            {"accumulation": accumulation, "manipulation": manipulation},
        )
    if manipulation.get("both_sides_swept") and not bool(_cfg(config, "allow_double_sided_sweep", False)):
        return _decision(
            AMDStatus.REJECTED,
            symbol,
            safety_reasons + ["double_sided_sweep_no_clear_direction"],
            {"accumulation": accumulation, "manipulation": manipulation},
        )

    distribution = detect_distribution_shift(df, manipulation, context.get("swings"), None, config)
    if not distribution.get("distribution_confirmed"):
        return _decision(
            AMDStatus.CONTEXT_ONLY,
            symbol,
            safety_reasons + list(distribution.get("rejection_reasons", [])),
            {"accumulation": accumulation, "manipulation": manipulation, "distribution": distribution},
        )

    direction = _direction(distribution.get("distribution_direction"))
    confirmation_candle = candles[int(distribution["confirmation_position"])]
    entry_price = confirmation_candle.close
    entry_poi = {
        "poi_type": f"{direction}_mss_displacement_entry",
        "zone_low": round(min(confirmation_candle.open, confirmation_candle.close), 8),
        "zone_high": round(max(confirmation_candle.open, confirmation_candle.close), 8),
        "zone_mid": round((confirmation_candle.open + confirmation_candle.close) / 2.0, 8),
        "retest_status": "confirmed_by_distribution_close",
        "reaction_confirmed": True,
        "quality_score": 7.8 + (0.5 if distribution.get("fvg_created") else 0.0),
    }
    target = _select_target(direction, entry_price, accumulation, context.get("liquidity_pools", []))
    rejection_reasons = list(safety_reasons)
    if not target:
        rejection_reasons.append("no_valid_target")
    elif target.get("already_swept"):
        rejection_reasons.append("target_already_swept")
    if context.get("htf_poi_blocks_target"):
        rejection_reasons.append("htf_poi_blocks_target")

    risk = _risk(direction, entry_price, manipulation, candles, config, spread_status)
    target_price = float(target["price"]) if target else entry_price
    risk_distance, reward_distance, rr_value = _rr(direction, entry_price, float(risk["stop_loss"]), target_price)
    min_rr = float(_cfg(config, "min_rr", 2.0))
    if risk_distance <= 0 or reward_distance <= 0:
        rejection_reasons.append("invalid_risk_reward")
    if rr_value < min_rr:
        rejection_reasons.append("rr_below_minimum")

    setup = {
        "strategy": "Power of Three / AMD",
        "symbol": symbol,
        "signal_id": f"{symbol}_AMD_{direction.upper()}_{distribution['confirmation_index']}",
        "signal_status": AMDStatus.VALID.value,
        "uses_closed_candles": all(c.is_closed for c in candles),
        "direction": direction,
        "accumulation": accumulation,
        "manipulation": manipulation,
        "distribution": distribution,
        "entry_poi": entry_poi,
        "entry": {
            "entry_type": "mss_displacement_confirmation_entry",
            "entry_price": round(entry_price, 8),
            "entry_time": confirmation_candle.timestamp,
        },
        "target": target,
        "risk": {
            **risk,
            "target": round(target_price, 8) if target else None,
            "target_reference": target.get("reference") if target else None,
            "risk_distance": round(risk_distance, 8),
            "reward_distance": round(reward_distance, 8),
            "rr": round(rr_value, 4),
            "min_rr_required": min_rr,
        },
        "rr": rr_value,
        "filters": {
            "news_filter": "failed" if "news_restricted" in rejection_reasons else "passed",
            "spread_filter": "failed" if "spread_too_high" in rejection_reasons else "passed",
            "double_sweep_filter": (
                "failed" if "double_sided_sweep_no_clear_direction" in rejection_reasons else "passed"
            ),
            "range_quality_filter": "passed",
            "htf_blocker_filter": "failed" if "htf_poi_blocks_target" in rejection_reasons else "passed",
            "rr_filter": "failed" if "rr_below_minimum" in rejection_reasons else "passed",
        },
        "rejection_reasons": rejection_reasons,
        "warnings": [
            "Do not classify future days as AMD unless accumulation, manipulation, and distribution all confirm."
        ],
    }
    setup["score"] = score_amd_setup(setup, context, config)
    setup["trade_allowed"] = bool(setup["score"]["trade_allowed"])
    if setup["trade_allowed"]:
        return setup
    return _decision(
        AMDStatus.REJECTED,
        symbol,
        rejection_reasons or setup["score"]["hard_filter_failures"] or ["score_or_filter_failed"],
        {
            "accumulation": accumulation,
            "manipulation": manipulation,
            "distribution": distribution,
            "score": setup["score"],
            "rr": round(rr_value, 4),
        },
    )
