"""Asian range liquidity logic for ICT/SMC XAUUSD analysis.

The Asian session high and low are treated as session liquidity. This module
maps the range, then classifies post-session interaction as sweep/reclaim,
breakout/acceptance, or unclear context. It does not authorize entries alone.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone as dt_timezone
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence

from src.core.domain.market_data import CandleNode


class AsianLiquidityType(str, Enum):
    ASIAN_HIGH = "asian_high"
    ASIAN_LOW = "asian_low"


class AsianLiquidityDirection(str, Enum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


class AsianSweepType(str, Enum):
    NONE = "none"
    ASIAN_HIGH_SWEEP = "asian_high_sweep"
    ASIAN_HIGH_BREAKOUT = "asian_high_breakout_continuation"
    ASIAN_LOW_SWEEP = "asian_low_sweep"
    ASIAN_LOW_BREAKDOWN = "asian_low_breakdown_continuation"
    UNCLEAR = "unclear"


class AsianReclaimStatus(str, Enum):
    NONE = "none"
    REJECTED_BACK_INSIDE = "rejected_back_inside_range"
    RECLAIMED_BACK_INSIDE = "reclaimed_back_inside_range"
    ACCEPTED_ABOVE = "accepted_above_range"
    ACCEPTED_BELOW = "accepted_below_range"
    UNCLEAR = "unclear"


class AsianReactionBias(str, Enum):
    NONE = "none"
    BEARISH_POSSIBLE = "bearish_possible"
    BULLISH_POSSIBLE = "bullish_possible"
    BULLISH_CONTINUATION = "bullish_continuation_possible"
    BEARISH_CONTINUATION = "bearish_continuation_possible"


class AsianQualityGrade(str, Enum):
    INVALID = "invalid"
    WEAK = "weak"
    USABLE_CONTEXT = "usable_context"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True, slots=True)
class AsianLiquidityObject:
    liquidity_id: str
    liquidity_type: AsianLiquidityType
    direction: AsianLiquidityDirection
    price: float
    zone_low: float
    zone_high: float
    liquidity_role: str
    target_use: str
    swept_status: str = "unswept"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["liquidity_type"] = self.liquidity_type.value
        payload["direction"] = self.direction.value
        payload["price"] = round(self.price, 5)
        payload["zone_low"] = round(self.zone_low, 5)
        payload["zone_high"] = round(self.zone_high, 5)
        return payload


@dataclass(frozen=True, slots=True)
class _Candle:
    index: int
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str
    symbol: str
    is_closed: bool = True

    @property
    def range(self) -> float:
        return max(0.0, self.high - self.low)

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
    def close_position(self) -> float:
        if self.range <= 0:
            return 0.5
        return (self.close - self.low) / self.range


@dataclass(frozen=True, slots=True)
class _SweepEvent:
    sweep_type: AsianSweepType
    swept_side: AsianLiquidityType
    swept_liquidity: AsianLiquidityDirection
    level: float
    candle: _Candle
    raid_extreme: float
    reclaim_status: AsianReclaimStatus
    sweep_confirmed: bool
    breakout_confirmed: bool
    expected_bias: AsianReactionBias
    condition: str


def calculate_session_range(
    df: Sequence[CandleNode | Mapping[str, Any]] | Any,
    session_start: str,
    session_end: str,
    timezone: str,
    *,
    symbol: str = "unknown",
    timeframe: str | None = None,
    tolerance: float | None = None,
    tolerance_percent: float = 0.02,
    atr_period: int = 14,
) -> dict[str, Any]:
    """Calculate Asian high, low, midpoint, and range from closed session candles."""
    candles = [candle for candle in _normalize_candles(df, timeframe, symbol) if candle.is_closed]
    if not candles:
        return _empty_range("insufficient_closed_candles", session_start, session_end, timezone, symbol)

    start_time = _parse_clock(session_start)
    end_time = _parse_clock(session_end)
    session_candles = [candle for candle in candles if _inside_session(candle.timestamp.time(), start_time, end_time)]
    if not session_candles:
        return _empty_range("no_closed_candles_inside_session_window", session_start, session_end, timezone, symbol)

    selected_date = session_candles[-1].timestamp.date()
    day_session = [candle for candle in session_candles if candle.timestamp.date() == selected_date]
    if len(day_session) < 2 and _crosses_midnight(start_time, end_time):
        day_session = session_candles
    asian_high_candle = max(day_session, key=lambda candle: candle.high)
    asian_low_candle = min(day_session, key=lambda candle: candle.low)
    asian_high = asian_high_candle.high
    asian_low = asian_low_candle.low
    asian_range = max(0.0, asian_high - asian_low)
    atr = _calculate_atr(candles, atr_period)[-1]
    zone_tolerance = tolerance if tolerance is not None else asian_range * tolerance_percent
    warnings = _range_warnings(timezone, asian_range, atr, len(day_session))
    score = _range_quality_score(timezone, asian_range, atr, warnings)

    high_object = AsianLiquidityObject(
        liquidity_id=f"ASIA_HIGH_{selected_date.isoformat().replace('-', '_')}",
        liquidity_type=AsianLiquidityType.ASIAN_HIGH,
        direction=AsianLiquidityDirection.BUY_SIDE,
        price=asian_high,
        zone_low=asian_high - zone_tolerance,
        zone_high=asian_high + zone_tolerance,
        liquidity_role="buy_side_liquidity",
        target_use="long_target_or_bearish_sweep_area",
    )
    low_object = AsianLiquidityObject(
        liquidity_id=f"ASIA_LOW_{selected_date.isoformat().replace('-', '_')}",
        liquidity_type=AsianLiquidityType.ASIAN_LOW,
        direction=AsianLiquidityDirection.SELL_SIDE,
        price=asian_low,
        zone_low=asian_low - zone_tolerance,
        zone_high=asian_low + zone_tolerance,
        liquidity_role="sell_side_liquidity",
        target_use="short_target_or_bullish_sweep_area",
    )
    return {
        "concept_name": "Asian Range Liquidity",
        "symbol": day_session[0].symbol or symbol,
        "session_name": "asian_session",
        "session_definition": {
            "session_start": session_start,
            "session_end": session_end,
            "timezone": timezone,
        },
        "session_start": session_start,
        "session_end": session_end,
        "timezone": timezone,
        "session_date": selected_date.isoformat(),
        "asian_high": round(asian_high, 5),
        "asian_low": round(asian_low, 5),
        "asian_midpoint": round((asian_high + asian_low) / 2.0, 5),
        "asian_range_size": round(asian_range, 5),
        "session_open": round(day_session[0].open, 5),
        "session_close": round(day_session[-1].close, 5),
        "session_volume": round(sum(candle.volume for candle in day_session), 5),
        "session_candle_count": len(day_session),
        "candles_used": len(day_session),
        "high_candle_index": asian_high_candle.index,
        "low_candle_index": asian_low_candle.index,
        "asian_range": {
            "asian_high": round(asian_high, 5),
            "asian_low": round(asian_low, 5),
            "asian_midpoint": round((asian_high + asian_low) / 2.0, 5),
            "asian_range_size": round(asian_range, 5),
            "session_open": round(day_session[0].open, 5),
            "session_close": round(day_session[-1].close, 5),
            "high_candle_index": asian_high_candle.index,
            "low_candle_index": asian_low_candle.index,
        },
        "liquidity_objects": [high_object.as_dict(), low_object.as_dict()],
        "quality_score": score,
        "quality_grade": _quality_grade(score).value,
        "warnings": warnings,
        "entry_allowed_from_asian_range_alone": False,
    }


def detect_asian_range_sweep(
    df: Sequence[CandleNode | Mapping[str, Any]] | Any,
    asian_high: float | Mapping[str, Any],
    asian_low: float | Mapping[str, Any],
    *,
    asian_midpoint: float | None = None,
    asian_session_end_index: int | None = None,
    sweep_buffer: float | None = None,
    close_buffer: float | None = None,
    atr_period: int = 14,
    buffer_atr_multiplier: float = 0.05,
    mss_events: Sequence[Mapping[str, Any] | Any] | None = None,
    fvg_events: Sequence[Mapping[str, Any] | Any] | None = None,
    symbol: str = "unknown",
    timeframe: str | None = None,
    session_label: str | None = None,
) -> dict[str, Any]:
    """Detect post-Asian-session sweeps, acceptance, entry zones, and targets."""
    candles = [candle for candle in _normalize_candles(df, timeframe, symbol) if candle.is_closed]
    high_price = _extract_level_price(asian_high, "asian_high")
    low_price = _extract_level_price(asian_low, "asian_low")
    if not candles or high_price is None or low_price is None:
        return _empty_sweep("missing_closed_candles_or_asian_levels", symbol, timeframe)

    midpoint = asian_midpoint if asian_midpoint is not None else (high_price + low_price) / 2.0
    post_session = [
        candle
        for candle in candles
        if asian_session_end_index is None or candle.index > asian_session_end_index
    ]
    if not post_session:
        return _empty_sweep("no_post_asian_session_candles_to_scan", candles[0].symbol or symbol, timeframe)

    atr = _calculate_atr(candles, atr_period)[-1]
    sweep = sweep_buffer if sweep_buffer is not None else max(atr * buffer_atr_multiplier, 0.00001)
    close = close_buffer if close_buffer is not None else sweep
    events = _detect_sweep_events(post_session, high_price, low_price, sweep, close)
    if not events:
        return _empty_sweep(
            "no_asian_range_sweep_or_breakout",
            candles[0].symbol or symbol,
            timeframe or candles[-1].timeframe,
        )

    selected = events[0]
    expected_direction = _expected_direction(selected.sweep_type)
    mss = _find_mss_confirmation(post_session, selected, expected_direction, mss_events)
    fvg = _find_fvg_confirmation(post_session, selected, expected_direction, fvg_events)
    displacement_strength = _displacement_strength(post_session, selected, expected_direction, atr)
    entry_zone = _entry_zone(selected, fvg)
    targets = _targets(selected.sweep_type, high_price, low_price, midpoint)
    reasons, warnings = _sweep_reasons_and_warnings(selected, mss, fvg, displacement_strength, session_label)
    score = _sweep_quality_score(selected, mss, fvg, displacement_strength, session_label, reasons, warnings)

    return {
        "concept_name": "Asian Range Sweep",
        "symbol": selected.candle.symbol or symbol,
        "timeframe": timeframe or selected.candle.timeframe,
        "sweep_id": _sweep_id(selected),
        "swept_side": selected.swept_side.value,
        "swept_liquidity": selected.swept_liquidity.value,
        "asian_high": round(high_price, 5),
        "asian_low": round(low_price, 5),
        "asian_midpoint": round(midpoint, 5),
        "raid_candle_index": selected.candle.index,
        "raid_candle_timestamp": selected.candle.timestamp.isoformat(),
        "raid_extreme": round(selected.raid_extreme, 5),
        "reclaim_status": selected.reclaim_status.value,
        "sweep_type": selected.sweep_type.value,
        "sweep_confirmed": selected.sweep_confirmed,
        "breakout_confirmed": selected.breakout_confirmed,
        "expected_bias": selected.expected_bias.value,
        "sweep_result": {
            "sweep_confirmed": selected.sweep_confirmed,
            "breakout_confirmed": selected.breakout_confirmed,
            "condition": selected.condition,
            "expected_bias": selected.expected_bias.value,
        },
        "confirmation": {
            "mss_confirmed": mss["confirmed"],
            "mss_direction": mss["direction"],
            "mss_confirmation_index": mss["index"],
            "displacement_after_sweep": displacement_strength in {"moderate", "strong"},
            "displacement_strength": displacement_strength,
            "fvg_after_sweep": fvg["confirmed"],
            "fvg_type": fvg["type"],
        },
        "mss_confirmed": mss["confirmed"],
        "mss_direction": mss["direction"],
        "entry_zone": entry_zone,
        "target_side": targets["target_side"],
        "targets": targets,
        "risk_logic": {
            "entry_allowed_from_sweep_alone": False,
            "entry_allowed_after_fvg_retest_reaction": bool(fvg["confirmed"] and mss["confirmed"]),
            "stop_loss_reference": _stop_loss_reference(selected.sweep_type),
        },
        "double_sweep_day": len({event.swept_side for event in events}) > 1,
        "quality_score": score,
        "quality_grade": _quality_grade(score).value,
        "reasons": reasons,
        "warnings": warnings,
    }


def _detect_sweep_events(
    candles: Sequence[_Candle],
    asian_high: float,
    asian_low: float,
    sweep_buffer: float,
    close_buffer: float,
) -> list[_SweepEvent]:
    events: list[_SweepEvent] = []
    for candle in candles:
        if candle.high > asian_high + sweep_buffer:
            if candle.close < asian_high:
                events.append(
                    _SweepEvent(
                        AsianSweepType.ASIAN_HIGH_SWEEP,
                        AsianLiquidityType.ASIAN_HIGH,
                        AsianLiquidityDirection.BUY_SIDE,
                        asian_high,
                        candle,
                        candle.high,
                        AsianReclaimStatus.REJECTED_BACK_INSIDE,
                        True,
                        False,
                        AsianReactionBias.BEARISH_POSSIBLE,
                        "candle_high_above_asian_high_and_close_back_below_asian_high",
                    )
                )
            elif candle.close > asian_high + close_buffer and candle.bullish and candle.close_position >= 0.65:
                events.append(
                    _SweepEvent(
                        AsianSweepType.ASIAN_HIGH_BREAKOUT,
                        AsianLiquidityType.ASIAN_HIGH,
                        AsianLiquidityDirection.BUY_SIDE,
                        asian_high,
                        candle,
                        candle.high,
                        AsianReclaimStatus.ACCEPTED_ABOVE,
                        False,
                        True,
                        AsianReactionBias.BULLISH_CONTINUATION,
                        "candle_close_above_asian_high_with_bullish_acceptance",
                    )
                )
            else:
                events.append(_unclear_event(AsianLiquidityType.ASIAN_HIGH, candle, asian_high, candle.high))
        if candle.low < asian_low - sweep_buffer:
            if candle.close > asian_low:
                events.append(
                    _SweepEvent(
                        AsianSweepType.ASIAN_LOW_SWEEP,
                        AsianLiquidityType.ASIAN_LOW,
                        AsianLiquidityDirection.SELL_SIDE,
                        asian_low,
                        candle,
                        candle.low,
                        AsianReclaimStatus.RECLAIMED_BACK_INSIDE,
                        True,
                        False,
                        AsianReactionBias.BULLISH_POSSIBLE,
                        "candle_low_below_asian_low_and_close_back_above_asian_low",
                    )
                )
            elif candle.close < asian_low - close_buffer and candle.bearish and candle.close_position <= 0.35:
                events.append(
                    _SweepEvent(
                        AsianSweepType.ASIAN_LOW_BREAKDOWN,
                        AsianLiquidityType.ASIAN_LOW,
                        AsianLiquidityDirection.SELL_SIDE,
                        asian_low,
                        candle,
                        candle.low,
                        AsianReclaimStatus.ACCEPTED_BELOW,
                        False,
                        True,
                        AsianReactionBias.BEARISH_CONTINUATION,
                        "candle_close_below_asian_low_with_bearish_acceptance",
                    )
                )
            else:
                events.append(_unclear_event(AsianLiquidityType.ASIAN_LOW, candle, asian_low, candle.low))
    return sorted(events, key=lambda event: (event.candle.timestamp, event.candle.index))


def _unclear_event(side: AsianLiquidityType, candle: _Candle, level: float, extreme: float) -> _SweepEvent:
    liquidity = (
        AsianLiquidityDirection.BUY_SIDE
        if side == AsianLiquidityType.ASIAN_HIGH
        else AsianLiquidityDirection.SELL_SIDE
    )
    return _SweepEvent(
        AsianSweepType.UNCLEAR,
        side,
        liquidity,
        level,
        candle,
        extreme,
        AsianReclaimStatus.UNCLEAR,
        False,
        False,
        AsianReactionBias.NONE,
        "wick_beyond_asian_range_without_clean_reclaim_or_acceptance",
    )


def _expected_direction(sweep_type: AsianSweepType) -> str | None:
    if sweep_type in {AsianSweepType.ASIAN_HIGH_SWEEP, AsianSweepType.ASIAN_LOW_BREAKDOWN}:
        return "bearish"
    if sweep_type in {AsianSweepType.ASIAN_LOW_SWEEP, AsianSweepType.ASIAN_HIGH_BREAKOUT}:
        return "bullish"
    return None


def _find_mss_confirmation(
    candles: Sequence[_Candle],
    event: _SweepEvent,
    direction: str | None,
    mss_events: Sequence[Mapping[str, Any] | Any] | None,
) -> dict[str, Any]:
    if direction is None:
        return {"confirmed": False, "direction": None, "index": None}
    if mss_events is not None:
        for item in mss_events:
            item_direction = str(_get(item, "direction", _get(item, "mss_direction", ""))).lower()
            item_index = int(_get(item, "index", _get(item, "confirmation_index", -1)))
            if item_direction == direction and item_index > event.candle.index:
                return {"confirmed": True, "direction": direction, "index": item_index}
        return {"confirmed": False, "direction": direction, "index": None}
    later = [candle for candle in candles if candle.index > event.candle.index]
    if len(later) < 3:
        return {"confirmed": False, "direction": direction, "index": None}
    if direction == "bearish":
        post_raid_low = min(candle.low for candle in later[:2])
        for candle in later[2:]:
            if candle.close < post_raid_low:
                return {"confirmed": True, "direction": direction, "index": candle.index}
    else:
        post_raid_high = max(candle.high for candle in later[:2])
        for candle in later[2:]:
            if candle.close > post_raid_high:
                return {"confirmed": True, "direction": direction, "index": candle.index}
    return {"confirmed": False, "direction": direction, "index": None}


def _find_fvg_confirmation(
    candles: Sequence[_Candle],
    event: _SweepEvent,
    direction: str | None,
    fvg_events: Sequence[Mapping[str, Any] | Any] | None,
) -> dict[str, Any]:
    if direction is None:
        return _empty_fvg()
    fvg_type = "bearish_fvg" if direction == "bearish" else "bullish_fvg"
    if fvg_events is not None:
        for item in fvg_events:
            item_type = str(_get(item, "type", _get(item, "fvg_type", ""))).lower()
            item_direction = str(_get(item, "direction", "")).lower()
            item_index = int(_get(item, "index", _get(item, "creation_index", -1)))
            if item_index > event.candle.index and (item_type == fvg_type or item_direction == direction):
                return {
                    "confirmed": True,
                    "type": fvg_type,
                    "index": item_index,
                    "zone_low": _optional_float(_get(item, "zone_low")),
                    "zone_high": _optional_float(_get(item, "zone_high")),
                }
        return _empty_fvg(fvg_type)
    later = [candle for candle in candles if candle.index > event.candle.index]
    for idx in range(len(later) - 2):
        first, _, third = later[idx], later[idx + 1], later[idx + 2]
        if direction == "bearish" and first.low > third.high:
            return {
                "confirmed": True,
                "type": fvg_type,
                "index": third.index,
                "zone_low": third.high,
                "zone_high": first.low,
            }
        if direction == "bullish" and first.high < third.low:
            return {
                "confirmed": True,
                "type": fvg_type,
                "index": third.index,
                "zone_low": first.high,
                "zone_high": third.low,
            }
    return _empty_fvg(fvg_type)


def _empty_fvg(fvg_type: str | None = None) -> dict[str, Any]:
    return {"confirmed": False, "type": fvg_type, "index": None, "zone_low": None, "zone_high": None}


def _displacement_strength(candles: Sequence[_Candle], event: _SweepEvent, direction: str | None, atr: float) -> str:
    if direction is None or atr <= 0:
        return "none"
    strongest = 0.0
    for candle in [item for item in candles if item.index > event.candle.index][:5]:
        directional = (direction == "bullish" and candle.bullish) or (direction == "bearish" and candle.bearish)
        if directional:
            strongest = max(strongest, candle.body / atr)
    if strongest >= 1.2:
        return "strong"
    if strongest >= 0.65:
        return "moderate"
    return "none"


def _entry_zone(event: _SweepEvent, fvg: Mapping[str, Any]) -> dict[str, Any] | None:
    if not fvg["confirmed"]:
        if event.sweep_type == AsianSweepType.ASIAN_HIGH_BREAKOUT:
            return {"entry_zone_type": "bullish_fvg_or_asian_high_retest", "zone_reference": "asian_high_as_support"}
        if event.sweep_type == AsianSweepType.ASIAN_LOW_BREAKDOWN:
            return {"entry_zone_type": "bearish_fvg_or_asian_low_retest", "zone_reference": "asian_low_as_resistance"}
        return None
    zone_low = _optional_float(fvg["zone_low"])
    zone_high = _optional_float(fvg["zone_high"])
    entry_type = fvg["type"] or "fvg"
    invalidation = event.raid_extreme
    return {
        "entry_zone_type": entry_type,
        "zone_low": _round_optional(zone_low),
        "zone_high": _round_optional(zone_high),
        "zone_mid": _round_optional(None if zone_low is None or zone_high is None else (zone_low + zone_high) / 2.0),
        "source_event": f"{entry_type}_after_{event.swept_side.value}_interaction",
        "invalidation_level": round(invalidation, 5),
    }


def _targets(sweep_type: AsianSweepType, asian_high: float, asian_low: float, midpoint: float) -> dict[str, Any]:
    if sweep_type == AsianSweepType.ASIAN_HIGH_SWEEP:
        return {
            "target_side": "sell_side",
            "first_target": "asian_midpoint",
            "first_target_price": round(midpoint, 5),
            "second_target": "asian_low",
            "second_target_price": round(asian_low, 5),
            "final_target": "PDL_or_external_sell_side_liquidity",
        }
    if sweep_type == AsianSweepType.ASIAN_LOW_SWEEP:
        return {
            "target_side": "buy_side",
            "first_target": "asian_midpoint",
            "first_target_price": round(midpoint, 5),
            "second_target": "asian_high",
            "second_target_price": round(asian_high, 5),
            "final_target": "PDH_or_external_buy_side_liquidity",
        }
    if sweep_type == AsianSweepType.ASIAN_HIGH_BREAKOUT:
        return {"target_side": "buy_side", "final_target": "PDH_or_external_buy_side_liquidity"}
    if sweep_type == AsianSweepType.ASIAN_LOW_BREAKDOWN:
        return {"target_side": "sell_side", "final_target": "PDL_or_external_sell_side_liquidity"}
    return {"target_side": "unknown", "final_target": None}


def _stop_loss_reference(sweep_type: AsianSweepType) -> str | None:
    if sweep_type == AsianSweepType.ASIAN_HIGH_SWEEP:
        return "above_sweep_high_or_above_bearish_entry_zone"
    if sweep_type == AsianSweepType.ASIAN_LOW_SWEEP:
        return "below_sweep_low_or_below_bullish_entry_zone"
    if sweep_type == AsianSweepType.ASIAN_HIGH_BREAKOUT:
        return "below_asian_high_retest_or_bullish_entry_zone"
    if sweep_type == AsianSweepType.ASIAN_LOW_BREAKDOWN:
        return "above_asian_low_retest_or_bearish_entry_zone"
    return None


def _sweep_quality_score(
    event: _SweepEvent,
    mss: Mapping[str, Any],
    fvg: Mapping[str, Any],
    displacement_strength: str,
    session_label: str | None,
    reasons: list[str],
    warnings: list[str],
) -> float:
    if event.sweep_type == AsianSweepType.UNCLEAR:
        return 3.0
    score = 2.0
    score += 1.5 if event.sweep_confirmed else 1.0
    score += 1.5 if event.reclaim_status != AsianReclaimStatus.UNCLEAR else 0.25
    if mss["confirmed"]:
        score += 2.0
        reasons.append("market_structure_shift_confirmed_after_asian_range_interaction")
    else:
        warnings.append("no_mss_after_asian_range_interaction")
    if displacement_strength == "strong":
        score += 1.0
    elif displacement_strength == "moderate":
        score += 0.5
    else:
        warnings.append("no_clear_displacement_after_asian_range_interaction")
    if fvg["confirmed"]:
        score += 1.0
        reasons.append("fvg_detected_after_asian_range_interaction")
    else:
        warnings.append("no_fvg_after_asian_range_interaction")
    if session_label and session_label.lower() in {"london", "london_killzone", "newyork", "newyork_killzone", "ny"}:
        score += 1.0
        reasons.append("interaction_happened_during_london_or_newyork_window")
    elif session_label:
        score += 0.5
    if not mss["confirmed"]:
        score = min(score, 5.0)
    if not fvg["confirmed"] or displacement_strength == "none":
        score = min(score, 6.0)
    if event.breakout_confirmed:
        score = min(score, 8.0)
    return round(max(0.0, min(10.0, score)), 2)


def _sweep_reasons_and_warnings(
    event: _SweepEvent,
    mss: Mapping[str, Any],
    fvg: Mapping[str, Any],
    displacement_strength: str,
    session_label: str | None,
) -> tuple[list[str], list[str]]:
    reasons = [event.condition]
    warnings = ["Asian range liquidity is not an entry signal by itself"]
    if event.sweep_type == AsianSweepType.ASIAN_HIGH_SWEEP:
        reasons += ["asian_high_acted_as_buy_side_liquidity", "price_swept_above_asian_high_and_rejected_inside"]
    elif event.sweep_type == AsianSweepType.ASIAN_LOW_SWEEP:
        reasons += ["asian_low_acted_as_sell_side_liquidity", "price_swept_below_asian_low_and_reclaimed_inside"]
    elif event.sweep_type == AsianSweepType.ASIAN_HIGH_BREAKOUT:
        reasons.append("price_accepted_above_asian_high_instead_of_rejecting")
        warnings.append("not_an_asian_high_sweep_reversal")
    elif event.sweep_type == AsianSweepType.ASIAN_LOW_BREAKDOWN:
        reasons.append("price_accepted_below_asian_low_instead_of_reclaiming")
        warnings.append("not_an_asian_low_sweep_reversal")
    else:
        warnings.append("unclear_asian_range_interaction")
    if session_label is None:
        warnings.append("session_timing_not_supplied_for_quality_scoring")
    if not mss["confirmed"]:
        warnings.append("wait_for_mss_before_execution_model")
    if not fvg["confirmed"]:
        warnings.append("wait_for_fvg_or_ob_retest_before_execution_model")
    if displacement_strength == "none":
        warnings.append("displacement_not_confirmed")
    return reasons, warnings


def _range_quality_score(session_timezone: str, asian_range: float, atr: float, warnings: list[str]) -> float:
    score = 1.0
    if session_timezone.lower() in {"unknown", "broker"}:
        score += 0.5
    else:
        score += 1.0
    if atr <= 0:
        score += 0.5
    else:
        ratio = asian_range / atr
        if 0.75 <= ratio <= 4.0:
            score += 1.0
        elif 0.35 <= ratio <= 6.0:
            score += 0.5
        else:
            warnings.append("asian_range_size_outside_preferred_atr_band")
    return round(max(0.0, min(10.0, score + 5.0)), 2)


def _range_warnings(session_timezone: str, asian_range: float, atr: float, candle_count: int) -> list[str]:
    warnings = [
        "Asian session range depends on timezone and broker session definition",
        "Use the same session settings in backtest and live trading",
        "Asian range liquidity is not an entry signal without MSS/FVG/retest confirmation",
    ]
    if session_timezone.lower() in {"unknown", "broker"}:
        warnings.append("session_timezone_uncertain")
    if candle_count < 3:
        warnings.append("insufficient_session_candle_count")
    if atr > 0 and asian_range / atr < 0.35:
        warnings.append("asian_range_too_compressed_or_noisy")
    if atr > 0 and asian_range / atr > 6.0:
        warnings.append("asian_range_too_expanded_for_clean_liquidity_box")
    return warnings


def _quality_grade(score: float) -> AsianQualityGrade:
    if score < 2.5:
        return AsianQualityGrade.INVALID
    if score < 5.0:
        return AsianQualityGrade.WEAK
    if score < 7.0:
        return AsianQualityGrade.USABLE_CONTEXT
    if score < 9.0:
        return AsianQualityGrade.STRONG
    return AsianQualityGrade.HIGH_QUALITY


def _sweep_id(event: _SweepEvent) -> str:
    prefix = {
        AsianSweepType.ASIAN_HIGH_SWEEP: "ASIA_HIGH_SWEEP",
        AsianSweepType.ASIAN_HIGH_BREAKOUT: "ASIA_HIGH_BREAKOUT",
        AsianSweepType.ASIAN_LOW_SWEEP: "ASIA_LOW_SWEEP",
        AsianSweepType.ASIAN_LOW_BREAKDOWN: "ASIA_LOW_BREAKDOWN",
        AsianSweepType.UNCLEAR: "ASIA_RANGE_UNCLEAR",
        AsianSweepType.NONE: "ASIA_RANGE_NONE",
    }[event.sweep_type]
    stamp = event.candle.timestamp.strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{event.candle.index}"


def _normalize_candles(
    source: Sequence[CandleNode | Mapping[str, Any]] | Any,
    timeframe: str | None,
    symbol: str,
) -> list[_Candle]:
    if hasattr(source, "to_dict"):
        source = source.to_dict("records")
    candles: list[_Candle] = []
    for position, item in enumerate(source or []):
        if isinstance(item, CandleNode):
            raw_timestamp = item.timestamp
            open_price = item.open
            high_price = item.high
            low_price = item.low
            close_price = item.close
            volume = item.volume
            candle_timeframe = item.timeframe
            candle_symbol = item.symbol
            is_closed = getattr(item, "is_closed", True)
            index = getattr(item, "index", position)
        else:
            raw_timestamp = _get(item, "timestamp", datetime.now(dt_timezone.utc))
            open_price = _get(item, "open", _get(item, "open_p", 0.0))
            high_price = _get(item, "high", _get(item, "high_p", 0.0))
            low_price = _get(item, "low", _get(item, "low_p", 0.0))
            close_price = _get(item, "close", _get(item, "close_p", 0.0))
            volume = _get(item, "volume", 0.0)
            candle_timeframe = _get(item, "timeframe", timeframe or "unknown")
            candle_symbol = _get(item, "symbol", symbol)
            is_closed = bool(_get(item, "is_closed", True))
            index = int(_get(item, "index", position))
        candles.append(
            _Candle(
                index=index,
                timestamp=_timestamp(raw_timestamp),
                open=float(open_price),
                high=float(high_price),
                low=float(low_price),
                close=float(close_price),
                volume=float(volume or 0.0),
                timeframe=str(candle_timeframe or timeframe or "unknown"),
                symbol=str(candle_symbol or symbol),
                is_closed=is_closed,
            )
        )
    return sorted(candles, key=lambda candle: (candle.timestamp, candle.index))


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=dt_timezone.utc)
    if hasattr(value, "to_pydatetime"):
        result = value.to_pydatetime()
        return result if result.tzinfo is not None else result.replace(tzinfo=dt_timezone.utc)
    if isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else result.replace(tzinfo=dt_timezone.utc)
    return datetime.now(dt_timezone.utc)


def _get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _parse_clock(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _inside_session(candidate: time, start: time, end: time) -> bool:
    if _crosses_midnight(start, end):
        return candidate >= start or candidate <= end
    return start <= candidate <= end


def _crosses_midnight(start: time, end: time) -> bool:
    return start > end


def _calculate_atr(candles: Sequence[_Candle], period: int) -> list[float]:
    if not candles:
        return [0.0]
    true_ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        if previous_close is None:
            true_range = candle.range
        else:
            true_range = max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        true_ranges.append(max(0.0, true_range))
        previous_close = candle.close
    values: list[float] = []
    for idx in range(len(true_ranges)):
        start = max(0, idx - period + 1)
        values.append(mean(true_ranges[start : idx + 1]))
    return values


def _extract_level_price(value: float | Mapping[str, Any], key: str) -> float | None:
    if isinstance(value, Mapping):
        if key in value:
            return float(value[key])
        if "price" in value:
            return float(value["price"])
        if "asian_range" in value and key in value["asian_range"]:
            return float(value["asian_range"][key])
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _round_optional(value: Any) -> float | None:
    parsed = _optional_float(value)
    return None if parsed is None else round(parsed, 5)


def _empty_range(
    reason: str,
    session_start: str,
    session_end: str,
    session_timezone: str,
    symbol: str,
) -> dict[str, Any]:
    return {
        "concept_name": "Asian Range Liquidity",
        "symbol": symbol,
        "session_name": "asian_session",
        "session_definition": {
            "session_start": session_start,
            "session_end": session_end,
            "timezone": session_timezone,
        },
        "asian_range": {},
        "liquidity_objects": [],
        "quality_score": 0.0,
        "quality_grade": AsianQualityGrade.INVALID.value,
        "warnings": [reason],
        "entry_allowed_from_asian_range_alone": False,
    }


def _empty_sweep(reason: str, symbol: str, timeframe: str | None) -> dict[str, Any]:
    return {
        "concept_name": "Asian Range Sweep",
        "symbol": symbol,
        "timeframe": timeframe or "unknown",
        "swept_side": None,
        "swept_liquidity": None,
        "reclaim_status": AsianReclaimStatus.NONE.value,
        "sweep_type": AsianSweepType.NONE.value,
        "sweep_confirmed": False,
        "breakout_confirmed": False,
        "expected_bias": AsianReactionBias.NONE.value,
        "mss_confirmed": False,
        "mss_direction": None,
        "entry_zone": None,
        "target_side": "unknown",
        "targets": {"target_side": "unknown", "final_target": None},
        "risk_logic": {
            "entry_allowed_from_sweep_alone": False,
            "entry_allowed_after_fvg_retest_reaction": False,
            "stop_loss_reference": None,
        },
        "quality_score": 0.0,
        "quality_grade": AsianQualityGrade.INVALID.value,
        "reasons": [],
        "warnings": [reason],
    }
