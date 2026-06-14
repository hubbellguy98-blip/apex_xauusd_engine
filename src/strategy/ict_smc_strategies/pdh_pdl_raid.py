"""Previous Day High / Previous Day Low raid strategy model.

The model treats PDH and PDL as external liquidity, but it does not fade them
blindly. A tradable reversal requires:

previous completed day levels -> raid beyond PDH/PDL -> reclaim/rejection ->
post-raid MSS/displacement -> FVG/OB entry POI -> retracement -> target/RR checks.

This module is deterministic, closed-candle only, and never places orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence


class PDHPDLStatus(str, Enum):
    VALID = "valid"
    REJECTED = "rejected"
    CONTEXT_ONLY = "context_only"
    WAITING_FOR_RETEST = "waiting_for_retest"


class RaidDirection(str, Enum):
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
    if raw in {"bull", "buy", "long", "bullish", "pdl_raid", "sell_side"}:
        return "bullish"
    if raw in {"bear", "sell", "short", "bearish", "pdh_raid", "buy_side"}:
        return "bearish"
    return "unknown"


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


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


def _pdh(levels: Mapping[str, Any]) -> float:
    return float(levels.get("pdh", levels.get("pdh_price")))


def _pdl(levels: Mapping[str, Any]) -> float:
    return float(levels.get("pdl", levels.get("pdl_price")))


def calculate_previous_day_levels(
    daily_or_session_df: Any,
    current_timestamp: Any | None = None,
    session_config: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate PDH/PDL from the previous completed daily/session candle."""
    candles = _candles(daily_or_session_df)
    if not candles:
        return {"valid_status": False, "rejection_reasons": ["missing_daily_or_session_data"]}

    current_dt = _parse_time(current_timestamp)
    eligible = candles
    if current_dt is not None:
        eligible = [
            candle
            for candle in candles
            if (_parse_time(candle.timestamp) is None or _parse_time(candle.timestamp).date() < current_dt.date())
        ]
    if not eligible:
        return {
            "valid_status": False,
            "rejection_reasons": ["no_completed_previous_day_candle"],
        }

    previous = eligible[-1]
    if previous.high <= previous.low:
        return {
            "valid_status": False,
            "rejection_reasons": ["invalid_previous_day_range"],
        }

    return {
        "date": str(_value(_rows(daily_or_session_df)[previous.position], "date", previous.timestamp)),
        "pdh": round(previous.high, 8),
        "pdl": round(previous.low, 8),
        "pdh_price": round(previous.high, 8),
        "pdl_price": round(previous.low, 8),
        "previous_day_open": round(previous.open, 8),
        "previous_day_close": round(previous.close, 8),
        "pdh_time": _value(_rows(daily_or_session_df)[previous.position], "pdh_time", previous.timestamp),
        "pdl_time": _value(_rows(daily_or_session_df)[previous.position], "pdl_time", previous.timestamp),
        "previous_session_start": _value(
            _rows(daily_or_session_df)[previous.position],
            "session_start",
            _value(_rows(daily_or_session_df)[previous.position], "previous_session_start", previous.timestamp),
        ),
        "previous_session_end": _value(
            _rows(daily_or_session_df)[previous.position],
            "session_end",
            _value(_rows(daily_or_session_df)[previous.position], "previous_session_end", previous.timestamp),
        ),
        "timezone": (session_config or {}).get("timezone", _cfg(config, "timezone", "UTC")),
        "valid_from_time": _value(
            _rows(daily_or_session_df)[previous.position],
            "valid_from_time",
            _value(_rows(daily_or_session_df)[previous.position], "session_end", previous.timestamp),
        ),
        "valid_status": True,
        "uses_completed_previous_day": True,
        "rejection_reasons": [],
    }


def detect_pdh_pdl_raid(
    df: Any,
    previous_day_levels: Mapping[str, Any],
    raid_window: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect a raid beyond PDH or PDL without assuming it is tradable yet."""
    candles = _candles(df)
    if not previous_day_levels.get("valid_status", False):
        return {
            "raid_detected": False,
            "rejection_reasons": ["invalid_previous_day_levels"],
        }
    if not candles:
        return {"raid_detected": False, "rejection_reasons": ["insufficient_closed_candles"]}

    start, end = _window_positions(candles, raid_window, 0, len(candles) - 1)
    pdh = _pdh(previous_day_levels)
    pdl = _pdl(previous_day_levels)
    raid_buffer = float(_cfg(config, "raid_buffer", 0.05))
    min_raid_depth = float(_cfg(config, "min_raid_depth", 0.05))
    max_raid_atr = float(_cfg(config, "max_raid_atr_multiplier", 3.5))
    candidates: list[dict[str, Any]] = []
    sides_seen: set[str] = set()

    for pos in range(start, end + 1):
        candle = candles[pos]
        possible: list[tuple[str, str, str, float, float]] = []
        if candle.low < pdl - raid_buffer:
            possible.append(("pdl_raid", "sell_side", "bullish", pdl - candle.low, candle.low))
        if candle.high > pdh + raid_buffer:
            possible.append(("pdh_raid", "buy_side", "bearish", candle.high - pdh, candle.high))
        for raid_type, side, direction, depth, extreme in possible:
            sides_seen.add(side)
            atr_value = _atr(candles, int(_cfg(config, "atr_period", 14)), pos)
            reasons = []
            if depth < min_raid_depth:
                reasons.append("raid_depth_too_small")
            if depth / max(atr_value, 1e-9) > max_raid_atr:
                reasons.append("abnormal_raid_range")
            score = 6.0 + min(2.5, depth / max(atr_value, 1e-9) * 2.0)
            if candle.body_to_range < 0.45:
                score += 0.6
            candidates.append(
                {
                    "raid_detected": not reasons,
                    "raid_type": raid_type,
                    "swept_side": side,
                    "swept_level": round(pdl if raid_type == "pdl_raid" else pdh, 8),
                    "raid_extreme": round(extreme, 8),
                    "raid_depth": round(depth, 8),
                    "raid_index": candle.index,
                    "raid_position": pos,
                    "raid_time": candle.timestamp,
                    "direction": direction,
                    "direction_bias": f"{direction}_candidate",
                    "raid_quality_score": round(max(0.0, min(10.0, score if not reasons else 3.0)), 2),
                    "both_sides_swept": False,
                    "rejection_reasons": reasons,
                }
            )

    double_sided = len(sides_seen) > 1
    if double_sided:
        for item in candidates:
            item["both_sides_swept"] = True
            if not bool(_cfg(config, "allow_double_sided_raid", False)):
                item["raid_detected"] = False
                if "double_sided_raid_no_clear_direction" not in item["rejection_reasons"]:
                    item["rejection_reasons"].append("double_sided_raid_no_clear_direction")

    valid = [item for item in candidates if item["raid_detected"]]
    if valid:
        valid.sort(key=lambda item: (int(item["raid_position"]), -float(item["raid_quality_score"])))
        return valid[0]
    if candidates:
        candidates.sort(key=lambda item: (int(item["raid_position"]), -float(item["raid_quality_score"])))
        return candidates[0]
    return {"raid_detected": False, "rejection_reasons": ["no_pdh_pdl_raid"]}


def detect_reclaim_or_rejection(
    df: Any,
    raid_event: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Confirm reclaim/rejection or classify accepted breakout beyond PDH/PDL."""
    candles = _candles(df)
    if not raid_event.get("raid_detected"):
        return {
            "reclaim_or_rejection_confirmed": False,
            "status": "no_raid",
            "rejection_reasons": list(raid_event.get("rejection_reasons", ["no_pdh_pdl_raid"])),
        }

    level = float(raid_event["swept_level"])
    raid_type = str(raid_event["raid_type"])
    direction = _direction(raid_event.get("direction"))
    start = int(raid_event["raid_position"])
    max_reclaim = int(_cfg(config, "max_reclaim_candles", 3))
    reclaim_buffer = float(_cfg(config, "reclaim_buffer", _cfg(config, "rejection_buffer", 0.05)))
    acceptance_buffer = float(_cfg(config, "acceptance_buffer", 0.08))

    for pos in range(start, min(len(candles), start + max_reclaim + 1)):
        candle = candles[pos]
        next_candle = candles[pos + 1] if pos + 1 < len(candles) else None
        if raid_type == "pdl_raid":
            if candle.close > level + reclaim_buffer:
                strength = 6.5 + min(3.0, candle.bullish_close_position * 3.0)
                return {
                    "reclaim_or_rejection_confirmed": True,
                    "acceptance_confirmed": False,
                    "status": "reclaimed",
                    "direction": "bullish",
                    "confirmation_index": candle.index,
                    "confirmation_position": pos,
                    "confirmation_time": candle.timestamp,
                    "close_price": round(candle.close, 8),
                    "close_position_score": round(candle.bullish_close_position, 4),
                    "strength_score": round(max(0.0, min(10.0, strength)), 2),
                    "rejection_reasons": [],
                }
            if (
                candle.close < level - acceptance_buffer
                and next_candle is not None
                and next_candle.close < level - acceptance_buffer
            ):
                return {
                    "reclaim_or_rejection_confirmed": False,
                    "acceptance_confirmed": True,
                    "status": "accepted_breakout",
                    "breakout_direction": "bearish",
                    "direction": direction,
                    "confirmation_index": next_candle.index,
                    "confirmation_position": next_candle.position,
                    "confirmation_time": next_candle.timestamp,
                    "rejection_reasons": ["pdl_accepted_breakout_not_raid"],
                }
        if raid_type == "pdh_raid":
            if candle.close < level - reclaim_buffer:
                strength = 6.5 + min(3.0, candle.bearish_close_position * 3.0)
                return {
                    "reclaim_or_rejection_confirmed": True,
                    "acceptance_confirmed": False,
                    "status": "rejected",
                    "direction": "bearish",
                    "confirmation_index": candle.index,
                    "confirmation_position": pos,
                    "confirmation_time": candle.timestamp,
                    "close_price": round(candle.close, 8),
                    "close_position_score": round(candle.bearish_close_position, 4),
                    "strength_score": round(max(0.0, min(10.0, strength)), 2),
                    "rejection_reasons": [],
                }
            if (
                candle.close > level + acceptance_buffer
                and next_candle is not None
                and next_candle.close > level + acceptance_buffer
            ):
                return {
                    "reclaim_or_rejection_confirmed": False,
                    "acceptance_confirmed": True,
                    "status": "accepted_breakout",
                    "breakout_direction": "bullish",
                    "direction": direction,
                    "confirmation_index": next_candle.index,
                    "confirmation_position": next_candle.position,
                    "confirmation_time": next_candle.timestamp,
                    "rejection_reasons": ["pdh_accepted_breakout_not_raid"],
                }

    return {
        "reclaim_or_rejection_confirmed": False,
        "acceptance_confirmed": False,
        "status": "unresolved",
        "direction": direction,
        "rejection_reasons": ["no_reclaim_or_rejection"],
    }


def _post_raid_level(candles: Sequence[_Candle], raid_position: int, pos: int, direction: str) -> float:
    window = candles[raid_position:pos]
    if not window:
        return candles[raid_position].high if direction == "bullish" else candles[raid_position].low
    return max(c.high for c in window) if direction == "bullish" else min(c.low for c in window)


def _displacement_ok(candle: _Candle, candles: Sequence[_Candle], config: Mapping[str, Any] | None) -> bool:
    return candle.body_to_range >= float(_cfg(config, "min_body_to_range", 0.55)) and candle.range / max(
        _atr(candles, int(_cfg(config, "atr_period", 14)), candle.position), 1e-9
    ) >= float(_cfg(config, "displacement_min_range_to_atr", 0.65))


def detect_post_raid_mss(
    df: Any,
    swings: Sequence[Mapping[str, Any]] | None,
    raid_event: Mapping[str, Any],
    reclaim_event: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect post-raid MSS and displacement in the reversal direction."""
    candles = _candles(df)
    if not reclaim_event.get("reclaim_or_rejection_confirmed"):
        return {
            "mss_confirmed": False,
            "displacement_confirmed": False,
            "rejection_reasons": reclaim_event.get("rejection_reasons", ["no_reclaim_or_rejection"]),
        }

    direction = _direction(reclaim_event.get("direction"))
    start = int(reclaim_event["confirmation_position"]) + 1
    raid_position = int(raid_event["raid_position"])
    max_wait = int(_cfg(config, "max_mss_wait_candles", 8))
    break_buffer = float(_cfg(config, "break_buffer", 0.05))

    for pos in range(start, min(len(candles), start + max_wait + 1)):
        candle = candles[pos]
        level = _post_raid_level(candles, raid_position, pos, direction)
        if direction == "bullish":
            mss_confirmed = candle.close > level + break_buffer
            displacement = (
                candle.bullish and _displacement_ok(candle, candles, config) and candle.bullish_close_position >= 0.62
            )
            fail_reason = "no_bullish_mss_after_pdl_raid"
        else:
            mss_confirmed = candle.close < level - break_buffer
            displacement = (
                candle.bearish and _displacement_ok(candle, candles, config) and candle.bearish_close_position >= 0.62
            )
            fail_reason = "no_bearish_mss_after_pdh_raid"
        if mss_confirmed and displacement:
            strength = 6.5 + min(
                2.5,
                candle.range / max(_atr(candles, int(_cfg(config, "atr_period", 14)), pos), 1e-9),
            )
            return {
                "mss_confirmed": True,
                "direction": direction,
                "broken_level": round(level, 8),
                "confirmation_index": candle.index,
                "confirmation_position": pos,
                "confirmation_time": candle.timestamp,
                "confirmed_by_close": True,
                "displacement_confirmed": True,
                "range_to_atr_ratio": round(
                    candle.range / max(_atr(candles, int(_cfg(config, "atr_period", 14)), pos), 1e-9),
                    4,
                ),
                "body_to_range_ratio": round(candle.body_to_range, 4),
                "close_position_score": round(
                    candle.bullish_close_position if direction == "bullish" else candle.bearish_close_position,
                    4,
                ),
                "quality_score": round(max(0.0, min(10.0, strength)), 2),
                "displacement": {
                    "direction": direction,
                    "confirmed": True,
                    "strength_score": round(max(0.0, min(10.0, strength)), 2),
                },
                "rejection_reasons": [],
            }

    return {
        "mss_confirmed": False,
        "direction": direction,
        "confirmed_by_close": False,
        "displacement_confirmed": False,
        "quality_score": 0.0,
        "rejection_reasons": ["no_post_raid_mss", fail_reason, "no_post_raid_displacement"],
    }


def _last_opposite_candle(candles: Sequence[_Candle], direction: str, start: int, end: int) -> _Candle | None:
    for pos in range(end, max(start - 1, -1), -1):
        candle = candles[pos]
        if direction == "bullish" and candle.bearish:
            return candle
        if direction == "bearish" and candle.bullish:
            return candle
    return None


def detect_post_raid_fvg_or_ob(
    df: Any,
    mss_event: Mapping[str, Any],
    displacement_event: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Find a post-MSS FVG or order block suitable for retracement entry."""
    candles = _candles(df)
    if not mss_event.get("mss_confirmed"):
        return {"entry_poi_detected": False, "rejection_reasons": ["no_post_raid_mss"]}
    if not mss_event.get("displacement_confirmed", (displacement_event or {}).get("confirmed", False)):
        return {
            "entry_poi_detected": False,
            "rejection_reasons": ["no_post_raid_displacement"],
        }

    direction = _direction(mss_event.get("direction"))
    pos = int(mss_event["confirmation_position"])
    poi: dict[str, Any] | None = None
    if pos >= 2 and direction == "bullish" and candles[pos - 2].high < candles[pos].low:
        poi = {
            "poi_type": "bullish_fvg",
            "zone_low": round(candles[pos - 2].high, 8),
            "zone_high": round(candles[pos].low, 8),
        }
    elif pos >= 2 and direction == "bearish" and candles[pos - 2].low > candles[pos].high:
        poi = {
            "poi_type": "bearish_fvg",
            "zone_low": round(candles[pos].high, 8),
            "zone_high": round(candles[pos - 2].low, 8),
        }

    if poi is None:
        ob = _last_opposite_candle(candles, direction, max(0, pos - 6), pos - 1)
        if ob is None:
            return {
                "entry_poi_detected": False,
                "rejection_reasons": ["no_valid_entry_poi"],
            }
        poi = {
            "poi_type": "bullish_order_block" if direction == "bullish" else "bearish_order_block",
            "zone_low": round(ob.low, 8),
            "zone_high": round(ob.high, 8),
        }

    zone_low = float(poi["zone_low"])
    zone_high = float(poi["zone_high"])
    max_width = float(_cfg(config, "max_poi_width", 12.0))
    if zone_high <= zone_low or zone_high - zone_low > max_width:
        return {
            "entry_poi_detected": False,
            "rejection_reasons": ["entry_poi_too_wide_or_invalid"],
        }

    poi.update(
        {
            "zone_mid": round((zone_low + zone_high) / 2.0, 8),
            "mean_threshold": round((zone_low + zone_high) / 2.0, 8),
            "created_by_displacement": True,
            "active_status": True,
            "creation_index": candles[pos].index,
            "creation_position": pos,
            "quality_score": 8.5 if "fvg" in str(poi["poi_type"]) else 7.8,
            "rejection_reasons": [],
            "entry_poi_detected": True,
        }
    )
    return poi


def _find_retest(
    candles: Sequence[_Candle],
    entry_poi: Mapping[str, Any],
    direction: str,
    config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    start = int(entry_poi.get("creation_position", 0)) + 1
    max_wait = int(_cfg(config, "max_entry_wait_candles", 6))
    zone_low = float(entry_poi["zone_low"])
    zone_high = float(entry_poi["zone_high"])
    for pos in range(start, min(len(candles), start + max_wait + 1)):
        candle = candles[pos]
        touches = candle.low <= zone_high and candle.high >= zone_low
        if not touches:
            continue
        reaction = candle.close > zone_low if direction == "bullish" else candle.close < zone_high
        return {
            "retested": True,
            "reaction_confirmed": reaction,
            "retest_index": candle.index,
            "retest_position": pos,
            "retest_time": candle.timestamp,
            "entry_price": round(candle.close if reaction else float(entry_poi["zone_mid"]), 8),
        }
    return {"retested": False, "reaction_confirmed": False}


def _pool_side(pool: Mapping[str, Any]) -> str:
    raw = str(pool.get("side", pool.get("type", ""))).lower()
    if raw in {"bsl", "buy_side", "buyside", "buy-side", "high", "equal_highs"}:
        return "buy_side"
    if raw in {"ssl", "sell_side", "sellside", "sell-side", "low", "equal_lows"}:
        return "sell_side"
    return raw


def _pool_level(pool: Mapping[str, Any]) -> float | None:
    for key in ("price", "level", "liquidity_level", "target_price"):
        if key in pool:
            try:
                return float(pool[key])
            except (TypeError, ValueError):
                return None
    return None


def _select_target(
    direction: str,
    entry_price: float,
    levels: Mapping[str, Any],
    pools: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    side = "buy_side" if direction == "bullish" else "sell_side"
    candidates: list[tuple[bool, bool, float, dict[str, Any], float]] = []
    daily_target = _pdh(levels) if direction == "bullish" else _pdl(levels)
    if (direction == "bullish" and daily_target > entry_price) or (
        direction == "bearish" and daily_target < entry_price
    ):
        candidates.append(
            (
                False,
                False,
                abs(daily_target - entry_price),
                {
                    "id": (
                        "previous_day_high_buy_side_liquidity"
                        if direction == "bullish"
                        else "previous_day_low_sell_side_liquidity"
                    ),
                    "side": side,
                },
                daily_target,
            )
        )

    for pool in pools or []:
        price = _pool_level(pool)
        if price is None or _pool_side(pool) != side:
            continue
        if direction == "bullish" and price <= entry_price:
            continue
        if direction == "bearish" and price >= entry_price:
            continue
        candidates.append(
            (
                bool(pool.get("swept", pool.get("already_swept", False))),
                True,
                abs(price - entry_price),
                dict(pool),
                price,
            )
        )

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], not item[1], -item[2]))
    swept, _, _, source, price = candidates[0]
    return {
        "side": side,
        "price": round(price, 8),
        "reference": source.get("id", source.get("name", "liquidity_pool")),
        "already_swept": swept,
    }


def _risk(
    direction: str,
    entry_price: float,
    raid_event: Mapping[str, Any],
    candles: Sequence[_Candle],
    spread_status: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    spread_status = spread_status or {}
    atr_value = _atr(candles, int(_cfg(config, "atr_period", 14)))
    spread_points = float(spread_status.get("spread_points", spread_status.get("spread", 0.0)) or 0.0)
    buffer = max(float(_cfg(config, "stop_buffer", 0.10)), atr_value * float(_cfg(config, "stop_atr_buffer", 0.08)))
    buffer += spread_points * float(_cfg(config, "spread_point_value", 0.01))
    extreme = float(raid_event["raid_extreme"])
    stop = extreme - buffer if direction == "bullish" else extreme + buffer
    return {
        "stop_loss": round(stop, 8),
        "stop_reference": (
            "below_pdl_raid_low_with_atr_and_spread_buffer"
            if direction == "bullish"
            else "above_pdh_raid_high_with_atr_and_spread_buffer"
        ),
        "stop_buffer": round(buffer, 8),
    }


def _rr(direction: str, entry_price: float, stop_loss: float, target_price: float) -> tuple[float, float, float]:
    risk_distance = abs(entry_price - stop_loss)
    reward_distance = target_price - entry_price if direction == "bullish" else entry_price - target_price
    if risk_distance <= 0:
        return risk_distance, reward_distance, 0.0
    return risk_distance, reward_distance, reward_distance / risk_distance


def score_pdh_pdl_raid_setup(
    setup: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a completed PDH/PDL raid setup from 0 to 10."""
    context = context or {}
    levels = setup.get("previous_day_levels", {}) or {}
    raid = setup.get("raid", {}) or {}
    reclaim = setup.get("reclaim_or_rejection", {}) or {}
    mss = setup.get("mss", {}) or {}
    entry_poi = setup.get("entry_poi", {}) or {}
    direction = _direction(setup.get("direction"))
    htf_direction = _direction(
        (context.get("htf_bias", {}) or {}).get("bias_direction", (context.get("htf_bias", {}) or {}).get("direction"))
    )
    rr_value = float(setup.get("risk", {}).get("rr_to_final_target", setup.get("rr", 0.0)) or 0.0)
    min_rr = float(_cfg(config, "min_rr", 2.0))
    level_quality = 8.5 if levels.get("valid_status") else 0.0
    components = {
        "pdh_pdl_level_quality": level_quality,
        "raid_quality": float(raid.get("raid_quality_score", 0.0) or 0.0),
        "reclaim_rejection": float(reclaim.get("strength_score", 0.0) or 0.0),
        "post_raid_mss": float(mss.get("quality_score", 0.0) or 0.0),
        "displacement_strength": float(
            (mss.get("displacement", {}) or {}).get("strength_score", mss.get("quality_score", 0.0)) or 0.0
        ),
        "entry_poi_quality": float(entry_poi.get("quality_score", 0.0) or 0.0),
        "htf_alignment": 8.5 if htf_direction in {"unknown", direction} else 4.0,
        "target_rr": min(10.0, rr_value / max(min_rr, 1e-9) * 8.0),
        "xauusd_safety": 9.0,
        "session_timing": float(context.get("session_score", _cfg(config, "default_session_score", 8.0))),
    }
    spread_status = context.get("spread_status", {}) or {}
    news_status = context.get("news_status", {}) or {}
    if spread_status.get("spread_safe") is False or str(spread_status.get("status", "")).lower() in {"unsafe", "high"}:
        components["xauusd_safety"] -= 4.0
    if news_status.get("restricted", news_status.get("is_restricted", False)):
        components["xauusd_safety"] -= 4.0

    components = {key: round(max(0.0, min(10.0, value)), 2) for key, value in components.items()}
    weights = {
        "pdh_pdl_level_quality": 0.8,
        "raid_quality": 1.1,
        "reclaim_rejection": 1.0,
        "post_raid_mss": 1.2,
        "displacement_strength": 1.1,
        "entry_poi_quality": 0.9,
        "htf_alignment": 0.7,
        "target_rr": 1.0,
        "xauusd_safety": 0.9,
        "session_timing": 0.7,
    }
    total = sum(components[key] * weights[key] for key in components) / sum(weights.values())
    hard_filters = list(setup.get("rejection_reasons", []))
    trade_allowed = (
        total >= float(_cfg(config, "minimum_setup_score", _cfg(config, "min_score", 7.0))) and not hard_filters
    )
    grade = (
        "A+"
        if total >= 9.0
        else "A" if total >= 8.0 else "B" if total >= 7.0 else "C" if total >= 6.0 else "D" if total >= 5.0 else "F"
    )
    return {
        "total_score": round(total, 2),
        "component_scores": components,
        "grade": grade,
        "trade_allowed": trade_allowed,
        "hard_filter_failures": hard_filters,
    }


def _decision(
    status: PDHPDLStatus, symbol: str, reasons: Sequence[str], details: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    clean = []
    for reason in reasons:
        if reason and reason not in clean:
            clean.append(reason)
    return {
        "strategy": "PDH/PDL Raid",
        "symbol": symbol,
        "signal_status": status.value,
        "trade_allowed": False,
        "rejection_reasons": clean,
        "details": dict(details or {}),
    }


def generate_pdh_pdl_raid_signal(context: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Generate a PDH/PDL raid signal or a deterministic no-trade decision."""
    symbol = str(context.get("symbol", "XAUUSD"))
    df = context.get("setup_df", context.get("m15_df", context.get("candles", context.get("df", []))))
    candles = _candles(df)
    if len(candles) < int(_cfg(config, "min_total_candles", 8)):
        return _decision(PDHPDLStatus.REJECTED, symbol, ["insufficient_closed_candles"])

    safety_reasons: list[str] = []
    news_status = context.get("news_status", {}) or {}
    if news_status.get("restricted", news_status.get("is_restricted", False)):
        safety_reasons.append("news_restricted")
    spread_status = context.get("spread_status", {}) or {}
    if spread_status.get("spread_safe") is False or str(spread_status.get("status", "")).lower() in {"unsafe", "high"}:
        safety_reasons.append("spread_too_high")

    levels = context.get("previous_day_levels") or calculate_previous_day_levels(
        context.get("daily_df", context.get("session_df", [])),
        context.get("current_timestamp"),
        context.get("session_config"),
        config,
    )
    if not levels.get("valid_status"):
        return _decision(
            PDHPDLStatus.REJECTED,
            symbol,
            safety_reasons + ["invalid_previous_day_levels"] + list(levels.get("rejection_reasons", [])),
            {"previous_day_levels": levels},
        )

    raid = detect_pdh_pdl_raid(df, levels, context.get("raid_window"), config)
    if not raid.get("raid_detected"):
        return _decision(
            PDHPDLStatus.REJECTED,
            symbol,
            safety_reasons + list(raid.get("rejection_reasons", [])),
            {"previous_day_levels": levels, "raid": raid},
        )

    reclaim = detect_reclaim_or_rejection(df, raid, config)
    if reclaim.get("acceptance_confirmed"):
        return _decision(
            PDHPDLStatus.REJECTED,
            symbol,
            safety_reasons + list(reclaim.get("rejection_reasons", [])),
            {"previous_day_levels": levels, "raid": raid, "reclaim_or_rejection": reclaim},
        )
    if not reclaim.get("reclaim_or_rejection_confirmed"):
        return _decision(
            PDHPDLStatus.CONTEXT_ONLY,
            symbol,
            safety_reasons + list(reclaim.get("rejection_reasons", [])),
            {"previous_day_levels": levels, "raid": raid, "reclaim_or_rejection": reclaim},
        )

    mss = detect_post_raid_mss(df, context.get("swings"), raid, reclaim, config)
    if not mss.get("mss_confirmed"):
        return _decision(
            PDHPDLStatus.CONTEXT_ONLY,
            symbol,
            safety_reasons + list(mss.get("rejection_reasons", [])),
            {"previous_day_levels": levels, "raid": raid, "reclaim_or_rejection": reclaim, "mss": mss},
        )

    entry_poi = detect_post_raid_fvg_or_ob(df, mss, mss.get("displacement"), config)
    if not entry_poi.get("entry_poi_detected"):
        return _decision(
            PDHPDLStatus.REJECTED,
            symbol,
            safety_reasons + list(entry_poi.get("rejection_reasons", [])),
            {
                "previous_day_levels": levels,
                "raid": raid,
                "reclaim_or_rejection": reclaim,
                "mss": mss,
                "entry_poi": entry_poi,
            },
        )

    direction = _direction(mss.get("direction"))
    retest = _find_retest(candles, entry_poi, direction, config)
    if not retest.get("retested"):
        return _decision(
            PDHPDLStatus.WAITING_FOR_RETEST,
            symbol,
            safety_reasons + ["waiting_for_fvg_or_ob_retest"],
            {
                "previous_day_levels": levels,
                "raid": raid,
                "reclaim_or_rejection": reclaim,
                "mss": mss,
                "entry_poi": entry_poi,
            },
        )
    if not retest.get("reaction_confirmed") and bool(_cfg(config, "require_entry_reaction", True)):
        return _decision(
            PDHPDLStatus.REJECTED,
            symbol,
            safety_reasons + ["no_entry_confirmation"],
            {"entry_poi": entry_poi, "retest": retest},
        )

    entry_price = float(retest["entry_price"])
    target = _select_target(direction, entry_price, levels, context.get("liquidity_pools", []))
    rejection_reasons = list(safety_reasons)
    if not target:
        rejection_reasons.append("no_valid_target")
    elif target.get("already_swept"):
        rejection_reasons.append("target_already_swept")
    if context.get("htf_poi_blocks_target"):
        rejection_reasons.append("htf_poi_blocks_target")

    risk = _risk(direction, entry_price, raid, candles, spread_status, config)
    target_price = float(target["price"]) if target else entry_price
    risk_distance, reward_distance, rr_value = _rr(direction, entry_price, float(risk["stop_loss"]), target_price)
    min_rr = float(_cfg(config, "min_rr", 2.0))
    min_target_distance = float(_cfg(config, "minimum_target_distance", 0.10))
    if risk_distance <= 0 or reward_distance <= 0:
        rejection_reasons.append("invalid_risk_reward")
    if abs(target_price - entry_price) < min_target_distance:
        rejection_reasons.append("target_too_close")
    if rr_value < min_rr:
        rejection_reasons.append("rr_below_minimum")

    setup = {
        "strategy": "PDH/PDL Raid",
        "symbol": symbol,
        "signal_id": f"{symbol}_{raid['raid_type'].upper()}_{direction.upper()}_{mss['confirmation_index']}",
        "signal_status": PDHPDLStatus.VALID.value,
        "uses_closed_candles": all(c.is_closed for c in candles),
        "direction": direction,
        "previous_day_levels": levels,
        "raid": raid,
        "reclaim_or_rejection": reclaim,
        "mss": mss,
        "displacement": mss.get("displacement", {}),
        "entry_poi": {**entry_poi, "retest_status": "retested", "reaction_confirmed": retest.get("reaction_confirmed")},
        "entry": {
            "entry_type": f"{entry_poi['poi_type']}_retest_reaction_entry",
            "entry_price": round(entry_price, 8),
            "entry_time": retest.get("retest_time"),
        },
        "target": target,
        "risk": {
            **risk,
            "target_1": round(target_price, 8) if target else None,
            "final_target": round(target_price, 8) if target else None,
            "final_target_reference": target.get("reference") if target else None,
            "risk_distance": round(risk_distance, 8),
            "reward_distance": round(reward_distance, 8),
            "rr_to_final_target": round(rr_value, 4),
            "min_rr_required": min_rr,
        },
        "rr": rr_value,
        "filters": {
            "news_filter": "failed" if "news_restricted" in rejection_reasons else "passed",
            "spread_filter": "failed" if "spread_too_high" in rejection_reasons else "passed",
            "accepted_breakout_filter": "passed",
            "target_distance_filter": "failed" if "target_too_close" in rejection_reasons else "passed",
            "htf_blocker_filter": "failed" if "htf_poi_blocks_target" in rejection_reasons else "passed",
            "rr_filter": "failed" if "rr_below_minimum" in rejection_reasons else "passed",
        },
        "rejection_reasons": rejection_reasons,
        "warnings": [
            "Do not automatically fade PDH or PDL; require raid, reclaim/rejection, MSS, displacement, and retracement."
        ],
    }
    setup["score"] = score_pdh_pdl_raid_setup(setup, context, config)
    setup["trade_allowed"] = bool(setup["score"]["trade_allowed"])
    if setup["trade_allowed"]:
        return setup
    return _decision(
        PDHPDLStatus.REJECTED,
        symbol,
        rejection_reasons or setup["score"]["hard_filter_failures"] or ["score_or_filter_failed"],
        {
            "previous_day_levels": levels,
            "raid": raid,
            "reclaim_or_rejection": reclaim,
            "mss": mss,
            "entry_poi": entry_poi,
            "score": setup["score"],
            "rr": round(rr_value, 4),
        },
    )
