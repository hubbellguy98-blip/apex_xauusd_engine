"""Rule-based Judas Swing detection for ICT/SMC session manipulation.

The Judas Swing model is treated as a full sequence:
accumulation -> manipulation sweep -> reclaim/rejection -> MSS -> displacement
-> FVG/OB retracement context -> opposite-side liquidity target.

This module is deterministic analytics only. It does not authorize execution
from a range sweep alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone as dt_timezone
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence

from src.core.domain.market_data import CandleNode


class JudasType(str, Enum):
    NONE = "none"
    BULLISH = "bullish_judas"
    BEARISH = "bearish_judas"
    BULLISH_CANDIDATE = "bullish_judas_candidate"
    BEARISH_CANDIDATE = "bearish_judas_candidate"
    INVALID_BULLISH = "invalid_bullish_judas_candidate"
    INVALID_BEARISH = "invalid_bearish_judas_candidate"
    WEAK_BULLISH = "weak_bullish_judas_candidate"
    WEAK_BEARISH = "weak_bearish_judas_candidate"


class JudasManipulationSide(str, Enum):
    NONE = "none"
    BELOW_RANGE = "below_range"
    ABOVE_RANGE = "above_range"


class JudasLiquiditySide(str, Enum):
    NONE = "none"
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


class JudasReclaimStatus(str, Enum):
    NONE = "none"
    RECLAIMED_BACK_INSIDE = "reclaimed_back_inside_range"
    REJECTED_BACK_INSIDE = "rejected_back_inside_range"
    ACCEPTED_BELOW = "accepted_below_range"
    ACCEPTED_ABOVE = "accepted_above_range"
    UNCLEAR = "unclear"


class JudasClassification(str, Enum):
    NONE = "none"
    BULLISH_JUDAS = "bullish_judas_reversal"
    BEARISH_JUDAS = "bearish_judas_reversal"
    BEARISH_BREAKDOWN = "bearish_breakdown_continuation_not_bullish_judas"
    BULLISH_BREAKOUT = "bullish_breakout_continuation_not_bearish_judas"
    UNCLEAR_DOWNSIDE = "unclear_downside_raid"
    UNCLEAR_UPSIDE = "unclear_upside_raid"


class JudasQualityGrade(str, Enum):
    INVALID = "invalid"
    WEAK = "weak"
    CANDIDATE = "candidate"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


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
class _SessionRange:
    session_name: str
    range_high: float
    range_low: float
    range_midpoint: float
    range_size: float
    session_start: str | None
    session_end: str | None
    timezone: str
    quality_score: float
    session_end_index: int | None


@dataclass(frozen=True, slots=True)
class _JudasCandidate:
    judas_type: JudasType
    classification: JudasClassification
    manipulation_side: JudasManipulationSide
    swept_liquidity: JudasLiquiditySide
    reclaim_status: JudasReclaimStatus
    direction: str | None
    sweep_candle: _Candle
    sweep_level: float
    sweep_extreme: float
    sweep_confirmed: bool
    continuation_confirmed: bool
    condition: str


def detect_judas_swing(
    df: Sequence[CandleNode | Mapping[str, Any]] | Any,
    session_range: Mapping[str, Any],
    htf_bias: str,
    *,
    sweep_buffer: float | None = None,
    close_buffer: float | None = None,
    atr_period: int = 14,
    buffer_atr_multiplier: float = 0.05,
    min_range_atr_multiplier: float = 1.0,
    max_range_atr_multiplier: float = 10.0,
    min_inside_ratio: float = 0.70,
    mss_events: Sequence[Mapping[str, Any] | Any] | None = None,
    fvg_events: Sequence[Mapping[str, Any] | Any] | None = None,
    order_blocks: Sequence[Mapping[str, Any] | Any] | None = None,
    target_liquidity: Sequence[Mapping[str, Any] | Any] | None = None,
    active_session: str | None = None,
    symbol: str = "unknown",
    timeframe: str | None = None,
) -> dict[str, Any]:
    """Detect the highest-quality Judas Swing setup from closed candles."""
    candles = [candle for candle in _normalize_candles(df, timeframe, symbol) if candle.is_closed]
    parsed_range = _parse_session_range(session_range)
    if not candles or parsed_range is None:
        return _empty_result("missing_closed_candles_or_session_range", symbol, timeframe, htf_bias)

    atr = _calculate_atr(candles, atr_period)[-1]
    sweep = sweep_buffer if sweep_buffer is not None else max(atr * buffer_atr_multiplier, 0.00001)
    close = close_buffer if close_buffer is not None else sweep
    accumulation = _accumulation_context(
        candles,
        parsed_range,
        atr,
        min_range_atr_multiplier,
        max_range_atr_multiplier,
        min_inside_ratio,
    )
    scan_candles = _post_session_candles(candles, parsed_range)
    if not scan_candles:
        return _empty_result("no_post_accumulation_candles_to_scan", candles[0].symbol, timeframe, htf_bias)

    candidates = [
        candidate
        for candle in scan_candles
        for candidate in _candidate_from_candle(candle, parsed_range, sweep, close)
    ]
    if not candidates:
        result = _empty_result(
            "no_judas_manipulation_sweep_or_breakout_detected",
            candles[0].symbol,
            timeframe or candles[-1].timeframe,
            htf_bias,
        )
        result["session_context"] = _session_context(parsed_range, accumulation)
        return result

    enriched = [
        _build_result(
            candidate,
            candles,
            parsed_range,
            accumulation,
            htf_bias,
            atr,
            mss_events,
            fvg_events,
            order_blocks,
            target_liquidity,
            active_session,
            timeframe,
            symbol,
        )
        for candidate in candidates
    ]
    enriched.sort(key=lambda item: item["quality_score"], reverse=True)
    best = enriched[0]
    best["candidate_count"] = len(enriched)
    best["double_sweep"] = len({item["manipulation_side"] for item in enriched}) > 1
    best["alternative_candidates"] = [
        {
            "judas_type": item["judas_type"],
            "classification": item["classification"],
            "sweep_index": item["manipulation"]["sweep_index"],
            "quality_score": item["quality_score"],
        }
        for item in enriched[1:4]
    ]
    return best


def _candidate_from_candle(
    candle: _Candle,
    session_range: _SessionRange,
    sweep_buffer: float,
    close_buffer: float,
) -> list[_JudasCandidate]:
    candidates: list[_JudasCandidate] = []
    if candle.low < session_range.range_low - sweep_buffer:
        if candle.close > session_range.range_low:
            candidates.append(
                _JudasCandidate(
                    JudasType.BULLISH_CANDIDATE,
                    JudasClassification.BULLISH_JUDAS,
                    JudasManipulationSide.BELOW_RANGE,
                    JudasLiquiditySide.SELL_SIDE,
                    JudasReclaimStatus.RECLAIMED_BACK_INSIDE,
                    "bullish",
                    candle,
                    session_range.range_low,
                    candle.low,
                    True,
                    False,
                    "candle_low_below_range_low_and_close_back_above_range_low",
                )
            )
        elif candle.close < session_range.range_low - close_buffer and candle.bearish:
            candidates.append(
                _JudasCandidate(
                    JudasType.INVALID_BULLISH,
                    JudasClassification.BEARISH_BREAKDOWN,
                    JudasManipulationSide.BELOW_RANGE,
                    JudasLiquiditySide.SELL_SIDE,
                    JudasReclaimStatus.ACCEPTED_BELOW,
                    None,
                    candle,
                    session_range.range_low,
                    candle.low,
                    False,
                    True,
                    "price_closed_below_range_low_with_bearish_acceptance",
                )
            )
        else:
            candidates.append(
                _weak_candidate(
                    candle,
                    session_range.range_low,
                    candle.low,
                    JudasType.WEAK_BULLISH,
                    JudasClassification.UNCLEAR_DOWNSIDE,
                    JudasManipulationSide.BELOW_RANGE,
                    JudasLiquiditySide.SELL_SIDE,
                )
            )
    if candle.high > session_range.range_high + sweep_buffer:
        if candle.close < session_range.range_high:
            candidates.append(
                _JudasCandidate(
                    JudasType.BEARISH_CANDIDATE,
                    JudasClassification.BEARISH_JUDAS,
                    JudasManipulationSide.ABOVE_RANGE,
                    JudasLiquiditySide.BUY_SIDE,
                    JudasReclaimStatus.REJECTED_BACK_INSIDE,
                    "bearish",
                    candle,
                    session_range.range_high,
                    candle.high,
                    True,
                    False,
                    "candle_high_above_range_high_and_close_back_below_range_high",
                )
            )
        elif candle.close > session_range.range_high + close_buffer and candle.bullish:
            candidates.append(
                _JudasCandidate(
                    JudasType.INVALID_BEARISH,
                    JudasClassification.BULLISH_BREAKOUT,
                    JudasManipulationSide.ABOVE_RANGE,
                    JudasLiquiditySide.BUY_SIDE,
                    JudasReclaimStatus.ACCEPTED_ABOVE,
                    None,
                    candle,
                    session_range.range_high,
                    candle.high,
                    False,
                    True,
                    "price_closed_above_range_high_with_bullish_acceptance",
                )
            )
        else:
            candidates.append(
                _weak_candidate(
                    candle,
                    session_range.range_high,
                    candle.high,
                    JudasType.WEAK_BEARISH,
                    JudasClassification.UNCLEAR_UPSIDE,
                    JudasManipulationSide.ABOVE_RANGE,
                    JudasLiquiditySide.BUY_SIDE,
                )
            )
    return candidates


def _build_result(
    candidate: _JudasCandidate,
    candles: Sequence[_Candle],
    session_range: _SessionRange,
    accumulation: Mapping[str, Any],
    htf_bias: str,
    atr: float,
    mss_events: Sequence[Mapping[str, Any] | Any] | None,
    fvg_events: Sequence[Mapping[str, Any] | Any] | None,
    order_blocks: Sequence[Mapping[str, Any] | Any] | None,
    target_liquidity: Sequence[Mapping[str, Any] | Any] | None,
    active_session: str | None,
    timeframe: str | None,
    symbol: str,
) -> dict[str, Any]:
    mss = _find_mss_confirmation(candles, candidate, mss_events)
    displacement = _displacement_context(candles, candidate, mss, atr)
    fvg = _find_fvg(candles, candidate, fvg_events)
    order_block = _find_order_block(candles, candidate, order_blocks)
    entry_zone = _entry_zone(candidate, fvg, order_block)
    targets = _targets(candidate, session_range, target_liquidity)
    reasons, warnings = _reasons_and_warnings(candidate, accumulation, mss, displacement, entry_zone, targets)
    quality_score = _quality_score(
        candidate,
        accumulation,
        mss,
        displacement,
        entry_zone,
        targets,
        htf_bias,
        active_session,
        reasons,
        warnings,
    )
    judas_type = _final_judas_type(candidate, mss["confirmed"], bool(entry_zone), quality_score)
    entry_allowed = bool(
        judas_type in {JudasType.BULLISH, JudasType.BEARISH}
        and mss["confirmed"]
        and displacement["confirmed"]
        and entry_zone
        and targets["target_exists"]
    )

    return {
        "concept_name": "Judas Swing",
        "symbol": candidate.sweep_candle.symbol or symbol,
        "timeframe": timeframe or candidate.sweep_candle.timeframe,
        "judas_id": _judas_id(candidate, judas_type),
        "judas_type": judas_type.value,
        "classification": candidate.classification.value,
        "session_context": _session_context(session_range, accumulation),
        "htf_bias": (htf_bias or "unknown").lower(),
        "manipulation_side": candidate.manipulation_side.value,
        "swept_liquidity": candidate.swept_liquidity.value,
        "sweep_index": candidate.sweep_candle.index,
        "reclaim_status": candidate.reclaim_status.value,
        "manipulation": _manipulation_payload(candidate),
        "reclaim": {
            "reclaim_status": candidate.reclaim_status.value,
            "reclaim_condition": candidate.condition,
            "reclaim_close": round(candidate.sweep_candle.close, 5),
        },
        "confirmation": {
            "mss_confirmed": mss["confirmed"],
            "mss_direction": mss["direction"],
            "mss_confirmation_index": mss["index"],
            "broken_level": _round_optional(mss["broken_level"]),
            "displacement_confirmed": displacement["confirmed"],
            "displacement_strength": displacement["strength"],
            "displacement_start_index": displacement["start_index"],
            "displacement_end_index": displacement["end_index"],
            "fvg_created": fvg["confirmed"],
            "order_block_found": order_block["confirmed"],
        },
        "mss_confirmed": mss["confirmed"],
        "mss_direction": mss["direction"],
        "entry_zone": entry_zone,
        "target_liquidity": targets["target_liquidity"],
        "target_side": targets["target_side"],
        "targets": targets,
        "risk_logic": {
            "entry_allowed_from_judas_sweep_alone": False,
            "entry_allowed_after_fvg_or_ob_retest_reaction": entry_allowed,
            "stop_loss_reference": _stop_loss_reference(candidate),
            "risk_note": _risk_note(candidate),
        },
        "quality_score": quality_score,
        "quality_grade": _quality_grade(quality_score).value,
        "reasons": reasons,
        "warnings": warnings,
    }


def _weak_candidate(
    candle: _Candle,
    level: float,
    extreme: float,
    judas_type: JudasType,
    classification: JudasClassification,
    manipulation_side: JudasManipulationSide,
    liquidity_side: JudasLiquiditySide,
) -> _JudasCandidate:
    return _JudasCandidate(
        judas_type,
        classification,
        manipulation_side,
        liquidity_side,
        JudasReclaimStatus.UNCLEAR,
        None,
        candle,
        level,
        extreme,
        False,
        False,
        "wick_beyond_range_without_clean_reclaim_or_acceptance",
    )


def _accumulation_context(
    candles: Sequence[_Candle],
    session_range: _SessionRange,
    atr: float,
    min_range_atr_multiplier: float,
    max_range_atr_multiplier: float,
    min_inside_ratio: float,
) -> dict[str, Any]:
    session_candles = _session_candles(candles, session_range)
    if not session_candles:
        session_candles = [
            candle
            for candle in candles
            if session_range.session_end_index is None or candle.index <= session_range.session_end_index
        ]
    inside = [
        candle
        for candle in session_candles
        if candle.high <= session_range.range_high and candle.low >= session_range.range_low
    ]
    inside_ratio = len(inside) / len(session_candles) if session_candles else 0.0
    atr_ratio = session_range.range_size / atr if atr > 0 else 0.0
    valid_range_size = (
        atr <= 0
        or min_range_atr_multiplier <= atr_ratio <= max_range_atr_multiplier
    )
    valid = bool(
        session_candles
        and inside_ratio >= min_inside_ratio
        and valid_range_size
        and session_range.quality_score >= 4.0
    )
    warnings: list[str] = []
    if inside_ratio < min_inside_ratio:
        warnings.append("accumulation_inside_range_ratio_below_threshold")
    if atr > 0 and atr_ratio < min_range_atr_multiplier:
        warnings.append("accumulation_range_too_small_relative_to_atr")
    if atr > 0 and atr_ratio > max_range_atr_multiplier:
        warnings.append("accumulation_range_too_large_relative_to_atr")
    if session_range.quality_score < 4.0:
        warnings.append("session_range_quality_score_low")
    return {
        "valid_accumulation": valid,
        "candles_checked": len(session_candles),
        "candles_inside_range": len(inside),
        "inside_range_ratio": round(inside_ratio, 4),
        "range_atr_ratio": round(atr_ratio, 4) if atr > 0 else None,
        "warnings": warnings,
    }


def _find_mss_confirmation(
    candles: Sequence[_Candle],
    candidate: _JudasCandidate,
    mss_events: Sequence[Mapping[str, Any] | Any] | None,
) -> dict[str, Any]:
    direction = candidate.direction
    if direction is None:
        return {"confirmed": False, "direction": None, "index": None, "broken_level": None}
    if mss_events is not None:
        for item in mss_events:
            item_direction = str(_get(item, "direction", _get(item, "mss_direction", ""))).lower()
            item_index = int(_get(item, "index", _get(item, "confirmation_index", -1)))
            broken_level = _get(item, "broken_level", _get(item, "price", None))
            if item_direction == direction and item_index > candidate.sweep_candle.index:
                return {
                    "confirmed": True,
                    "direction": direction,
                    "index": item_index,
                    "broken_level": _optional_float(broken_level),
                }
        return {"confirmed": False, "direction": direction, "index": None, "broken_level": None}

    later = [candle for candle in candles if candle.index > candidate.sweep_candle.index]
    if len(later) < 3:
        return {"confirmed": False, "direction": direction, "index": None, "broken_level": None}
    if direction == "bullish":
        swing_high = max(later[:2], key=lambda candle: candle.high).high
        for candle in later[2:]:
            if candle.close > swing_high:
                return {
                    "confirmed": True,
                    "direction": direction,
                    "index": candle.index,
                    "broken_level": swing_high,
                }
    else:
        swing_low = min(later[:2], key=lambda candle: candle.low).low
        for candle in later[2:]:
            if candle.close < swing_low:
                return {
                    "confirmed": True,
                    "direction": direction,
                    "index": candle.index,
                    "broken_level": swing_low,
                }
    return {"confirmed": False, "direction": direction, "index": None, "broken_level": None}


def _displacement_context(
    candles: Sequence[_Candle],
    candidate: _JudasCandidate,
    mss: Mapping[str, Any],
    atr: float,
) -> dict[str, Any]:
    direction = candidate.direction
    if direction is None or atr <= 0:
        return {"confirmed": False, "strength": "none", "start_index": None, "end_index": None}
    later = [candle for candle in candles if candle.index > candidate.sweep_candle.index][:6]
    strongest: tuple[float, _Candle] | None = None
    for candle in later:
        directional = (direction == "bullish" and candle.bullish) or (direction == "bearish" and candle.bearish)
        close_quality = candle.close_position >= 0.65 if direction == "bullish" else candle.close_position <= 0.35
        body_ratio = candle.body / candle.range if candle.range > 0 else 0.0
        if directional and close_quality and body_ratio >= 0.55:
            strength_value = candle.range / atr
            if strongest is None or strength_value > strongest[0]:
                strongest = (strength_value, candle)
    if strongest is None:
        return {"confirmed": False, "strength": "none", "start_index": None, "end_index": None}
    strength = "strong" if strongest[0] >= 1.2 else "moderate"
    if mss["confirmed"] and strongest[1].index >= int(mss["index"]):
        strength = "strong"
    return {
        "confirmed": True,
        "strength": strength,
        "start_index": strongest[1].index,
        "end_index": strongest[1].index,
    }


def _find_fvg(
    candles: Sequence[_Candle],
    candidate: _JudasCandidate,
    fvg_events: Sequence[Mapping[str, Any] | Any] | None,
) -> dict[str, Any]:
    direction = candidate.direction
    if direction is None:
        return _empty_zone("fvg")
    expected_type = "bullish_fvg" if direction == "bullish" else "bearish_fvg"
    if fvg_events is not None:
        for item in fvg_events:
            item_type = str(_get(item, "type", _get(item, "fvg_type", ""))).lower()
            item_direction = str(_get(item, "direction", "")).lower()
            item_index = int(_get(item, "index", _get(item, "creation_index", -1)))
            if item_index > candidate.sweep_candle.index and (
                item_type == expected_type or item_direction == direction
            ):
                return {
                    "confirmed": True,
                    "type": expected_type,
                    "index": item_index,
                    "zone_low": _optional_float(_get(item, "zone_low")),
                    "zone_high": _optional_float(_get(item, "zone_high")),
                }
        return _empty_zone(expected_type)

    later = [candle for candle in candles if candle.index > candidate.sweep_candle.index]
    for idx in range(len(later) - 2):
        first, _, third = later[idx], later[idx + 1], later[idx + 2]
        if direction == "bullish" and first.high < third.low:
            return {
                "confirmed": True,
                "type": expected_type,
                "index": third.index,
                "zone_low": first.high,
                "zone_high": third.low,
            }
        if direction == "bearish" and first.low > third.high:
            return {
                "confirmed": True,
                "type": expected_type,
                "index": third.index,
                "zone_low": third.high,
                "zone_high": first.low,
            }
    return _empty_zone(expected_type)


def _find_order_block(
    candles: Sequence[_Candle],
    candidate: _JudasCandidate,
    order_blocks: Sequence[Mapping[str, Any] | Any] | None,
) -> dict[str, Any]:
    direction = candidate.direction
    if direction is None:
        return _empty_zone("order_block")
    expected_type = "bullish_order_block" if direction == "bullish" else "bearish_order_block"
    if order_blocks is not None:
        for item in order_blocks:
            item_type = str(_get(item, "type", _get(item, "order_block_type", ""))).lower()
            item_direction = str(_get(item, "direction", "")).lower()
            item_index = int(_get(item, "index", _get(item, "creation_index", -1)))
            if item_index > candidate.sweep_candle.index and (
                item_type == expected_type or item_direction == direction
            ):
                return {
                    "confirmed": True,
                    "type": expected_type,
                    "index": item_index,
                    "zone_low": _optional_float(_get(item, "zone_low")),
                    "zone_high": _optional_float(_get(item, "zone_high")),
                }
        return _empty_zone(expected_type)

    later = [candle for candle in candles if candle.index > candidate.sweep_candle.index]
    if direction == "bullish":
        bearish = [candle for candle in later[:4] if candle.bearish]
        if bearish:
            source = bearish[-1]
            return {
                "confirmed": True,
                "type": expected_type,
                "index": source.index,
                "zone_low": source.low,
                "zone_high": source.open,
            }
    else:
        bullish = [candle for candle in later[:4] if candle.bullish]
        if bullish:
            source = bullish[-1]
            return {
                "confirmed": True,
                "type": expected_type,
                "index": source.index,
                "zone_low": source.open,
                "zone_high": source.high,
            }
    return _empty_zone(expected_type)


def _entry_zone(
    candidate: _JudasCandidate,
    fvg: Mapping[str, Any],
    order_block: Mapping[str, Any],
) -> dict[str, Any] | None:
    zone = fvg if fvg["confirmed"] else order_block
    if not zone["confirmed"]:
        return None
    zone_low = _optional_float(zone["zone_low"])
    zone_high = _optional_float(zone["zone_high"])
    return {
        "entry_zone_type": zone["type"],
        "zone_low": _round_optional(zone_low),
        "zone_high": _round_optional(zone_high),
        "zone_mid": _round_optional(None if zone_low is None or zone_high is None else (zone_low + zone_high) / 2.0),
        "source_event": f"{candidate.direction}_MSS_after_{candidate.manipulation_side.value}_judas_sweep",
        "invalidation_level": round(candidate.sweep_extreme, 5),
    }


def _targets(
    candidate: _JudasCandidate,
    session_range: _SessionRange,
    target_liquidity: Sequence[Mapping[str, Any] | Any] | None,
) -> dict[str, Any]:
    if candidate.direction == "bullish":
        target_side = "buy_side"
        first = {"target_type": "session_midpoint", "price": round(session_range.range_midpoint, 5)}
        second = {"target_type": "session_high", "price": round(session_range.range_high, 5)}
        final_type = "PDH_or_external_buy_side_liquidity"
    elif candidate.direction == "bearish":
        target_side = "sell_side"
        first = {"target_type": "session_midpoint", "price": round(session_range.range_midpoint, 5)}
        second = {"target_type": "session_low", "price": round(session_range.range_low, 5)}
        final_type = "PDL_or_external_sell_side_liquidity"
    else:
        return {
            "target_side": "unknown",
            "target_exists": False,
            "target_liquidity": None,
            "first_target": None,
            "second_target": None,
            "final_target": None,
        }
    external = _select_external_target(target_liquidity, target_side)
    final = {
        "target_type": final_type,
        "price": _round_optional(_get(external, "price", None)) if external else None,
    }
    return {
        "target_side": target_side,
        "target_exists": True,
        "target_liquidity": external or final,
        "first_target": first,
        "second_target": second,
        "final_target": final,
    }


def _quality_score(
    candidate: _JudasCandidate,
    accumulation: Mapping[str, Any],
    mss: Mapping[str, Any],
    displacement: Mapping[str, Any],
    entry_zone: Mapping[str, Any] | None,
    targets: Mapping[str, Any],
    htf_bias: str,
    active_session: str | None,
    reasons: list[str],
    warnings: list[str],
) -> float:
    if candidate.continuation_confirmed:
        return 2.4
    if candidate.reclaim_status == JudasReclaimStatus.UNCLEAR:
        return 3.0

    score = 2.0
    if accumulation["valid_accumulation"]:
        score += 1.5
        reasons.append("clean_pre_session_accumulation_range_detected")
    else:
        warnings.extend(accumulation["warnings"])
    if candidate.sweep_confirmed:
        score += 1.5
        reasons.append(f"{candidate.swept_liquidity.value}_liquidity_swept")
    if mss["confirmed"]:
        score += 2.0
        reasons.append("MSS_confirmed_after_manipulation_with_candle_close")
    else:
        warnings.append("no_MSS_after_judas_manipulation")
    if displacement["confirmed"]:
        score += 1.0 if displacement["strength"] == "strong" else 0.5
        reasons.append(f"{displacement['strength']}_displacement_confirmed_after_sweep")
    else:
        warnings.append("no_clear_displacement_after_judas_manipulation")
    if entry_zone:
        score += 1.0
        reasons.append("FVG_or_order_block_entry_zone_available_after_confirmation")
    else:
        warnings.append("no_FVG_or_order_block_entry_zone_after_MSS")
    if targets["target_exists"]:
        score += 0.75
        reasons.append("opposite_side_liquidity_target_available")
    else:
        warnings.append("no_opposite_liquidity_target_available")
    bias = (htf_bias or "unknown").lower()
    if candidate.direction and bias == candidate.direction:
        score += 0.75
        reasons.append("HTF_bias_aligns_with_judas_direction")
    elif candidate.direction and bias in {"bullish", "bearish"} and bias != candidate.direction:
        score -= 1.0
        warnings.append("HTF_bias_conflicts_with_judas_direction")
    if active_session and active_session.lower() in {"london", "london_killzone", "newyork", "newyork_killzone", "ny"}:
        score += 0.5
        reasons.append("manipulation_happened_during_active_session_expansion_window")

    if not accumulation["valid_accumulation"]:
        score = min(score, 6.0)
    if not mss["confirmed"]:
        score = min(score, 5.0)
    if not displacement["confirmed"] or not entry_zone:
        score = min(score, 6.0)
    if not targets["target_exists"]:
        score = min(score, 6.5)
    return round(max(0.0, min(10.0, score)), 2)


def _reasons_and_warnings(
    candidate: _JudasCandidate,
    accumulation: Mapping[str, Any],
    mss: Mapping[str, Any],
    displacement: Mapping[str, Any],
    entry_zone: Mapping[str, Any] | None,
    targets: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    reasons = [candidate.condition]
    warnings = ["Judas Swing is not an entry signal by itself"]
    if candidate.classification == JudasClassification.BEARISH_BREAKDOWN:
        warnings += ["price_accepted_below_range_not_bullish_judas", "possible_bearish_continuation_context"]
    elif candidate.classification == JudasClassification.BULLISH_BREAKOUT:
        warnings += ["price_accepted_above_range_not_bearish_judas", "possible_bullish_continuation_context"]
    elif candidate.classification in {JudasClassification.UNCLEAR_DOWNSIDE, JudasClassification.UNCLEAR_UPSIDE}:
        warnings.append("weak_or_unclear_judas_range_interaction")
    if not accumulation["valid_accumulation"]:
        warnings.append("accumulation_filter_not_fully_satisfied")
    if not mss["confirmed"]:
        warnings.append("wait_for_MSS_before_treating_as_valid_judas")
    if not displacement["confirmed"]:
        warnings.append("wait_for_displacement_before_entry_model")
    if not entry_zone:
        warnings.append("entry_requires_FVG_or_OB_retracement_context")
    if not targets["target_exists"]:
        warnings.append("target_liquidity_required_for_high_quality_judas")
    return reasons, warnings


def _final_judas_type(
    candidate: _JudasCandidate,
    mss_confirmed: bool,
    has_entry_zone: bool,
    quality_score: float,
) -> JudasType:
    if candidate.continuation_confirmed:
        return candidate.judas_type
    if candidate.reclaim_status == JudasReclaimStatus.UNCLEAR:
        return candidate.judas_type
    if not mss_confirmed:
        return candidate.judas_type
    if not has_entry_zone or quality_score < 7.0:
        return candidate.judas_type
    if candidate.direction == "bullish":
        return JudasType.BULLISH
    if candidate.direction == "bearish":
        return JudasType.BEARISH
    return candidate.judas_type


def _parse_session_range(session_range: Mapping[str, Any]) -> _SessionRange | None:
    nested = _get(session_range, "asian_range", {})
    range_high = _first_float(session_range, nested, "range_high", "asian_high", "high")
    range_low = _first_float(session_range, nested, "range_low", "asian_low", "low")
    if range_high is None or range_low is None:
        return None
    midpoint = _first_float(session_range, nested, "range_midpoint", "asian_midpoint", "midpoint")
    range_size = _first_float(session_range, nested, "range_size", "asian_range_size")
    return _SessionRange(
        session_name=str(_get(session_range, "session_name", "session_range")),
        range_high=range_high,
        range_low=range_low,
        range_midpoint=midpoint if midpoint is not None else (range_high + range_low) / 2.0,
        range_size=range_size if range_size is not None else max(0.0, range_high - range_low),
        session_start=_optional_str(_get(session_range, "session_start", None)),
        session_end=_optional_str(_get(session_range, "session_end", None)),
        timezone=str(_get(session_range, "timezone", "unknown")),
        quality_score=float(_get(session_range, "quality_score", _get(session_range, "range_quality_score", 5.0))),
        session_end_index=_optional_int(
            _get(session_range, "session_end_index", _get(session_range, "asian_session_end_index", None))
        ),
    )


def _post_session_candles(candles: Sequence[_Candle], session_range: _SessionRange) -> list[_Candle]:
    if session_range.session_end_index is not None:
        return [candle for candle in candles if candle.index > session_range.session_end_index]
    if session_range.session_end:
        end_time = _parse_clock(session_range.session_end)
        return [candle for candle in candles if candle.timestamp.time() > end_time]
    return list(candles)


def _session_candles(candles: Sequence[_Candle], session_range: _SessionRange) -> list[_Candle]:
    if session_range.session_start and session_range.session_end:
        start = _parse_clock(session_range.session_start)
        end = _parse_clock(session_range.session_end)
        return [candle for candle in candles if _inside_session(candle.timestamp.time(), start, end)]
    if session_range.session_end_index is not None:
        return [candle for candle in candles if candle.index <= session_range.session_end_index]
    return []


def _session_context(session_range: _SessionRange, accumulation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "session_name": session_range.session_name,
        "range_high": round(session_range.range_high, 5),
        "range_low": round(session_range.range_low, 5),
        "range_midpoint": round(session_range.range_midpoint, 5),
        "range_size": round(session_range.range_size, 5),
        "session_start": session_range.session_start,
        "session_end": session_range.session_end,
        "timezone": session_range.timezone,
        "range_quality_score": round(session_range.quality_score, 2),
        "accumulation": dict(accumulation),
    }


def _manipulation_payload(candidate: _JudasCandidate) -> dict[str, Any]:
    payload = {
        "manipulation_side": candidate.manipulation_side.value,
        "swept_liquidity": candidate.swept_liquidity.value,
        "sweep_index": candidate.sweep_candle.index,
        "sweep_timestamp": candidate.sweep_candle.timestamp.isoformat(),
        "sweep_level": round(candidate.sweep_level, 5),
        "sweep_confirmed": candidate.sweep_confirmed,
    }
    if candidate.manipulation_side == JudasManipulationSide.BELOW_RANGE:
        payload["sweep_low"] = round(candidate.sweep_extreme, 5)
    if candidate.manipulation_side == JudasManipulationSide.ABOVE_RANGE:
        payload["sweep_high"] = round(candidate.sweep_extreme, 5)
    return payload


def _stop_loss_reference(candidate: _JudasCandidate) -> str | None:
    if candidate.direction == "bullish":
        return "below_sweep_low_or_bullish_entry_zone_low"
    if candidate.direction == "bearish":
        return "above_sweep_high_or_bearish_entry_zone_high"
    return None


def _risk_note(candidate: _JudasCandidate) -> str:
    if candidate.direction == "bullish":
        return "Stop should be below manipulation low or below confirmed bullish entry zone"
    if candidate.direction == "bearish":
        return "Stop should be above manipulation high or above confirmed bearish entry zone"
    return "Invalid or unclear Judas candidate should not be used for execution"


def _quality_grade(score: float) -> JudasQualityGrade:
    if score < 2.5:
        return JudasQualityGrade.INVALID
    if score < 5.0:
        return JudasQualityGrade.WEAK
    if score < 7.0:
        return JudasQualityGrade.CANDIDATE
    if score < 9.0:
        return JudasQualityGrade.STRONG
    return JudasQualityGrade.HIGH_QUALITY


def _judas_id(candidate: _JudasCandidate, judas_type: JudasType) -> str:
    stamp = candidate.sweep_candle.timestamp.strftime("%Y%m%d_%H%M%S")
    return f"JUDAS_{judas_type.value.upper()}_{stamp}_{candidate.sweep_candle.index}"


def _empty_zone(zone_type: str | None = None) -> dict[str, Any]:
    return {"confirmed": False, "type": zone_type, "index": None, "zone_low": None, "zone_high": None}


def _empty_result(reason: str, symbol: str, timeframe: str | None, htf_bias: str) -> dict[str, Any]:
    return {
        "concept_name": "Judas Swing",
        "symbol": symbol,
        "timeframe": timeframe or "unknown",
        "judas_id": None,
        "judas_type": JudasType.NONE.value,
        "classification": JudasClassification.NONE.value,
        "session_context": {},
        "htf_bias": (htf_bias or "unknown").lower(),
        "manipulation_side": JudasManipulationSide.NONE.value,
        "swept_liquidity": JudasLiquiditySide.NONE.value,
        "sweep_index": None,
        "reclaim_status": JudasReclaimStatus.NONE.value,
        "mss_confirmed": False,
        "mss_direction": None,
        "entry_zone": None,
        "target_liquidity": None,
        "target_side": "unknown",
        "quality_score": 0.0,
        "quality_grade": JudasQualityGrade.INVALID.value,
        "reasons": [],
        "warnings": [reason],
    }


def _select_external_target(
    target_liquidity: Sequence[Mapping[str, Any] | Any] | None,
    target_side: str,
) -> dict[str, Any] | None:
    for item in target_liquidity or []:
        side = str(_get(item, "side", _get(item, "target_side", _get(item, "direction", "")))).lower()
        if side == target_side:
            return {
                "target_type": str(_get(item, "target_type", _get(item, "type", "external_liquidity"))),
                "price": _round_optional(_get(item, "price", None)),
                "side": target_side,
            }
    return None


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


def _first_float(primary: Mapping[str, Any], nested: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in primary:
            return _optional_float(primary[key])
        if key in nested:
            return _optional_float(nested[key])
    return None


def _get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _optional_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _round_optional(value: Any) -> float | None:
    parsed = _optional_float(value)
    return None if parsed is None else round(parsed, 5)


def _parse_clock(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _inside_session(candidate: time, start: time, end: time) -> bool:
    if start > end:
        return candidate >= start or candidate <= end
    return start <= candidate <= end
