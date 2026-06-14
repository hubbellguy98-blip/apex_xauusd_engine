"""Breaker Block strategy model for ICT/SMC research.

A breaker block is treated as a failed order block, not as a generic support /
resistance flip. The required sequence is:

valid original OB -> OB failure by close -> acceptance beyond the OB ->
structure shift -> breaker retest -> reaction -> target/RR/safety checks.

This module is pure Python and returns dictionaries for tests, backtests, and
future orchestration. It never places broker orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence


class BreakerDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    UNKNOWN = "unknown"


class BreakerStatus(str, Enum):
    VALID = "valid"
    REJECTED = "rejected"
    WAITING_FOR_RETEST = "waiting_for_breaker_retest"


class BreakerEntryMode(str, Enum):
    AGGRESSIVE = "aggressive"
    BALANCED = "balanced"
    CONSERVATIVE = "conservative"


class BreakerConfirmationMode(str, Enum):
    AGGRESSIVE = "aggressive"
    CANDLE_REACTION = "candle_reaction"
    LTF_MSS = "ltf_mss"


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
    if raw in {"bull", "buy", "long", "buy_side", "buyside", "bullish", "bullish_breaker"}:
        return "bullish"
    if raw in {"bear", "sell", "short", "sell_side", "sellside", "bearish", "bearish_breaker"}:
        return "bearish"
    return "unknown"


def _target_side(direction: str) -> str:
    return "buy_side" if direction == "bullish" else "sell_side"


def _atr(candles: Sequence[_Candle], period: int = 14, end_position: int | None = None) -> float:
    if not candles:
        return 1.0
    end = len(candles) if end_position is None else max(1, min(len(candles), end_position + 1))
    window = candles[max(0, end - max(1, period)) : end]
    return max(mean([c.range for c in window]), 1e-9) if window else 1.0


def _swing_list(candles: Sequence[_Candle], swings: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if swings:
        out = []
        for i, swing in enumerate(swings):
            try:
                out.append(
                    {
                        "id": swing.get("id", f"SWING_{i}"),
                        "kind": str(swing.get("kind", swing.get("type", ""))).lower(),
                        "index": int(swing.get("index", i)),
                        "price": float(swing.get("price", swing.get("level"))),
                    }
                )
            except (TypeError, ValueError):
                continue
        return out

    out = []
    for pos in range(1, len(candles) - 1):
        prev_c, current, next_c = candles[pos - 1], candles[pos], candles[pos + 1]
        if current.high > prev_c.high and current.high > next_c.high:
            out.append(
                {"id": f"SWING_HIGH_{current.index}", "kind": "high", "index": current.index, "price": current.high}
            )
        if current.low < prev_c.low and current.low < next_c.low:
            out.append(
                {"id": f"SWING_LOW_{current.index}", "kind": "low", "index": current.index, "price": current.low}
            )
    return out


def _breaks_structure(
    candles: Sequence[_Candle],
    swings: Sequence[Mapping[str, Any]] | None,
    direction: str,
    source_position: int,
    end_position: int,
    buffer: float,
) -> dict[str, Any] | None:
    swing_kind = "high" if direction == "bullish" else "low"
    candidates = [
        swing
        for swing in _swing_list(candles, swings)
        if swing["kind"] == swing_kind and int(swing["index"]) <= candles[source_position].index
    ]
    if not candidates:
        return None
    swing = candidates[-1]
    level = float(swing["price"])
    for pos in range(source_position + 1, min(len(candles), end_position + 1)):
        candle = candles[pos]
        if direction == "bullish" and candle.close > level + buffer:
            return {
                "type": "bullish_bos",
                "broken_level": level,
                "broken_swing_id": swing["id"],
                "confirmed_by_close": True,
                "confirmation_index": candle.index,
                "confirmation_position": pos,
                "confirmation_time": candle.timestamp,
            }
        if direction == "bearish" and candle.close < level - buffer:
            return {
                "type": "bearish_bos",
                "broken_level": level,
                "broken_swing_id": swing["id"],
                "confirmed_by_close": True,
                "confirmation_index": candle.index,
                "confirmation_position": pos,
                "confirmation_time": candle.timestamp,
            }
    return None


def detect_order_blocks(
    df: Any,
    swings: Sequence[Mapping[str, Any]] | None = None,
    structure_events: Sequence[Mapping[str, Any]] | None = None,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Detect valid original order blocks that can later become breakers."""
    candles = _candles(df)
    if len(candles) < 4:
        return []
    break_buffer = float(_cfg(config, "break_buffer", 0.0))
    wait = int(_cfg(config, "max_displacement_wait_candles", 4))
    min_body = float(_cfg(config, "min_body_to_range", 0.55))
    min_range_to_atr = float(_cfg(config, "displacement_min_range_to_atr", 0.55))
    max_width_atr = float(_cfg(config, "max_breaker_width_atr", 3.0))
    blocks = []

    for pos, source in enumerate(candles[:-1]):
        if source.bearish:
            ob_type, direction = "bullish_order_block", "bullish"
        elif source.bullish:
            ob_type, direction = "bearish_order_block", "bearish"
        else:
            continue
        displacement = None
        for dpos in range(pos + 1, min(len(candles), pos + wait + 1)):
            candidate = candles[dpos]
            if direction == "bullish" and not candidate.bullish:
                continue
            if direction == "bearish" and not candidate.bearish:
                continue
            range_to_atr = candidate.range / _atr(candles, int(_cfg(config, "atr_period", 14)), dpos)
            close_position = (
                candidate.bullish_close_position if direction == "bullish" else candidate.bearish_close_position
            )
            if candidate.body_to_range >= min_body and range_to_atr >= min_range_to_atr and close_position >= 0.62:
                displacement = {
                    "index": candidate.index,
                    "position": dpos,
                    "range_to_atr": round(range_to_atr, 4),
                    "body_to_range": round(candidate.body_to_range, 4),
                }
                break
        if not displacement:
            continue
        structure = _breaks_structure(candles, swings, direction, pos, displacement["position"], break_buffer)
        if structure_events:
            structure = next(
                (dict(event) for event in structure_events if _direction(event.get("direction")) == direction),
                structure,
            )
        if not structure:
            continue
        width = source.range
        width_to_atr = width / _atr(candles, int(_cfg(config, "atr_period", 14)), pos)
        if width_to_atr > max_width_atr:
            quality = 4.0
        else:
            quality = 7.0 + min(1.5, displacement["range_to_atr"]) + min(1.0, source.body_to_range)
        zone_low, zone_high = source.low, source.high
        blocks.append(
            {
                "ob_id": f"{ob_type.upper()}_{source.index}",
                "ob_type": ob_type,
                "direction": direction,
                "source_candle_index": source.index,
                "source_position": pos,
                "source_candle_time": source.timestamp,
                "zone_low": round(zone_low, 8),
                "zone_high": round(zone_high, 8),
                "body_low": round(min(source.open, source.close), 8),
                "body_high": round(max(source.open, source.close), 8),
                "mean_threshold": round((zone_low + zone_high) / 2.0, 8),
                "created_by_displacement": True,
                "structure_break_confirmed": True,
                "structure_event": structure,
                "valid_from_index": structure["confirmation_index"],
                "valid_from_position": structure["confirmation_position"],
                "fresh_status": "fresh",
                "mitigated_count": 0,
                "failed_status": False,
                "width": round(width, 8),
                "width_to_atr_ratio": round(width_to_atr, 4),
                "quality_score": round(max(0.0, min(10.0, quality)), 2),
                "too_wide": width_to_atr > max_width_atr,
            }
        )
    return blocks


def detect_order_block_failure(
    df: Any,
    order_blocks: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Detect OB failure by close and keep wick-only failures distinguishable."""
    candles = _candles(df)
    blocks = [order_blocks] if isinstance(order_blocks, Mapping) else list(order_blocks)
    break_buffer = float(_cfg(config, "failure_break_buffer", _cfg(config, "break_buffer", 0.0)))
    failures = []
    for block in blocks:
        ob_type = str(block.get("ob_type", ""))
        possible_direction = (
            "bullish"
            if ob_type == "bearish_order_block"
            else "bearish" if ob_type == "bullish_order_block" else "unknown"
        )
        if possible_direction == "unknown":
            continue
        zone_low = float(block["zone_low"])
        zone_high = float(block["zone_high"])
        start = int(block.get("valid_from_position", block.get("source_position", 0))) + 1
        wick_candidate = None
        for pos in range(start, len(candles)):
            candle = candles[pos]
            if possible_direction == "bullish":
                wick_beyond = candle.high > zone_high + break_buffer
                close_beyond = candle.close > zone_high + break_buffer
                distance = candle.close - zone_high
            else:
                wick_beyond = candle.low < zone_low - break_buffer
                close_beyond = candle.close < zone_low - break_buffer
                distance = zone_low - candle.close
            if not wick_beyond and not close_beyond:
                continue
            strength = 5.0 + min(
                3.0, max(distance, 0.0) / _atr(candles, int(_cfg(config, "atr_period", 14)), pos) * 2.0
            )
            event = {
                "original_ob_id": block["ob_id"],
                "failed_at_index": candle.index,
                "failed_at_position": pos,
                "failed_at_time": candle.timestamp,
                "failure_direction": possible_direction,
                "failure_close": candle.close,
                "close_beyond_zone": bool(close_beyond),
                "wick_beyond_zone": bool(wick_beyond),
                "wick_only_failure": bool(wick_beyond and not close_beyond),
                "break_distance": round(max(distance, 0.0), 8),
                "failure_strength_score": round(max(0.0, min(10.0, strength)), 2),
                "possible_breaker_direction": possible_direction,
            }
            if close_beyond:
                failures.append(event)
                break
            wick_candidate = event
        else:
            if wick_candidate is not None:
                failures.append(wick_candidate)
    return failures


def _acceptance(
    candles: Sequence[_Candle],
    ob: Mapping[str, Any],
    failure: Mapping[str, Any],
    config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    direction = _direction(failure.get("failure_direction"))
    if not failure.get("close_beyond_zone"):
        return {
            "acceptance_confirmed": False,
            "acceptance_type": "wick_only_failure",
            "accepted_at_index": None,
            "accepted_at_position": None,
            "accepted_at_time": None,
            "close_count_beyond_zone": 0,
            "displacement_after_failure": False,
            "structure_shift_after_failure": None,
            "acceptance_score": 0.0,
        }
    zone_low = float(ob["zone_low"])
    zone_high = float(ob["zone_high"])
    start = int(failure["failed_at_position"])
    close_count = 0
    accepted_position = start
    for pos in range(start, min(len(candles), start + int(_cfg(config, "acceptance_wait_candles", 3)))):
        candle = candles[pos]
        if direction == "bullish" and candle.close > zone_high:
            close_count += 1
            accepted_position = pos
        elif direction == "bearish" and candle.close < zone_low:
            close_count += 1
            accepted_position = pos
        else:
            break
    failure_candle = candles[start]
    displacement_after_failure = failure_candle.body_to_range >= float(
        _cfg(config, "min_body_to_range", 0.55)
    ) and failure_candle.range / _atr(candles, int(_cfg(config, "atr_period", 14)), start) >= float(
        _cfg(config, "displacement_min_range_to_atr", 0.55)
    )
    score = 5.5 + min(2.0, close_count) + (1.5 if displacement_after_failure else 0.0)
    accepted = close_count >= int(_cfg(config, "min_acceptance_closes", 1)) or displacement_after_failure
    return {
        "acceptance_confirmed": accepted,
        "acceptance_type": "two_closes_beyond_zone" if close_count >= 2 else "close_beyond_zone_plus_displacement",
        "accepted_at_index": candles[accepted_position].index if accepted else None,
        "accepted_at_position": accepted_position if accepted else None,
        "accepted_at_time": candles[accepted_position].timestamp if accepted else None,
        "close_count_beyond_zone": close_count,
        "displacement_after_failure": displacement_after_failure,
        "structure_shift_after_failure": (
            {
                "type": f"{direction}_mss",
                "confirmed_by_close": True,
                "confirmation_index": failure["failed_at_index"],
            }
            if accepted
            else None
        ),
        "acceptance_score": round(max(0.0, min(10.0, score if accepted else 0.0)), 2),
    }


def detect_breaker_block(
    df: Any,
    original_order_block: Mapping[str, Any],
    failure_event: Mapping[str, Any],
    structure_events: Sequence[Mapping[str, Any]] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Create a breaker only from a valid failed OB with acceptance."""
    candles = _candles(df)
    direction = _direction(failure_event.get("possible_breaker_direction", failure_event.get("failure_direction")))
    if original_order_block.get("ob_type") == "bearish_order_block" and direction != "bullish":
        return None
    if original_order_block.get("ob_type") == "bullish_order_block" and direction != "bearish":
        return None
    acceptance = _acceptance(candles, original_order_block, failure_event, config)
    structure_shift = acceptance.get("structure_shift_after_failure")
    if structure_events:
        structure_shift = next(
            (dict(event) for event in structure_events if _direction(event.get("direction")) == direction),
            structure_shift,
        )
    if not failure_event.get("close_beyond_zone") or not acceptance["acceptance_confirmed"] or not structure_shift:
        return None
    zone_low = float(original_order_block["zone_low"])
    zone_high = float(original_order_block["zone_high"])
    confidence = (
        float(original_order_block.get("quality_score", 0.0))
        + float(failure_event.get("failure_strength_score", 0.0))
        + float(acceptance.get("acceptance_score", 0.0))
    ) / 3.0
    return {
        "breaker_id": f"{direction.upper()}_BREAKER_FROM_{original_order_block['ob_id']}",
        "breaker_type": f"{direction}_breaker",
        "direction": direction,
        "original_ob_id": original_order_block["ob_id"],
        "original_ob_type": original_order_block["ob_type"],
        "zone_low": round(zone_low, 8),
        "zone_high": round(zone_high, 8),
        "mean_threshold": round((zone_low + zone_high) / 2.0, 8),
        "failed_at_index": failure_event["failed_at_index"],
        "failed_at_position": failure_event["failed_at_position"],
        "accepted_at_index": acceptance["accepted_at_index"],
        "accepted_at_position": acceptance["accepted_at_position"],
        "accepted_at_time": acceptance["accepted_at_time"],
        "acceptance": acceptance,
        "structure_shift_event": structure_shift,
        "active_status": True,
        "retest_status": "waiting",
        "confidence_score": round(max(0.0, min(10.0, confidence)), 2),
        "width": round(zone_high - zone_low, 8),
        "width_to_atr_ratio": original_order_block.get("width_to_atr_ratio", 0.0),
        "too_wide": bool(original_order_block.get("too_wide", False)),
    }


def detect_breaker_retest(
    df: Any,
    breaker_block: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect retest after breaker acceptance, never before."""
    candles = _candles(df)
    direction = _direction(breaker_block.get("direction", breaker_block.get("breaker_type")))
    zone_low = float(breaker_block["zone_low"])
    zone_high = float(breaker_block["zone_high"])
    mean_threshold = float(breaker_block["mean_threshold"])
    start = int(breaker_block.get("accepted_at_position", breaker_block.get("failed_at_position", 0))) + 1
    max_wait = int(_cfg(config, "max_retest_wait_candles", 12))
    for pos in range(start, min(len(candles), start + max_wait + 1)):
        candle = candles[pos]
        if direction == "bullish":
            if candle.close < zone_low:
                return _retest_result(candle, "invalidated", True, False, 0.0, candle.close)
            if candle.low <= zone_high and candle.high >= zone_low:
                touched_mean = candle.low <= mean_threshold
                depth = "mean_threshold_touched" if touched_mean else "shallow_touch"
                quality = 7.0 + (1.0 if touched_mean else 0.0)
                return _retest_result(candle, depth, False, touched_mean, quality, candle.low)
        elif direction == "bearish":
            if candle.close > zone_high:
                return _retest_result(candle, "invalidated", True, False, 0.0, candle.close)
            if candle.high >= zone_low and candle.low <= zone_high:
                touched_mean = candle.high >= mean_threshold
                depth = "mean_threshold_touched" if touched_mean else "shallow_touch"
                quality = 7.0 + (1.0 if touched_mean else 0.0)
                return _retest_result(candle, depth, False, touched_mean, quality, candle.high)
    return {
        "retest_detected": False,
        "retest_index": None,
        "retest_position": None,
        "retest_time": None,
        "retest_depth": "none",
        "touched_mean_threshold": False,
        "invalidated_on_retest": False,
        "touched_price": None,
        "retest_quality_score": 0.0,
    }


def _retest_result(
    candle: _Candle, depth: str, invalidated: bool, touched_mean: bool, quality: float, touched_price: float
) -> dict[str, Any]:
    return {
        "retest_detected": not invalidated,
        "retest_index": candle.index,
        "retest_position": candle.position,
        "retest_time": candle.timestamp,
        "retest_depth": depth,
        "touched_mean_threshold": touched_mean,
        "invalidated_on_retest": invalidated,
        "touched_price": round(touched_price, 8),
        "retest_quality_score": round(max(0.0, min(10.0, quality)), 2),
    }


def validate_breaker_reaction(
    df: Any,
    breaker_block: Mapping[str, Any],
    retest_event: Mapping[str, Any],
    ltf_context: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate that price reacts from the breaker instead of merely touching it."""
    candles = _candles(df)
    direction = _direction(breaker_block.get("direction", breaker_block.get("breaker_type")))
    reasons: list[str] = []
    if not retest_event.get("retest_detected"):
        reasons.append("waiting_for_breaker_retest")
    if retest_event.get("invalidated_on_retest"):
        reasons.append("breaker_invalidated_on_retest")
    if reasons:
        return {
            "reaction_confirmed": False,
            "confirmation_type": "none",
            "confirmation_index": None,
            "confirmation_time": None,
            "reaction_strength_score": 0.0,
            "entry_triggered": False,
            "entry_price": None,
            "rejection_reasons": reasons,
        }

    ltf_context = ltf_context or {}
    confirmation_mode = str(_cfg(config, "confirmation_mode", BreakerConfirmationMode.CANDLE_REACTION.value)).lower()
    retest_position = int(retest_event.get("retest_position", 0))
    wait = int(_cfg(config, "reaction_wait_candles", 3))
    mean_threshold = float(breaker_block["mean_threshold"])
    zone_low = float(breaker_block["zone_low"])
    zone_high = float(breaker_block["zone_high"])

    if confirmation_mode == BreakerConfirmationMode.AGGRESSIVE.value:
        candle = candles[retest_position]
        return {
            "reaction_confirmed": True,
            "confirmation_type": "aggressive_retest_entry",
            "confirmation_index": candle.index,
            "confirmation_time": candle.timestamp,
            "reaction_strength_score": 6.5,
            "entry_triggered": True,
            "entry_price": round(float(retest_event.get("touched_price") or mean_threshold), 8),
            "rejection_reasons": [],
        }

    if confirmation_mode == BreakerConfirmationMode.LTF_MSS.value:
        if direction == "bullish":
            ltf_ok = (
                ltf_context.get("sell_side_sweep_inside_breaker")
                and ltf_context.get("bullish_mss_confirmed")
                and ltf_context.get("bullish_displacement_confirmed")
            )
        else:
            ltf_ok = (
                ltf_context.get("buy_side_sweep_inside_breaker")
                and ltf_context.get("bearish_mss_confirmed")
                and ltf_context.get("bearish_displacement_confirmed")
            )
        if ltf_ok:
            entry = float(ltf_context.get("entry_price", retest_event.get("touched_price") or mean_threshold))
            return {
                "reaction_confirmed": True,
                "confirmation_type": "ltf_mss_inside_breaker",
                "confirmation_index": ltf_context.get("confirmation_index", retest_event.get("retest_index")),
                "confirmation_time": ltf_context.get("confirmation_time", retest_event.get("retest_time")),
                "reaction_strength_score": 9.0,
                "entry_triggered": True,
                "entry_price": round(entry, 8),
                "rejection_reasons": [],
            }
        return {
            "reaction_confirmed": False,
            "confirmation_type": "ltf_mss_missing",
            "confirmation_index": None,
            "confirmation_time": None,
            "reaction_strength_score": 0.0,
            "entry_triggered": False,
            "entry_price": None,
            "rejection_reasons": ["no_breaker_reaction"],
        }

    for pos in range(retest_position, min(len(candles), retest_position + wait + 1)):
        candle = candles[pos]
        if direction == "bullish":
            reacted = candle.bullish and candle.close > mean_threshold and candle.close >= zone_low
            strength = 6.5 + min(
                2.5, (candle.close - mean_threshold) / _atr(candles, int(_cfg(config, "atr_period", 14)), pos) * 2.0
            )
        else:
            reacted = candle.bearish and candle.close < mean_threshold and candle.close <= zone_high
            strength = 6.5 + min(
                2.5, (mean_threshold - candle.close) / _atr(candles, int(_cfg(config, "atr_period", 14)), pos) * 2.0
            )
        if reacted:
            return {
                "reaction_confirmed": True,
                "confirmation_type": "candle_reaction_from_breaker",
                "confirmation_index": candle.index,
                "confirmation_time": candle.timestamp,
                "reaction_strength_score": round(max(0.0, min(10.0, strength)), 2),
                "entry_triggered": True,
                "entry_price": round(candle.close, 8),
                "rejection_reasons": [],
            }

    return {
        "reaction_confirmed": False,
        "confirmation_type": "none",
        "confirmation_index": None,
        "confirmation_time": None,
        "reaction_strength_score": 0.0,
        "entry_triggered": False,
        "entry_price": None,
        "rejection_reasons": ["no_breaker_reaction"],
    }


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


def _select_target(
    direction: str, entry_price: float, liquidity_pools: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    target_side = _target_side(direction)
    candidates = []
    for pool in liquidity_pools or []:
        price = _pool_level(pool)
        if price is None or _pool_side(pool) != target_side:
            continue
        swept = bool(pool.get("swept", pool.get("already_swept", False)))
        if direction == "bullish" and price <= entry_price:
            continue
        if direction == "bearish" and price >= entry_price:
            continue
        distance = abs(price - entry_price)
        candidates.append((swept, distance, pool, price))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    swept, _, pool, price = candidates[0]
    return {
        "side": target_side,
        "price": round(price, 8),
        "reference": pool.get("id", pool.get("name", "liquidity_pool")),
        "already_swept": swept,
    }


def _risk(
    direction: str,
    entry_price: float,
    breaker_block: Mapping[str, Any],
    candles: Sequence[_Candle],
    config: Mapping[str, Any] | None,
    spread_status: Mapping[str, Any] | None,
) -> dict[str, Any]:
    spread_status = spread_status or {}
    atr_value = _atr(candles, int(_cfg(config, "atr_period", 14)))
    spread_points = float(spread_status.get("spread_points", spread_status.get("spread", 0.0)) or 0.0)
    buffer = max(float(_cfg(config, "stop_buffer", 0.05)), atr_value * float(_cfg(config, "stop_atr_buffer", 0.05)))
    buffer += spread_points * float(_cfg(config, "spread_point_value", 0.01))
    if direction == "bullish":
        stop_loss = float(breaker_block["zone_low"]) - buffer
    else:
        stop_loss = float(breaker_block["zone_high"]) + buffer
    return {
        "stop_loss": round(stop_loss, 8),
        "stop_source": "beyond_breaker_zone_with_spread_buffer",
        "stop_buffer": round(buffer, 8),
    }


def _rr(direction: str, entry_price: float, stop_loss: float, target_price: float) -> tuple[float, float, float]:
    risk_distance = abs(entry_price - stop_loss)
    if direction == "bullish":
        reward_distance = target_price - entry_price
    else:
        reward_distance = entry_price - target_price
    if risk_distance <= 0:
        return risk_distance, reward_distance, 0.0
    return risk_distance, reward_distance, reward_distance / risk_distance


def score_breaker_setup(
    setup: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a breaker setup from 0 to 10 with hard-filter awareness."""
    context = context or {}
    breaker = setup.get("breaker_block", {}) or {}
    original_ob = setup.get("original_order_block", {}) or {}
    failure = setup.get("failure_event", {}) or {}
    retest = setup.get("retest", {}) or {}
    reaction = setup.get("reaction", {}) or {}
    htf_bias = context.get("htf_bias", {}) or {}
    rr_value = float(setup.get("rr", setup.get("risk", {}).get("rr", 0.0)) or 0.0)
    min_rr = float(_cfg(config, "min_rr", 2.0))

    direction = _direction(setup.get("direction", breaker.get("direction")))
    htf_direction = _direction(htf_bias.get("bias_direction", htf_bias.get("direction")))
    components = {
        "original_ob_quality": float(original_ob.get("quality_score", 0.0) or 0.0),
        "ob_failure_quality": float(failure.get("failure_strength_score", 0.0) or 0.0),
        "acceptance_beyond_ob": float(breaker.get("acceptance", {}).get("acceptance_score", 0.0) or 0.0),
        "structure_shift": 8.5 if breaker.get("structure_shift_event") else 0.0,
        "breaker_zone_quality": 4.0 if breaker.get("too_wide") else 8.0,
        "retest_quality": float(retest.get("retest_quality_score", 0.0) or 0.0),
        "reaction_confirmation": float(reaction.get("reaction_strength_score", 0.0) or 0.0),
        "htf_alignment": 8.5 if htf_direction in {"unknown", direction} else 4.0,
        "target_rr": min(10.0, rr_value / max(min_rr, 1e-9) * 8.0),
        "xauusd_safety": 9.0,
    }

    spread_status = context.get("spread_status", {}) or {}
    if spread_status.get("spread_safe") is False or str(spread_status.get("status", "")).lower() in {"unsafe", "high"}:
        components["xauusd_safety"] -= 4.0
    if (context.get("news_status", {}) or {}).get("restricted", False):
        components["xauusd_safety"] -= 4.0
    if str(context.get("market_condition", "")).lower() in {"choppy", "range", "ranging"}:
        components["breaker_zone_quality"] -= 2.0

    components = {key: round(max(0.0, min(10.0, value)), 2) for key, value in components.items()}
    weights = {
        "original_ob_quality": 1.0,
        "ob_failure_quality": 1.1,
        "acceptance_beyond_ob": 1.2,
        "structure_shift": 1.2,
        "breaker_zone_quality": 0.9,
        "retest_quality": 1.0,
        "reaction_confirmation": 1.2,
        "htf_alignment": 0.8,
        "target_rr": 1.0,
        "xauusd_safety": 0.8,
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


def _rejected(symbol: str, reasons: Sequence[str], details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    clean = []
    for reason in reasons:
        if reason and reason not in clean:
            clean.append(reason)
    return {
        "strategy": "Breaker Block Strategy",
        "symbol": symbol,
        "signal_status": BreakerStatus.REJECTED.value,
        "trade_allowed": False,
        "rejection_reasons": clean,
        "details": dict(details or {}),
    }


def generate_breaker_signal(context: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Generate a breaker block signal or deterministic rejection decision."""
    symbol = str(context.get("symbol", "XAUUSD"))
    df = context.get("setup_df", context.get("m15_df", context.get("candles", context.get("df", []))))
    entry_df = context.get("entry_df", context.get("m5_df", df))
    candles = _candles(df)
    if len(candles) < 6:
        return _rejected(symbol, ["insufficient_closed_candles"])

    safety_reasons: list[str] = []
    news_status = context.get("news_status", {}) or {}
    if news_status.get("restricted", news_status.get("is_restricted", False)):
        safety_reasons.append("news_restricted")
    spread_status = context.get("spread_status", {}) or {}
    if spread_status.get("spread_safe") is False or str(spread_status.get("status", "")).lower() in {"unsafe", "high"}:
        safety_reasons.append("spread_too_high_or_caution")

    if "order_blocks" in context:
        order_blocks = list(context.get("order_blocks") or [])
    else:
        order_blocks = detect_order_blocks(df, context.get("swings"), context.get("structure_events"), config)
    if not order_blocks:
        return _rejected(symbol, safety_reasons + ["no_original_order_block"])

    failures = detect_order_block_failure(df, order_blocks, config)
    if not failures:
        return _rejected(
            symbol, safety_reasons + ["no_order_block_failure"], {"order_blocks_detected": len(order_blocks)}
        )

    best_rejected: dict[str, Any] | None = None
    collected_reasons: list[str] = []
    for failure in failures:
        original = next((block for block in order_blocks if block.get("ob_id") == failure.get("original_ob_id")), None)
        if not original:
            collected_reasons.append("no_original_order_block")
            continue
        if not original.get("structure_break_confirmed") or not original.get("created_by_displacement"):
            collected_reasons.append("original_ob_invalid")
            continue
        if failure.get("wick_only_failure"):
            direction = _direction(failure.get("failure_direction"))
            collected_reasons.extend(
                [
                    "wick_only_ob_failure",
                    "no_acceptance_beyond_ob",
                    "no_bullish_structure_shift" if direction == "bullish" else "no_bearish_structure_shift",
                ]
            )
            best_rejected = _rejected(symbol, safety_reasons + collected_reasons, {"failure_event": failure})
            continue

        breaker = detect_breaker_block(df, original, failure, context.get("structure_events"), config)
        if not breaker:
            direction = _direction(failure.get("failure_direction"))
            reasons = ["no_valid_breaker"]
            if not failure.get("close_beyond_zone"):
                reasons.extend(["wick_only_ob_failure", "no_acceptance_beyond_ob"])
            reasons.append("no_bullish_structure_shift" if direction == "bullish" else "no_bearish_structure_shift")
            collected_reasons.extend(reasons)
            best_rejected = _rejected(
                symbol, safety_reasons + reasons, {"original_order_block": original, "failure_event": failure}
            )
            continue

        retest = detect_breaker_retest(entry_df, breaker, config)
        if retest.get("invalidated_on_retest"):
            best_rejected = _rejected(
                symbol, safety_reasons + ["breaker_invalidated_on_retest"], {"breaker_block": breaker, "retest": retest}
            )
            collected_reasons.append("breaker_invalidated_on_retest")
            continue
        if not retest.get("retest_detected"):
            best_rejected = _rejected(
                symbol, safety_reasons + ["waiting_for_breaker_retest"], {"breaker_block": breaker}
            )
            collected_reasons.append("waiting_for_breaker_retest")
            continue

        reaction = validate_breaker_reaction(entry_df, breaker, retest, context.get("ltf_context"), config)
        if not reaction.get("reaction_confirmed"):
            reasons = reaction.get("rejection_reasons") or ["no_breaker_reaction"]
            best_rejected = _rejected(symbol, safety_reasons + reasons, {"breaker_block": breaker, "retest": retest})
            collected_reasons.extend(reasons)
            continue

        direction = _direction(breaker.get("direction"))
        entry_price = float(reaction["entry_price"])
        target = _select_target(direction, entry_price, context.get("liquidity_pools", []))
        rejection_reasons = list(safety_reasons)
        if not target:
            rejection_reasons.append("no_valid_target")
        elif target.get("already_swept"):
            rejection_reasons.append("target_already_swept")
        if breaker.get("too_wide") or float(breaker.get("width_to_atr_ratio", 0.0) or 0.0) > float(
            _cfg(config, "max_breaker_width_atr", 3.0)
        ):
            rejection_reasons.append("breaker_zone_too_wide")
        if context.get("htf_poi_blocks_target"):
            rejection_reasons.append("htf_poi_blocks_target")

        risk = _risk(direction, entry_price, breaker, candles, config, spread_status)
        target_price = float(target["price"]) if target else entry_price
        risk_distance, reward_distance, rr_value = _rr(direction, entry_price, float(risk["stop_loss"]), target_price)
        min_rr = float(_cfg(config, "min_rr", 2.0))
        if rr_value < min_rr:
            rejection_reasons.append("rr_below_minimum")

        setup = {
            "strategy": "Breaker Block Strategy",
            "symbol": symbol,
            "signal_id": f"{symbol}_BREAKER_{direction.upper()}_{failure['failed_at_index']}",
            "signal_status": BreakerStatus.VALID.value,
            "trade_allowed": False,
            "uses_closed_candles": all(c.is_closed for c in candles),
            "direction": direction,
            "breaker_type": breaker["breaker_type"],
            "original_ob_type": original["ob_type"],
            "original_ob_id": original["ob_id"],
            "original_order_block": original,
            "failure_event": failure,
            "breaker_block": breaker,
            "retest": retest,
            "reaction": reaction,
            "entry": {"entry_type": "breaker_reaction_entry", "entry_price": round(entry_price, 8)},
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
                "spread_filter": "failed" if "spread_too_high_or_caution" in rejection_reasons else "passed",
                "breaker_width_filter": "failed" if "breaker_zone_too_wide" in rejection_reasons else "passed",
                "target_filter": (
                    "failed" if {"no_valid_target", "target_already_swept"} & set(rejection_reasons) else "passed"
                ),
                "rr_filter": "failed" if "rr_below_minimum" in rejection_reasons else "passed",
            },
            "rejection_reasons": rejection_reasons,
        }
        setup["score"] = score_breaker_setup(setup, context, config)
        setup["trade_allowed"] = bool(setup["score"]["trade_allowed"])
        if setup["trade_allowed"]:
            return setup
        best_rejected = _rejected(
            symbol,
            rejection_reasons or setup["score"]["hard_filter_failures"],
            {
                "direction_candidate": direction,
                "breaker_type": breaker["breaker_type"],
                "score": setup["score"],
                "rr": round(rr_value, 4),
            },
        )
        collected_reasons.extend(rejection_reasons)

    return best_rejected or _rejected(symbol, safety_reasons + (collected_reasons or ["no_valid_breaker"]))
