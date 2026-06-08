"""ICT/SMC London Open liquidity raid model.

The London Open Raid model uses a completed Asian range as the liquidity map,
then classifies London-window movement as a reversal raid, breakout
continuation, or weak/no setup. It is deterministic analytics only and does not
allow a London sweep to become an entry signal without MSS, displacement,
entry-zone, target, and risk context.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as dt_timezone, tzinfo
from enum import Enum
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class LondonRaidType(str, Enum):
    NONE = "no_valid_london_open_raid"
    ASIAN_HIGH_SWEEP_REVERSAL = "asian_high_sweep_reversal"
    ASIAN_LOW_SWEEP_REVERSAL = "asian_low_sweep_reversal"
    ASIAN_HIGH_BREAKOUT_CONTINUATION = "asian_high_breakout_continuation"
    ASIAN_LOW_BREAKDOWN_CONTINUATION = "asian_low_breakdown_continuation"
    ASIAN_HIGH_SWEEP_CANDIDATE = "asian_high_sweep_candidate"
    ASIAN_LOW_SWEEP_CANDIDATE = "asian_low_sweep_candidate"
    UNCLEAR_ASIAN_HIGH_RAID = "unclear_asian_high_raid"
    UNCLEAR_ASIAN_LOW_RAID = "unclear_asian_low_raid"
    MESSY_ASIAN_RANGE = "messy_asian_range"
    OUTSIDE_LONDON_WINDOW = "outside_london_window"


class LondonRaidDirection(str, Enum):
    NONE = "none"
    BULLISH = "bullish"
    BEARISH = "bearish"
    BULLISH_CONTINUATION = "bullish_continuation"
    BEARISH_CONTINUATION = "bearish_continuation"


class LondonRaidSweptSide(str, Enum):
    NONE = "none"
    ASIAN_HIGH = "asian_high"
    ASIAN_LOW = "asian_low"


class LondonRaidReclaimStatus(str, Enum):
    NONE = "none"
    REJECTED_BACK_BELOW_ASIAN_HIGH = "rejected_back_below_asian_high"
    RECLAIMED_BACK_ABOVE_ASIAN_LOW = "reclaimed_back_above_asian_low"
    ACCEPTED_ABOVE_ASIAN_HIGH = "accepted_above_asian_high"
    ACCEPTED_BELOW_ASIAN_LOW = "accepted_below_asian_low"
    UNCLEAR = "unclear"


class LondonRaidQualityGrade(str, Enum):
    INVALID = "invalid"
    WATCHLIST = "watchlist"
    VALID = "valid"
    STRONG = "strong"


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
    def bullish_close_position(self) -> float:
        if self.range <= 0:
            return 0.5
        return (self.close - self.low) / self.range

    @property
    def bearish_close_position(self) -> float:
        if self.range <= 0:
            return 0.5
        return (self.high - self.close) / self.range


@dataclass(frozen=True, slots=True)
class _AsianRange:
    high: float
    low: float
    midpoint: float
    range_size: float
    quality_score: float
    session_end: datetime | None


@dataclass(frozen=True, slots=True)
class _LondonWindow:
    name: str
    start_time: time
    end_time: time
    timezone: tzinfo
    timezone_name: str
    allowed_days: set[str]
    strict_mode: bool
    post_window_buffer_minutes: int


_FIXED_ZONE_FALLBACKS: dict[str, int] = {
    "America/New_York": -4,
    "Europe/London": 1,
}


def detect_london_open_raid(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    asian_range: Mapping[str, Any],
    london_window: Mapping[str, Any],
    htf_bias: str,
    *,
    minimum_asian_quality: float = 6.0,
    minimum_valid_score: float = 7.0,
    sweep_buffer: float | None = None,
    close_buffer: float | None = None,
    break_buffer: float | None = None,
    stop_buffer: float | None = None,
    atr_period: int = 14,
    min_displacement_body_ratio: float = 0.55,
    min_displacement_range_ratio: float = 1.0,
    min_rr: float = 0.8,
) -> dict[str, Any]:
    """Detect the best London Open Raid candidate against Asian liquidity."""
    warnings: list[str] = []
    candles = [c for c in _normalize_candles(df) if c.is_closed]
    asian = _parse_asian_range(asian_range)
    window = _parse_london_window(london_window, warnings)
    if not candles or asian is None or window is None:
        return _empty_result(
            LondonRaidType.NONE,
            "missing_closed_candles_asian_range_or_london_window",
            asian,
            london_window,
            htf_bias,
            warnings,
        )

    if asian.quality_score < minimum_asian_quality:
        return _empty_result(
            LondonRaidType.MESSY_ASIAN_RANGE,
            "messy_asian_range_do_not_force_setup",
            asian,
            london_window,
            htf_bias,
            warnings,
            failed_requirements=[
                "messy_asian_range_do_not_force_setup",
                "Asian range quality below minimum threshold",
                "London raid logic depends on clean Asian range boundaries",
            ],
        )

    avg_range = _average_ranges(candles, atr_period)
    sweep = sweep_buffer if sweep_buffer is not None else _default_buffer(asian, avg_range, 0.05)
    close = close_buffer if close_buffer is not None else _default_buffer(asian, avg_range, 0.04)
    brk = break_buffer if break_buffer is not None else _default_buffer(asian, avg_range, 0.04)
    stop = stop_buffer if stop_buffer is not None else _default_buffer(asian, avg_range, 0.08)
    london_candles = _filter_london_window(candles, window, asian.session_end)
    if not london_candles:
        return _empty_result(
            LondonRaidType.OUTSIDE_LONDON_WINDOW,
            "no_closed_candles_inside_london_window",
            asian,
            london_window,
            htf_bias,
            warnings,
        )

    candidates: list[dict[str, Any]] = []
    for offset, candle in enumerate(london_candles):
        high_candidate = _classify_high_raid(
            candle,
            offset,
            london_candles,
            asian,
            htf_bias,
            sweep,
            close,
            brk,
            stop,
            avg_range,
            min_displacement_body_ratio,
            min_displacement_range_ratio,
            min_rr,
        )
        if high_candidate:
            candidates.append(high_candidate)
        low_candidate = _classify_low_raid(
            candle,
            offset,
            london_candles,
            asian,
            htf_bias,
            sweep,
            close,
            brk,
            stop,
            avg_range,
            min_displacement_body_ratio,
            min_displacement_range_ratio,
            min_rr,
        )
        if low_candidate:
            candidates.append(low_candidate)

    if not candidates:
        return _empty_result(
            LondonRaidType.NONE,
            "no_london_raid_of_asian_range_detected",
            asian,
            london_window,
            htf_bias,
            warnings,
        )

    high_swept = any(c["swept_side"] == LondonRaidSweptSide.ASIAN_HIGH.value for c in candidates)
    low_swept = any(c["swept_side"] == LondonRaidSweptSide.ASIAN_LOW.value for c in candidates)
    for candidate in candidates:
        candidate["double_sweep"] = high_swept and low_swept
        if candidate["double_sweep"]:
            candidate["warnings"].append("both_asian_high_and_low_swept_during_london")

    candidates.sort(
        key=lambda c: (
            c["valid_setup"],
            c["quality_score"],
            c["confirmation"]["mss_confirmed"],
        ),
        reverse=True,
    )
    best = candidates[0]
    best["raid_candidates"] = candidates
    if best["quality_score"] < minimum_valid_score:
        best["valid_setup"] = False
        best["classification"] = "low_quality_london_open_raid_candidate"
        best["failed_requirements"].append("quality_score_below_minimum_valid_score")
    best["warnings"] = _dedupe(best["warnings"] + warnings)
    return best


def _classify_high_raid(
    candle: _Candle,
    offset: int,
    london_candles: Sequence[_Candle],
    asian: _AsianRange,
    htf_bias: str,
    sweep_buffer: float,
    close_buffer: float,
    break_buffer: float,
    stop_buffer: float,
    avg_range: float,
    min_body_ratio: float,
    min_range_ratio: float,
    min_rr: float,
) -> dict[str, Any] | None:
    if candle.high <= asian.high:
        return None
    if candle.high <= asian.high + sweep_buffer:
        return _build_candidate(
            candle,
            offset,
            london_candles,
            asian,
            htf_bias,
            LondonRaidType.UNCLEAR_ASIAN_HIGH_RAID,
            LondonRaidDirection.NONE,
            LondonRaidSweptSide.ASIAN_HIGH,
            "buy_side",
            asian.high,
            candle.high,
            LondonRaidReclaimStatus.UNCLEAR,
            "tiny_wick_above_asian_high_below_sweep_buffer",
            stop_buffer,
            avg_range,
            min_body_ratio,
            min_range_ratio,
            break_buffer,
            min_rr,
        )
    if candle.close < asian.high:
        return _build_candidate(
            candle,
            offset,
            london_candles,
            asian,
            htf_bias,
            LondonRaidType.ASIAN_HIGH_SWEEP_REVERSAL,
            LondonRaidDirection.BEARISH,
            LondonRaidSweptSide.ASIAN_HIGH,
            "buy_side",
            asian.high,
            candle.high,
            LondonRaidReclaimStatus.REJECTED_BACK_BELOW_ASIAN_HIGH,
            "candle_high_above_asian_high_and_close_back_below_asian_high",
            stop_buffer,
            avg_range,
            min_body_ratio,
            min_range_ratio,
            break_buffer,
            min_rr,
        )
    if candle.close > asian.high + close_buffer:
        return _build_candidate(
            candle,
            offset,
            london_candles,
            asian,
            htf_bias,
            LondonRaidType.ASIAN_HIGH_BREAKOUT_CONTINUATION,
            LondonRaidDirection.BULLISH_CONTINUATION,
            LondonRaidSweptSide.ASIAN_HIGH,
            "buy_side",
            asian.high,
            candle.high,
            LondonRaidReclaimStatus.ACCEPTED_ABOVE_ASIAN_HIGH,
            "candle_close_accepted_above_asian_high",
            stop_buffer,
            avg_range,
            min_body_ratio,
            min_range_ratio,
            break_buffer,
            min_rr,
        )
    return _build_candidate(
        candle,
        offset,
        london_candles,
        asian,
        htf_bias,
        LondonRaidType.UNCLEAR_ASIAN_HIGH_RAID,
        LondonRaidDirection.NONE,
        LondonRaidSweptSide.ASIAN_HIGH,
        "buy_side",
        asian.high,
        candle.high,
        LondonRaidReclaimStatus.UNCLEAR,
        "asian_high_raid_close_near_level",
        stop_buffer,
        avg_range,
        min_body_ratio,
        min_range_ratio,
        break_buffer,
        min_rr,
    )


def _classify_low_raid(
    candle: _Candle,
    offset: int,
    london_candles: Sequence[_Candle],
    asian: _AsianRange,
    htf_bias: str,
    sweep_buffer: float,
    close_buffer: float,
    break_buffer: float,
    stop_buffer: float,
    avg_range: float,
    min_body_ratio: float,
    min_range_ratio: float,
    min_rr: float,
) -> dict[str, Any] | None:
    if candle.low >= asian.low:
        return None
    if candle.low >= asian.low - sweep_buffer:
        return _build_candidate(
            candle,
            offset,
            london_candles,
            asian,
            htf_bias,
            LondonRaidType.UNCLEAR_ASIAN_LOW_RAID,
            LondonRaidDirection.NONE,
            LondonRaidSweptSide.ASIAN_LOW,
            "sell_side",
            asian.low,
            candle.low,
            LondonRaidReclaimStatus.UNCLEAR,
            "tiny_wick_below_asian_low_below_sweep_buffer",
            stop_buffer,
            avg_range,
            min_body_ratio,
            min_range_ratio,
            break_buffer,
            min_rr,
        )
    if candle.close > asian.low:
        return _build_candidate(
            candle,
            offset,
            london_candles,
            asian,
            htf_bias,
            LondonRaidType.ASIAN_LOW_SWEEP_REVERSAL,
            LondonRaidDirection.BULLISH,
            LondonRaidSweptSide.ASIAN_LOW,
            "sell_side",
            asian.low,
            candle.low,
            LondonRaidReclaimStatus.RECLAIMED_BACK_ABOVE_ASIAN_LOW,
            "candle_low_below_asian_low_and_close_back_above_asian_low",
            stop_buffer,
            avg_range,
            min_body_ratio,
            min_range_ratio,
            break_buffer,
            min_rr,
        )
    if candle.close < asian.low - close_buffer:
        return _build_candidate(
            candle,
            offset,
            london_candles,
            asian,
            htf_bias,
            LondonRaidType.ASIAN_LOW_BREAKDOWN_CONTINUATION,
            LondonRaidDirection.BEARISH_CONTINUATION,
            LondonRaidSweptSide.ASIAN_LOW,
            "sell_side",
            asian.low,
            candle.low,
            LondonRaidReclaimStatus.ACCEPTED_BELOW_ASIAN_LOW,
            "candle_close_accepted_below_asian_low",
            stop_buffer,
            avg_range,
            min_body_ratio,
            min_range_ratio,
            break_buffer,
            min_rr,
        )
    return _build_candidate(
        candle,
        offset,
        london_candles,
        asian,
        htf_bias,
        LondonRaidType.UNCLEAR_ASIAN_LOW_RAID,
        LondonRaidDirection.NONE,
        LondonRaidSweptSide.ASIAN_LOW,
        "sell_side",
        asian.low,
        candle.low,
        LondonRaidReclaimStatus.UNCLEAR,
        "asian_low_raid_close_near_level",
        stop_buffer,
        avg_range,
        min_body_ratio,
        min_range_ratio,
        break_buffer,
        min_rr,
    )


def _build_candidate(
    candle: _Candle,
    offset: int,
    london_candles: Sequence[_Candle],
    asian: _AsianRange,
    htf_bias: str,
    raid_type: LondonRaidType,
    direction: LondonRaidDirection,
    swept_side: LondonRaidSweptSide,
    swept_liquidity: str,
    sweep_level: float,
    sweep_extreme: float,
    reclaim_status: LondonRaidReclaimStatus,
    condition: str,
    stop_buffer: float,
    avg_range: float,
    min_body_ratio: float,
    min_range_ratio: float,
    break_buffer: float,
    min_rr: float,
) -> dict[str, Any]:
    confirmation = _confirm_after_sweep(
        london_candles,
        offset,
        direction,
        avg_range,
        min_body_ratio,
        min_range_ratio,
        break_buffer,
    )
    effective_raid_type = _downgrade_unconfirmed_reversal(raid_type, confirmation)
    entry_zone = _entry_zone(london_candles, offset, direction, confirmation)
    trade_plan = _trade_plan(
        asian,
        direction,
        entry_zone,
        sweep_extreme,
        stop_buffer,
    )
    htf_alignment = _htf_alignment(direction, htf_bias)
    failed_requirements = _failed_requirements(
        raid_type,
        confirmation,
        entry_zone,
        trade_plan,
        min_rr,
    )
    valid_setup = not failed_requirements and raid_type in {
        LondonRaidType.ASIAN_HIGH_SWEEP_REVERSAL,
        LondonRaidType.ASIAN_LOW_SWEEP_REVERSAL,
        LondonRaidType.ASIAN_HIGH_BREAKOUT_CONTINUATION,
        LondonRaidType.ASIAN_LOW_BREAKDOWN_CONTINUATION,
    }
    quality_score = _quality_score(
        asian,
        effective_raid_type,
        confirmation,
        entry_zone,
        trade_plan,
        htf_alignment,
        valid_setup,
    )
    return {
        "concept_name": "London Open Liquidity Raid",
        "symbol": candle.symbol,
        "timeframe": candle.timeframe,
        "setup_id": (
            f"LONDON_RAID_{direction.value.upper()}_{candle.timestamp.strftime('%Y%m%d_%H%M%S')}"
            if valid_setup
            else None
        ),
        "raid_detected": _is_actual_raid(effective_raid_type),
        "valid_setup": valid_setup,
        "classification": effective_raid_type.value,
        "raid_type": effective_raid_type.value,
        "direction": direction.value,
        "swept_side": swept_side.value,
        "swept_liquidity": swept_liquidity,
        "reclaim_status": reclaim_status.value,
        "mss_confirmed": confirmation["mss_confirmed"],
        "displacement_confirmed": confirmation["displacement_confirmed"],
        "entry_zone": entry_zone,
        "target_liquidity": trade_plan["target_liquidity"],
        "entry": trade_plan["entry"],
        "stop": trade_plan["stop"],
        "target": trade_plan["second_target"],
        "risk_reward": trade_plan["risk_reward_to_second_target"],
        "quality_score": quality_score,
        "quality_grade": _quality_grade(quality_score, valid_setup).value,
        "asian_range": _asian_payload(asian),
        "sweep": {
            "swept_side": swept_side.value,
            "swept_liquidity": swept_liquidity,
            "sweep_level": round(sweep_level, 6),
            "sweep_index": candle.index,
            "sweep_timestamp": candle.timestamp.isoformat(),
            "sweep_extreme": round(sweep_extreme, 6),
            "reclaim_status": reclaim_status.value,
            "sweep_condition": condition,
        },
        "confirmation": confirmation,
        "trade_plan": trade_plan,
        "context": {
            "htf_bias": htf_bias,
            "htf_alignment": htf_alignment,
        },
        "failed_requirements": failed_requirements,
        "reasons": _reasons(
            effective_raid_type,
            reclaim_status,
            confirmation,
            entry_zone,
            trade_plan,
            htf_alignment,
        ),
        "warnings": [
            "London raid alone is not an entry signal",
            "Entry requires FVG/OB retest reaction and valid risk-to-reward",
        ],
        "entry_allowed_from_london_raid_alone": False,
        "double_sweep": False,
    }


def _downgrade_unconfirmed_reversal(
    raid_type: LondonRaidType,
    confirmation: Mapping[str, Any],
) -> LondonRaidType:
    if confirmation["mss_confirmed"]:
        return raid_type
    if raid_type is LondonRaidType.ASIAN_HIGH_SWEEP_REVERSAL:
        return LondonRaidType.ASIAN_HIGH_SWEEP_CANDIDATE
    if raid_type is LondonRaidType.ASIAN_LOW_SWEEP_REVERSAL:
        return LondonRaidType.ASIAN_LOW_SWEEP_CANDIDATE
    return raid_type


def _is_actual_raid(raid_type: LondonRaidType) -> bool:
    return raid_type not in {
        LondonRaidType.NONE,
        LondonRaidType.MESSY_ASIAN_RANGE,
        LondonRaidType.OUTSIDE_LONDON_WINDOW,
        LondonRaidType.UNCLEAR_ASIAN_HIGH_RAID,
        LondonRaidType.UNCLEAR_ASIAN_LOW_RAID,
    }


def _confirm_after_sweep(
    candles: Sequence[_Candle],
    offset: int,
    direction: LondonRaidDirection,
    avg_range: float,
    min_body_ratio: float,
    min_range_ratio: float,
    break_buffer: float,
) -> dict[str, Any]:
    follow = list(candles[offset + 1 : offset + 8])
    sweep = candles[offset]
    if not follow:
        return _empty_confirmation()
    bullish = direction in {
        LondonRaidDirection.BULLISH,
        LondonRaidDirection.BULLISH_CONTINUATION,
    }
    bearish = direction in {
        LondonRaidDirection.BEARISH,
        LondonRaidDirection.BEARISH_CONTINUATION,
    }
    if bullish:
        mss_candle = next((c for c in follow if c.close > sweep.high + break_buffer), None)
        displacement = next(
            (
                c
                for c in follow
                if c.bullish
                and c.body >= c.range * min_body_ratio
                and c.range >= avg_range * min_range_ratio
                and c.bullish_close_position >= 0.70
            ),
            None,
        )
        fvg = _find_bullish_fvg([sweep, *follow])
        fvg_type = "bullish_fvg" if fvg else None
        direction_value = "bullish" if mss_candle else None
    elif bearish:
        mss_candle = next((c for c in follow if c.close < sweep.low - break_buffer), None)
        displacement = next(
            (
                c
                for c in follow
                if c.bearish
                and c.body >= c.range * min_body_ratio
                and c.range >= avg_range * min_range_ratio
                and c.bearish_close_position >= 0.70
            ),
            None,
        )
        fvg = _find_bearish_fvg([sweep, *follow])
        fvg_type = "bearish_fvg" if fvg else None
        direction_value = "bearish" if mss_candle else None
    else:
        mss_candle = None
        displacement = None
        fvg = None
        fvg_type = None
        direction_value = None
    return {
        "mss_confirmed": mss_candle is not None,
        "mss_direction": direction_value,
        "mss_confirmation_index": mss_candle.index if mss_candle else None,
        "broken_level": (
            round(sweep.high, 6)
            if bullish and mss_candle
            else round(sweep.low, 6)
            if bearish and mss_candle
            else None
        ),
        "displacement_confirmed": displacement is not None,
        "displacement_direction": direction_value if displacement else None,
        "displacement_strength": "strong" if displacement else "none",
        "displacement_start_index": displacement.index if displacement else None,
        "displacement_end_index": displacement.index if displacement else None,
        "fvg_created": fvg is not None,
        "fvg_type": fvg_type,
        "fvg_zone": fvg,
    }


def _entry_zone(
    candles: Sequence[_Candle],
    offset: int,
    direction: LondonRaidDirection,
    confirmation: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not confirmation["mss_confirmed"] or not confirmation["displacement_confirmed"]:
        return None
    fvg = confirmation.get("fvg_zone")
    if fvg:
        source = (
            "bullish_MSS_after_London_sweep_of_Asian_low"
            if "bullish" in str(confirmation.get("fvg_type"))
            else "bearish_MSS_after_London_sweep_of_Asian_high"
        )
        return {
            "entry_zone_type": confirmation["fvg_type"],
            "zone_low": fvg["zone_low"],
            "zone_high": fvg["zone_high"],
            "zone_mid": round((fvg["zone_low"] + fvg["zone_high"]) / 2.0, 6),
            "source_event": source,
            "invalidation_level": round(
                candles[offset].low if "bullish" in source else candles[offset].high,
                6,
            ),
        }
    ob = _order_block_zone(candles, offset, direction)
    return ob


def _order_block_zone(
    candles: Sequence[_Candle],
    offset: int,
    direction: LondonRaidDirection,
) -> dict[str, Any] | None:
    follow = list(candles[offset + 1 : offset + 6])
    if direction in {LondonRaidDirection.BULLISH, LondonRaidDirection.BULLISH_CONTINUATION}:
        ob = next((c for c in reversed(follow) if c.bearish), None)
        if ob:
            return {
                "entry_zone_type": "bullish_order_block",
                "zone_low": round(ob.low, 6),
                "zone_high": round(ob.high, 6),
                "zone_mid": round((ob.low + ob.high) / 2.0, 6),
                "source_event": "last_bearish_candle_before_bullish_displacement",
                "invalidation_level": round(candles[offset].low, 6),
            }
    if direction in {LondonRaidDirection.BEARISH, LondonRaidDirection.BEARISH_CONTINUATION}:
        ob = next((c for c in reversed(follow) if c.bullish), None)
        if ob:
            return {
                "entry_zone_type": "bearish_order_block",
                "zone_low": round(ob.low, 6),
                "zone_high": round(ob.high, 6),
                "zone_mid": round((ob.low + ob.high) / 2.0, 6),
                "source_event": "last_bullish_candle_before_bearish_displacement",
                "invalidation_level": round(candles[offset].high, 6),
            }
    return None


def _trade_plan(
    asian: _AsianRange,
    direction: LondonRaidDirection,
    entry_zone: Mapping[str, Any] | None,
    sweep_extreme: float,
    stop_buffer: float,
) -> dict[str, Any]:
    bullish = direction in {
        LondonRaidDirection.BULLISH,
        LondonRaidDirection.BULLISH_CONTINUATION,
    }
    bearish = direction in {
        LondonRaidDirection.BEARISH,
        LondonRaidDirection.BEARISH_CONTINUATION,
    }
    entry = float(entry_zone["zone_mid"]) if entry_zone else None
    if bullish:
        stop = round(sweep_extreme - stop_buffer, 6)
        first_target = asian.midpoint
        second_target = asian.high
        target_side = "buy_side"
        final_target = "PDH_or_external_buy_side_liquidity"
        rr = _risk_reward(entry, stop, second_target, bullish=True)
    elif bearish:
        stop = round(sweep_extreme + stop_buffer, 6)
        first_target = asian.midpoint
        second_target = asian.low
        target_side = "sell_side"
        final_target = "PDL_or_external_sell_side_liquidity"
        rr = _risk_reward(entry, stop, second_target, bullish=False)
    else:
        stop = None
        first_target = None
        second_target = None
        target_side = None
        final_target = None
        rr = None
    return {
        "entry": round(entry, 6) if entry is not None else None,
        "entry_model": entry_zone.get("entry_zone_type") if entry_zone else None,
        "stop": stop,
        "stop_reference": "beyond_london_sweep_extreme_with_buffer" if stop else None,
        "first_target": round(first_target, 6) if first_target is not None else None,
        "first_target_name": "Asian midpoint" if first_target is not None else None,
        "second_target": round(second_target, 6) if second_target is not None else None,
        "second_target_name": (
            "Asian high"
            if bullish
            else "Asian low"
            if bearish
            else None
        ),
        "final_target": final_target,
        "target_side": target_side,
        "target_liquidity": {
            "target_side": target_side,
            "first_target": "asian_midpoint" if first_target is not None else None,
            "first_target_price": round(first_target, 6) if first_target is not None else None,
            "second_target": "asian_high" if bullish else "asian_low" if bearish else None,
            "second_target_price": round(second_target, 6) if second_target is not None else None,
            "final_target": final_target,
        },
        "risk_reward_to_second_target": rr,
    }


def _failed_requirements(
    raid_type: LondonRaidType,
    confirmation: Mapping[str, Any],
    entry_zone: Mapping[str, Any] | None,
    trade_plan: Mapping[str, Any],
    min_rr: float,
) -> list[str]:
    if raid_type in {
        LondonRaidType.UNCLEAR_ASIAN_HIGH_RAID,
        LondonRaidType.UNCLEAR_ASIAN_LOW_RAID,
    }:
        return ["raid_unclear_or_below_sweep_buffer"]
    failed = []
    reversal = raid_type in {
        LondonRaidType.ASIAN_HIGH_SWEEP_REVERSAL,
        LondonRaidType.ASIAN_LOW_SWEEP_REVERSAL,
    }
    if reversal and not confirmation["mss_confirmed"]:
        failed.append("mss_not_confirmed_after_london_raid")
    if not confirmation["displacement_confirmed"]:
        failed.append("displacement_not_confirmed_after_london_raid")
    if entry_zone is None:
        failed.append("no_fvg_or_order_block_entry_zone")
    rr = trade_plan.get("risk_reward_to_second_target")
    if rr is None or rr < min_rr:
        failed.append("risk_reward_below_minimum")
    return failed


def _quality_score(
    asian: _AsianRange,
    raid_type: LondonRaidType,
    confirmation: Mapping[str, Any],
    entry_zone: Mapping[str, Any] | None,
    trade_plan: Mapping[str, Any],
    htf_alignment: bool | None,
    valid_setup: bool,
) -> float:
    if raid_type in {
        LondonRaidType.UNCLEAR_ASIAN_HIGH_RAID,
        LondonRaidType.UNCLEAR_ASIAN_LOW_RAID,
    }:
        return 2.5
    if raid_type in {
        LondonRaidType.ASIAN_HIGH_SWEEP_CANDIDATE,
        LondonRaidType.ASIAN_LOW_SWEEP_CANDIDATE,
    }:
        score_cap = 5.0
    else:
        score_cap = 5.5
    score = min(2.0, asian.quality_score / 5.0)
    score += 1.5
    if confirmation["mss_confirmed"]:
        score += 1.5
    if confirmation["displacement_confirmed"]:
        score += 1.5
    if confirmation["fvg_created"]:
        score += 1.0
    elif entry_zone is not None:
        score += 0.5
    if trade_plan.get("target_liquidity", {}).get("second_target"):
        score += 0.8
    if htf_alignment is True:
        score += 0.7
    elif htf_alignment is False:
        score -= 0.7
    if not valid_setup:
        score = min(score, score_cap)
    return round(max(0.0, min(10.0, score)), 4)


def _quality_grade(score: float, valid_setup: bool) -> LondonRaidQualityGrade:
    if not valid_setup:
        return LondonRaidQualityGrade.INVALID if score < 5.0 else LondonRaidQualityGrade.WATCHLIST
    if score >= 8.0:
        return LondonRaidQualityGrade.STRONG
    if score >= 7.0:
        return LondonRaidQualityGrade.VALID
    return LondonRaidQualityGrade.WATCHLIST


def _parse_asian_range(raw: Mapping[str, Any]) -> _AsianRange | None:
    levels = raw.get("session_levels", raw)
    high = raw.get("asian_high", levels.get("session_high"))
    low = raw.get("asian_low", levels.get("session_low"))
    if high is None or low is None:
        return None
    high_f = float(high)
    low_f = float(low)
    midpoint = float(
        raw.get("asian_midpoint", levels.get("session_midpoint", (high_f + low_f) / 2.0))
    )
    range_size = float(
        raw.get("asian_range_size", levels.get("session_range_size", high_f - low_f))
    )
    quality = float(raw.get("range_quality_score", raw.get("quality_score", 0.0)))
    session_end = _coerce_datetime(
        raw.get("session_end")
        or raw.get("session_end_timestamp")
        or levels.get("session_end_timestamp")
    )
    return _AsianRange(high_f, low_f, midpoint, range_size, quality, session_end)


def _parse_london_window(
    raw: Mapping[str, Any],
    warnings: list[str],
) -> _LondonWindow | None:
    start = _parse_time(raw.get("start_time"))
    end = _parse_time(raw.get("end_time"))
    if start is None or end is None or start == end:
        warnings.append("invalid_london_window_time")
        return None
    timezone_name = str(raw.get("timezone") or "UTC")
    tz = _resolve_timezone(timezone_name, warnings)
    return _LondonWindow(
        name=str(raw.get("window_name") or "london_open"),
        start_time=start,
        end_time=end,
        timezone=tz,
        timezone_name=timezone_name,
        allowed_days=set(raw.get("allowed_days") or []),
        strict_mode=bool(raw.get("strict_mode", True)),
        post_window_buffer_minutes=int(raw.get("post_window_buffer_minutes", 0)),
    )


def _filter_london_window(
    candles: Sequence[_Candle],
    window: _LondonWindow,
    asian_session_end: datetime | None,
) -> list[_Candle]:
    selected = []
    for candle in candles:
        if asian_session_end and candle.timestamp <= asian_session_end:
            continue
        converted = candle.timestamp.astimezone(window.timezone)
        if window.allowed_days and converted.strftime("%A") not in window.allowed_days:
            continue
        if _time_inside_london_window(converted, window):
            selected.append(candle)
    return selected


def _time_inside_london_window(value: datetime, window: _LondonWindow) -> bool:
    if _time_inside(value.time(), window.start_time, window.end_time):
        return True
    if window.post_window_buffer_minutes <= 0:
        return False
    end_value = value.replace(
        hour=window.end_time.hour,
        minute=window.end_time.minute,
        second=0,
        microsecond=0,
    )
    if window.start_time > window.end_time and value.time() > window.end_time:
        end_value += timedelta(days=1)
    return end_value < value <= end_value + timedelta(
        minutes=window.post_window_buffer_minutes
    )


def _time_inside(value: time, start: time, end: time) -> bool:
    if start < end:
        return start <= value <= end
    return value >= start or value <= end


def _normalize_candles(df: Sequence[Mapping[str, Any] | Any] | Any) -> list[_Candle]:
    records = df.to_dict("records") if hasattr(df, "to_dict") else list(df or [])
    candles: list[_Candle] = []
    for fallback_index, row in enumerate(records):
        get = row.get if isinstance(row, Mapping) else lambda k, d=None: getattr(row, k, d)
        timestamp = _coerce_datetime(get("timestamp"))
        if timestamp is None:
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=dt_timezone.utc)
        candles.append(
            _Candle(
                index=int(get("index", fallback_index)),
                timestamp=timestamp,
                open=float(get("open", 0.0)),
                high=float(get("high", 0.0)),
                low=float(get("low", 0.0)),
                close=float(get("close", 0.0)),
                volume=float(get("volume", 0.0)),
                timeframe=str(get("timeframe", "unknown")),
                symbol=str(get("symbol", "unknown")),
                is_closed=bool(get("is_closed", True)),
            )
        )
    candles.sort(key=lambda candle: candle.timestamp)
    return candles


def _find_bullish_fvg(candles: Sequence[_Candle]) -> dict[str, Any] | None:
    for i in range(len(candles) - 2):
        if candles[i].high < candles[i + 2].low:
            return {
                "zone_low": round(candles[i].high, 6),
                "zone_high": round(candles[i + 2].low, 6),
                "creation_index": candles[i + 2].index,
            }
    return None


def _find_bearish_fvg(candles: Sequence[_Candle]) -> dict[str, Any] | None:
    for i in range(len(candles) - 2):
        if candles[i].low > candles[i + 2].high:
            return {
                "zone_low": round(candles[i + 2].high, 6),
                "zone_high": round(candles[i].low, 6),
                "creation_index": candles[i + 2].index,
            }
    return None


def _htf_alignment(direction: LondonRaidDirection, htf_bias: str) -> bool | None:
    bias = str(htf_bias).lower()
    if bias in {"neutral", "ranging", "unknown", "none", ""}:
        return None
    if direction in {LondonRaidDirection.BULLISH, LondonRaidDirection.BULLISH_CONTINUATION}:
        return bias == "bullish"
    if direction in {LondonRaidDirection.BEARISH, LondonRaidDirection.BEARISH_CONTINUATION}:
        return bias == "bearish"
    return None


def _risk_reward(
    entry: float | None,
    stop: float | None,
    target: float | None,
    *,
    bullish: bool,
) -> float | None:
    if entry is None or stop is None or target is None:
        return None
    risk = entry - stop if bullish else stop - entry
    reward = target - entry if bullish else entry - target
    if risk <= 0 or reward <= 0:
        return None
    return round(reward / risk, 4)


def _average_ranges(candles: Sequence[_Candle], period: int) -> float:
    ranges = [c.range for c in candles[-period:] if c.range > 0]
    return sum(ranges) / len(ranges) if ranges else 0.00001


def _default_buffer(asian: _AsianRange, avg_range: float, multiplier: float) -> float:
    return max(avg_range * multiplier, asian.range_size * multiplier * 0.2, 0.00001)


def _parse_time(value: Any) -> time | None:
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        return time(int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_timezone.utc)
        except ValueError:
            return None
    return None


def _resolve_timezone(name: str, warnings: list[str]) -> tzinfo:
    if not name or name in {"broker_timezone", "broker"}:
        warnings.append("london_window_timezone_unknown_assumed_UTC")
        return dt_timezone.utc
    if name.upper() == "UTC":
        return dt_timezone.utc
    offset = _offset_timezone(name)
    if offset:
        return offset
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        fallback = _FIXED_ZONE_FALLBACKS.get(name)
        if fallback is not None:
            warnings.append(f"london_window_fixed_offset_fallback_used:{name}")
            return dt_timezone(timedelta(hours=fallback), name)
        warnings.append(f"london_window_timezone_unknown_assumed_UTC:{name}")
        return dt_timezone.utc


def _offset_timezone(value: str) -> tzinfo | None:
    value = value.strip()
    if len(value) != 6 or value[0] not in "+-" or value[3] != ":":
        return None
    try:
        hours = int(value[1:3])
        minutes = int(value[4:6])
    except ValueError:
        return None
    sign = 1 if value[0] == "+" else -1
    return dt_timezone(timedelta(hours=hours, minutes=minutes) * sign, value)


def _asian_payload(asian: _AsianRange) -> dict[str, Any]:
    return {
        "asian_high": round(asian.high, 6),
        "asian_low": round(asian.low, 6),
        "asian_midpoint": round(asian.midpoint, 6),
        "asian_range_size": round(asian.range_size, 6),
        "range_quality_score": round(asian.quality_score, 4),
    }


def _empty_confirmation() -> dict[str, Any]:
    return {
        "mss_confirmed": False,
        "mss_direction": None,
        "mss_confirmation_index": None,
        "broken_level": None,
        "displacement_confirmed": False,
        "displacement_direction": None,
        "displacement_strength": "none",
        "displacement_start_index": None,
        "displacement_end_index": None,
        "fvg_created": False,
        "fvg_type": None,
        "fvg_zone": None,
    }


def _reasons(
    raid_type: LondonRaidType,
    reclaim_status: LondonRaidReclaimStatus,
    confirmation: Mapping[str, Any],
    entry_zone: Mapping[str, Any] | None,
    trade_plan: Mapping[str, Any],
    htf_alignment: bool | None,
) -> list[str]:
    reasons = [f"London classified event as {raid_type.value}"]
    if reclaim_status is not LondonRaidReclaimStatus.UNCLEAR:
        reasons.append(f"Reclaim status: {reclaim_status.value}")
    if confirmation["mss_confirmed"]:
        reasons.append("MSS confirmed with candle close after London raid")
    if confirmation["displacement_confirmed"]:
        reasons.append("Displacement confirmed after raid")
    if entry_zone:
        reasons.append(f"Entry zone detected: {entry_zone['entry_zone_type']}")
    if trade_plan.get("target_liquidity", {}).get("second_target"):
        reasons.append("Target liquidity exists at Asian midpoint/range boundary")
    if htf_alignment is True:
        reasons.append("HTF bias aligns with London raid direction")
    elif htf_alignment is False:
        reasons.append("HTF bias conflicts with London raid direction")
    return reasons


def _empty_result(
    raid_type: LondonRaidType,
    reason: str,
    asian: _AsianRange | None,
    london_window: Mapping[str, Any],
    htf_bias: str,
    warnings: list[str],
    *,
    failed_requirements: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "concept_name": "London Open Liquidity Raid",
        "symbol": "unknown",
        "timeframe": "unknown",
        "setup_id": None,
        "raid_detected": False,
        "valid_setup": False,
        "classification": raid_type.value,
        "raid_type": raid_type.value,
        "direction": LondonRaidDirection.NONE.value,
        "swept_side": LondonRaidSweptSide.NONE.value,
        "swept_liquidity": None,
        "reclaim_status": LondonRaidReclaimStatus.NONE.value,
        "mss_confirmed": False,
        "displacement_confirmed": False,
        "entry_zone": None,
        "target_liquidity": None,
        "entry": None,
        "stop": None,
        "target": None,
        "risk_reward": None,
        "quality_score": 0.0,
        "quality_grade": LondonRaidQualityGrade.INVALID.value,
        "asian_range": _asian_payload(asian) if asian else None,
        "london_window": dict(london_window),
        "context": {"htf_bias": htf_bias, "htf_alignment": None},
        "failed_requirements": failed_requirements or [reason],
        "reasons": [reason],
        "warnings": _dedupe(
            warnings
            + [
                "Do not force a London raid setup every day",
                "London raid alone is not an entry signal",
            ]
        ),
        "entry_allowed_from_london_raid_alone": False,
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
