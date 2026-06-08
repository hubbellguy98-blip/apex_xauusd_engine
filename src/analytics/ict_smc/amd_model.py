"""Power of Three / AMD detection for ICT/SMC session narrative analysis.

AMD is modeled as three required phases:
accumulation -> manipulation -> distribution.

This module is deterministic analytics only. It should not be wired directly to
execution without separate retest, risk, spread, and stop-quality controls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone as dt_timezone
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence

from src.core.domain.market_data import CandleNode


class AMDType(str, Enum):
    NONE = "none"
    BULLISH = "bullish_AMD"
    BEARISH = "bearish_AMD"
    BULLISH_CANDIDATE = "bullish_AMD_candidate"
    BEARISH_CANDIDATE = "bearish_AMD_candidate"
    INVALID_BULLISH = "invalid_bullish_AMD_candidate"
    INVALID_BEARISH = "invalid_bearish_AMD_candidate"


class AMDManipulationSide(str, Enum):
    NONE = "none"
    BELOW_RANGE = "below_range"
    ABOVE_RANGE = "above_range"


class AMDReclaimStatus(str, Enum):
    NONE = "none"
    RECLAIMED_BACK_INSIDE = "reclaimed_back_inside_range"
    REJECTED_BACK_INSIDE = "rejected_back_inside_range"
    ACCEPTED_BELOW = "accepted_below_range"
    ACCEPTED_ABOVE = "accepted_above_range"
    UNCLEAR = "unclear"


class AMDDistributionDirection(str, Enum):
    NONE = "unknown"
    BULLISH = "bullish"
    BEARISH = "bearish"


class AMDClassification(str, Enum):
    NONE = "no_valid_AMD"
    ACCUMULATION_ONLY = "accumulation_only"
    BULLISH_AMD = "bullish_AMD_model"
    BEARISH_AMD = "bearish_AMD_model"
    BULLISH_CANDIDATE = "bullish_AMD_candidate_without_distribution"
    BEARISH_CANDIDATE = "bearish_AMD_candidate_without_distribution"
    BULLISH_BREAKOUT = "bullish_breakout_continuation_not_bearish_AMD"
    BEARISH_BREAKDOWN = "bearish_breakdown_continuation_not_bullish_AMD"
    UNCLEAR = "unclear_manipulation"


class AMDConfidenceGrade(str, Enum):
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
class _AccumulationRange:
    session_name: str
    range_high: float
    range_low: float
    range_midpoint: float
    range_size: float
    start_index: int | None
    end_index: int | None
    session_start: str | None
    session_end: str | None
    timezone: str
    range_quality_score: float


@dataclass(frozen=True, slots=True)
class _AMDCandidate:
    amd_type: AMDType
    classification: AMDClassification
    manipulation_side: AMDManipulationSide
    swept_liquidity: str
    reclaim_status: AMDReclaimStatus
    distribution_direction: AMDDistributionDirection
    manipulation_candle: _Candle
    sweep_level: float
    sweep_extreme: float
    manipulation_confirmed: bool
    continuation_confirmed: bool
    condition: str


def detect_amd_model(
    df: Sequence[CandleNode | Mapping[str, Any]] | Any,
    sessions: Mapping[str, Any],
    htf_bias: str,
    *,
    sweep_buffer: float | None = None,
    close_buffer: float | None = None,
    atr_period: int = 14,
    buffer_atr_multiplier: float = 0.05,
    min_accumulation_range_atr: float = 1.0,
    max_accumulation_range_atr: float = 10.0,
    min_inside_percent: float = 70.0,
    minimum_range_quality: float = 5.0,
    mss_events: Sequence[Mapping[str, Any] | Any] | None = None,
    fvg_events: Sequence[Mapping[str, Any] | Any] | None = None,
    order_blocks: Sequence[Mapping[str, Any] | Any] | None = None,
    target_liquidity: Sequence[Mapping[str, Any] | Any] | None = None,
    active_session: str | None = None,
    symbol: str = "unknown",
    timeframe: str | None = None,
) -> dict[str, Any]:
    """Detect the best Accumulation-Manipulation-Distribution model."""
    candles = [candle for candle in _normalize_candles(df, timeframe, symbol) if candle.is_closed]
    accumulation_range = _parse_accumulation_range(sessions)
    if not candles or accumulation_range is None:
        return _empty_result("missing_closed_candles_or_accumulation_range", symbol, timeframe, htf_bias)

    atr = _calculate_atr(candles, atr_period)[-1]
    sweep = sweep_buffer if sweep_buffer is not None else max(atr * buffer_atr_multiplier, 0.00001)
    close = close_buffer if close_buffer is not None else sweep
    accumulation = _accumulation_context(
        candles,
        accumulation_range,
        atr,
        min_accumulation_range_atr,
        max_accumulation_range_atr,
        min_inside_percent,
        minimum_range_quality,
    )
    scan_candles = _post_accumulation_candles(candles, accumulation_range)
    if not scan_candles:
        result = _empty_result("accumulation_only_no_post_range_candles", candles[0].symbol, timeframe, htf_bias)
        result["accumulation_range"] = _accumulation_payload(accumulation_range, accumulation)
        result["classification"] = AMDClassification.ACCUMULATION_ONLY.value
        return result

    candidates = [
        candidate
        for candle in scan_candles
        for candidate in _candidate_from_candle(candle, accumulation_range, sweep, close)
    ]
    if not candidates:
        result = _empty_result("accumulation_only_no_liquidity_sweep_detected", candles[0].symbol, timeframe, htf_bias)
        result["accumulation_range"] = _accumulation_payload(accumulation_range, accumulation)
        result["classification"] = AMDClassification.ACCUMULATION_ONLY.value
        return result

    enriched = [
        _build_result(
            candidate,
            candles,
            accumulation_range,
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
    enriched.sort(key=lambda item: item["confidence_score"], reverse=True)
    best = enriched[0]
    best["candidate_count"] = len(enriched)
    best["alternative_candidates"] = [
        {
            "amd_type": item["amd_type"],
            "classification": item["classification"],
            "manipulation_index": item["manipulation"]["manipulation_index"],
            "confidence_score": item["confidence_score"],
        }
        for item in enriched[1:4]
    ]
    return best


def _candidate_from_candle(
    candle: _Candle,
    accumulation_range: _AccumulationRange,
    sweep_buffer: float,
    close_buffer: float,
) -> list[_AMDCandidate]:
    candidates: list[_AMDCandidate] = []
    if candle.low < accumulation_range.range_low - sweep_buffer:
        if candle.close > accumulation_range.range_low:
            candidates.append(
                _AMDCandidate(
                    AMDType.BULLISH_CANDIDATE,
                    AMDClassification.BULLISH_CANDIDATE,
                    AMDManipulationSide.BELOW_RANGE,
                    "sell_side",
                    AMDReclaimStatus.RECLAIMED_BACK_INSIDE,
                    AMDDistributionDirection.BULLISH,
                    candle,
                    accumulation_range.range_low,
                    candle.low,
                    True,
                    False,
                    "candle_low_below_accumulation_low_and_close_back_above_range_low",
                )
            )
        elif candle.close < accumulation_range.range_low - close_buffer and candle.bearish:
            candidates.append(
                _AMDCandidate(
                    AMDType.INVALID_BULLISH,
                    AMDClassification.BEARISH_BREAKDOWN,
                    AMDManipulationSide.BELOW_RANGE,
                    "sell_side",
                    AMDReclaimStatus.ACCEPTED_BELOW,
                    AMDDistributionDirection.BEARISH,
                    candle,
                    accumulation_range.range_low,
                    candle.low,
                    False,
                    True,
                    "price_accepted_below_accumulation_range",
                )
            )
        else:
            candidates.append(
                _unclear_candidate(
                    candle,
                    accumulation_range.range_low,
                    candle.low,
                    AMDManipulationSide.BELOW_RANGE,
                    "sell_side",
                )
            )
    if candle.high > accumulation_range.range_high + sweep_buffer:
        if candle.close < accumulation_range.range_high:
            candidates.append(
                _AMDCandidate(
                    AMDType.BEARISH_CANDIDATE,
                    AMDClassification.BEARISH_CANDIDATE,
                    AMDManipulationSide.ABOVE_RANGE,
                    "buy_side",
                    AMDReclaimStatus.REJECTED_BACK_INSIDE,
                    AMDDistributionDirection.BEARISH,
                    candle,
                    accumulation_range.range_high,
                    candle.high,
                    True,
                    False,
                    "candle_high_above_accumulation_high_and_close_back_below_range_high",
                )
            )
        elif candle.close > accumulation_range.range_high + close_buffer and candle.bullish:
            candidates.append(
                _AMDCandidate(
                    AMDType.INVALID_BEARISH,
                    AMDClassification.BULLISH_BREAKOUT,
                    AMDManipulationSide.ABOVE_RANGE,
                    "buy_side",
                    AMDReclaimStatus.ACCEPTED_ABOVE,
                    AMDDistributionDirection.BULLISH,
                    candle,
                    accumulation_range.range_high,
                    candle.high,
                    False,
                    True,
                    "price_accepted_above_accumulation_range",
                )
            )
        else:
            candidates.append(
                _unclear_candidate(
                    candle,
                    accumulation_range.range_high,
                    candle.high,
                    AMDManipulationSide.ABOVE_RANGE,
                    "buy_side",
                )
            )
    return candidates


def _build_result(
    candidate: _AMDCandidate,
    candles: Sequence[_Candle],
    accumulation_range: _AccumulationRange,
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
    targets = _targets(candidate, accumulation_range, target_liquidity)
    distribution = _distribution_context(candles, candidate, accumulation_range, mss, displacement, fvg)
    reasons, warnings = _reasons_and_warnings(candidate, accumulation, mss, displacement, entry_zone)
    confidence = _confidence_score(
        candidate,
        accumulation,
        mss,
        displacement,
        entry_zone,
        targets,
        distribution,
        htf_bias,
        active_session,
        reasons,
        warnings,
    )
    amd_type = _final_amd_type(candidate, accumulation, mss, displacement, entry_zone, confidence)
    amd_detected = amd_type in {AMDType.BULLISH, AMDType.BEARISH}
    if amd_detected:
        classification = (
            AMDClassification.BULLISH_AMD
            if amd_type == AMDType.BULLISH
            else AMDClassification.BEARISH_AMD
        )
    else:
        classification = candidate.classification

    return {
        "concept_name": "Power of Three / AMD",
        "symbol": candidate.manipulation_candle.symbol or symbol,
        "timeframe": timeframe or candidate.manipulation_candle.timeframe,
        "amd_id": _amd_id(candidate, amd_type),
        "amd_detected": amd_detected,
        "amd_type": amd_type.value,
        "classification": classification.value,
        "htf_bias": (htf_bias or "unknown").lower(),
        "accumulation_range": _accumulation_payload(accumulation_range, accumulation),
        "manipulation_side": candidate.manipulation_side.value,
        "swept_liquidity": candidate.swept_liquidity,
        "reclaim_status": candidate.reclaim_status.value,
        "distribution_direction": candidate.distribution_direction.value,
        "manipulation": _manipulation_payload(candidate),
        "distribution": distribution,
        "mss_confirmed": mss["confirmed"],
        "displacement_confirmed": displacement["confirmed"],
        "entry_zone": entry_zone,
        "target_liquidity": targets["target_liquidity"],
        "target_side": targets["target_side"],
        "targets": targets,
        "risk_logic": {
            "entry_allowed_from_AMD_alone": False,
            "entry_allowed_after_entry_zone_reaction": bool(amd_detected and entry_zone and targets["target_exists"]),
            "stop_loss_reference": _stop_loss_reference(candidate),
        },
        "confidence_score": confidence,
        "confidence_grade": _confidence_grade(confidence).value,
        "reasons": reasons,
        "warnings": warnings,
    }


def _accumulation_context(
    candles: Sequence[_Candle],
    accumulation_range: _AccumulationRange,
    atr: float,
    min_range_atr: float,
    max_range_atr: float,
    min_inside_percent: float,
    minimum_range_quality: float,
) -> dict[str, Any]:
    range_candles = _range_candles(candles, accumulation_range)
    inside = [
        candle
        for candle in range_candles
        if candle.high <= accumulation_range.range_high and candle.low >= accumulation_range.range_low
    ]
    inside_percent = (len(inside) / len(range_candles) * 100.0) if range_candles else 0.0
    range_atr_ratio = accumulation_range.range_size / atr if atr > 0 else None
    valid_size = (
        range_atr_ratio is None
        or min_range_atr <= range_atr_ratio <= max_range_atr
    )
    valid = bool(
        range_candles
        and inside_percent >= min_inside_percent
        and valid_size
        and accumulation_range.range_quality_score >= minimum_range_quality
    )
    warnings: list[str] = []
    if inside_percent < min_inside_percent:
        warnings.append("candles_inside_accumulation_range_below_threshold")
    if range_atr_ratio is not None and range_atr_ratio < min_range_atr:
        warnings.append("accumulation_range_too_small_relative_to_atr")
    if range_atr_ratio is not None and range_atr_ratio > max_range_atr:
        warnings.append("accumulation_range_too_large_relative_to_atr")
    if accumulation_range.range_quality_score < minimum_range_quality:
        warnings.append("accumulation_range_quality_below_minimum")
    return {
        "valid": valid,
        "candles_checked": len(range_candles),
        "candles_inside_range": len(inside),
        "candles_inside_percent": round(inside_percent, 2),
        "range_atr_ratio": _round_optional(range_atr_ratio),
        "warnings": warnings,
    }


def _find_mss_confirmation(
    candles: Sequence[_Candle],
    candidate: _AMDCandidate,
    mss_events: Sequence[Mapping[str, Any] | Any] | None,
) -> dict[str, Any]:
    direction = candidate.distribution_direction.value
    if direction == AMDDistributionDirection.NONE.value or candidate.continuation_confirmed:
        return {"confirmed": False, "direction": None, "index": None, "broken_level": None}
    if mss_events is not None:
        for item in mss_events:
            item_direction = str(_get(item, "direction", _get(item, "mss_direction", ""))).lower()
            item_index = int(_get(item, "index", _get(item, "confirmation_index", -1)))
            broken_level = _get(item, "broken_level", _get(item, "price", None))
            if item_direction == direction and item_index > candidate.manipulation_candle.index:
                return {
                    "confirmed": True,
                    "direction": direction,
                    "index": item_index,
                    "broken_level": _optional_float(broken_level),
                }
        return {"confirmed": False, "direction": direction, "index": None, "broken_level": None}

    later = [candle for candle in candles if candle.index > candidate.manipulation_candle.index]
    if len(later) < 3:
        return {"confirmed": False, "direction": direction, "index": None, "broken_level": None}
    if direction == "bullish":
        swing_high = max(candle.high for candle in later[:2])
        for candle in later[2:]:
            if candle.close > swing_high:
                return {"confirmed": True, "direction": direction, "index": candle.index, "broken_level": swing_high}
    if direction == "bearish":
        swing_low = min(candle.low for candle in later[:2])
        for candle in later[2:]:
            if candle.close < swing_low:
                return {"confirmed": True, "direction": direction, "index": candle.index, "broken_level": swing_low}
    return {"confirmed": False, "direction": direction, "index": None, "broken_level": None}


def _displacement_context(
    candles: Sequence[_Candle],
    candidate: _AMDCandidate,
    mss: Mapping[str, Any],
    atr: float,
) -> dict[str, Any]:
    direction = candidate.distribution_direction.value
    if direction == "unknown" or candidate.continuation_confirmed or atr <= 0:
        return {"confirmed": False, "strength": "none", "start_index": None, "end_index": None}
    strongest: tuple[float, _Candle] | None = None
    for candle in [item for item in candles if item.index > candidate.manipulation_candle.index][:8]:
        directional = (direction == "bullish" and candle.bullish) or (direction == "bearish" and candle.bearish)
        close_quality = candle.close_position >= 0.65 if direction == "bullish" else candle.close_position <= 0.35
        body_ratio = candle.body / candle.range if candle.range > 0 else 0.0
        if directional and close_quality and body_ratio >= 0.55:
            value = candle.range / atr
            if strongest is None or value > strongest[0]:
                strongest = (value, candle)
    if strongest is None:
        return {"confirmed": False, "strength": "none", "start_index": None, "end_index": None}
    strength = "strong" if strongest[0] >= 1.2 or mss["confirmed"] else "moderate"
    return {
        "confirmed": True,
        "strength": strength,
        "start_index": strongest[1].index,
        "end_index": strongest[1].index,
    }


def _find_fvg(
    candles: Sequence[_Candle],
    candidate: _AMDCandidate,
    fvg_events: Sequence[Mapping[str, Any] | Any] | None,
) -> dict[str, Any]:
    direction = candidate.distribution_direction.value
    if direction == "unknown" or candidate.continuation_confirmed:
        return _empty_zone("fvg")
    expected_type = "bullish_fvg" if direction == "bullish" else "bearish_fvg"
    if fvg_events is not None:
        for item in fvg_events:
            item_type = str(_get(item, "type", _get(item, "fvg_type", ""))).lower()
            item_direction = str(_get(item, "direction", "")).lower()
            item_index = int(_get(item, "index", _get(item, "creation_index", -1)))
            if item_index > candidate.manipulation_candle.index and (
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

    later = [candle for candle in candles if candle.index > candidate.manipulation_candle.index]
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
    candidate: _AMDCandidate,
    order_blocks: Sequence[Mapping[str, Any] | Any] | None,
) -> dict[str, Any]:
    direction = candidate.distribution_direction.value
    if direction == "unknown" or candidate.continuation_confirmed:
        return _empty_zone("order_block")
    expected_type = "bullish_order_block" if direction == "bullish" else "bearish_order_block"
    if order_blocks is not None:
        for item in order_blocks:
            item_type = str(_get(item, "type", _get(item, "order_block_type", ""))).lower()
            item_direction = str(_get(item, "direction", "")).lower()
            item_index = int(_get(item, "index", _get(item, "creation_index", -1)))
            if item_index > candidate.manipulation_candle.index and (
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


def _entry_zone(
    candidate: _AMDCandidate,
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
        "source_event": f"{candidate.distribution_direction.value}_MSS_after_AMD_manipulation",
        "invalidation_level": round(candidate.sweep_extreme, 5),
    }


def _distribution_context(
    candles: Sequence[_Candle],
    candidate: _AMDCandidate,
    accumulation_range: _AccumulationRange,
    mss: Mapping[str, Any],
    displacement: Mapping[str, Any],
    fvg: Mapping[str, Any],
) -> dict[str, Any]:
    direction = candidate.distribution_direction.value
    later = [candle for candle in candles if candle.index > candidate.manipulation_candle.index]
    if direction == "bullish":
        reached_midpoint = any(candle.high >= accumulation_range.range_midpoint for candle in later)
        reached_opposite_range = any(candle.high >= accumulation_range.range_high for candle in later)
        status = "expanding_toward_buy_side_liquidity" if reached_midpoint else "distribution_not_confirmed"
    elif direction == "bearish":
        reached_midpoint = any(candle.low <= accumulation_range.range_midpoint for candle in later)
        reached_opposite_range = any(candle.low <= accumulation_range.range_low for candle in later)
        status = "expanding_toward_sell_side_liquidity" if reached_midpoint else "distribution_not_confirmed"
    else:
        reached_midpoint = False
        reached_opposite_range = False
        status = "distribution_not_confirmed"
    return {
        "distribution_direction": direction,
        "mss_confirmed": mss["confirmed"],
        "mss_confirmation_index": mss["index"],
        "broken_level": _round_optional(mss["broken_level"]),
        "displacement_confirmed": displacement["confirmed"],
        "displacement_strength": displacement["strength"],
        "fvg_created": fvg["confirmed"],
        "reached_accumulation_midpoint": reached_midpoint,
        "reached_opposite_range_liquidity": reached_opposite_range,
        "distribution_status": status,
    }


def _targets(
    candidate: _AMDCandidate,
    accumulation_range: _AccumulationRange,
    target_liquidity: Sequence[Mapping[str, Any] | Any] | None,
) -> dict[str, Any]:
    direction = candidate.distribution_direction.value
    if direction == "bullish":
        target_side = "buy_side"
        second_type = "accumulation_high"
        second_price = accumulation_range.range_high
        final_type = "PDH_or_external_buy_side_liquidity"
    elif direction == "bearish":
        target_side = "sell_side"
        second_type = "accumulation_low"
        second_price = accumulation_range.range_low
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
        "first_target": {
            "target_type": "accumulation_midpoint",
            "price": round(accumulation_range.range_midpoint, 5),
        },
        "second_target": {"target_type": second_type, "price": round(second_price, 5)},
        "final_target": final,
    }


def _confidence_score(
    candidate: _AMDCandidate,
    accumulation: Mapping[str, Any],
    mss: Mapping[str, Any],
    displacement: Mapping[str, Any],
    entry_zone: Mapping[str, Any] | None,
    targets: Mapping[str, Any],
    distribution: Mapping[str, Any],
    htf_bias: str,
    active_session: str | None,
    reasons: list[str],
    warnings: list[str],
) -> float:
    if candidate.continuation_confirmed:
        return 2.6
    if candidate.reclaim_status == AMDReclaimStatus.UNCLEAR:
        return 3.0
    score = 1.0
    if accumulation["valid"]:
        score += 2.0
        reasons.append("valid_accumulation_range_detected")
    else:
        warnings.extend(accumulation["warnings"])
    if candidate.manipulation_confirmed:
        score += 1.5
        reasons.append(f"manipulation_swept_{candidate.swept_liquidity}_liquidity")
    if mss["confirmed"]:
        score += 2.0
        reasons.append("MSS_confirmed_after_manipulation")
    else:
        warnings.append("distribution_missing_MSS_confirmation")
    if displacement["confirmed"]:
        score += 1.0 if displacement["strength"] == "strong" else 0.5
        reasons.append(f"{displacement['strength']}_distribution_displacement_detected")
    else:
        warnings.append("distribution_missing_displacement")
    if entry_zone:
        score += 1.0
        reasons.append("FVG_or_OB_entry_zone_available_after_distribution_confirmation")
    else:
        warnings.append("AMD_entry_zone_missing")
    if distribution["reached_accumulation_midpoint"]:
        score += 0.5
        reasons.append("distribution_reached_accumulation_midpoint")
    if distribution["reached_opposite_range_liquidity"]:
        score += 0.5
        reasons.append("distribution_reached_opposite_range_liquidity")
    if targets["target_exists"]:
        score += 0.5
        reasons.append("opposite_side_target_liquidity_available")
    bias = (htf_bias or "unknown").lower()
    direction = candidate.distribution_direction.value
    if bias == direction:
        score += 0.75
        reasons.append("HTF_bias_aligns_with_distribution_direction")
    elif bias in {"bullish", "bearish"} and bias != direction:
        score -= 1.0
        warnings.append("HTF_bias_conflicts_with_distribution_direction")
    if active_session and active_session.lower() in {"london", "london_killzone", "newyork", "newyork_killzone", "ny"}:
        score += 0.5
        reasons.append("manipulation_distribution_occurred_in_active_session_window")

    if not accumulation["valid"]:
        score = min(score, 6.0)
    if not mss["confirmed"]:
        score = min(score, 5.0)
    if not displacement["confirmed"] or not entry_zone:
        score = min(score, 6.0)
    return round(max(0.0, min(10.0, score)), 2)


def _reasons_and_warnings(
    candidate: _AMDCandidate,
    accumulation: Mapping[str, Any],
    mss: Mapping[str, Any],
    displacement: Mapping[str, Any],
    entry_zone: Mapping[str, Any] | None,
) -> tuple[list[str], list[str]]:
    reasons = [candidate.condition]
    warnings = ["AMD is not an entry signal by itself", "Do not force AMD every day"]
    if candidate.classification == AMDClassification.BULLISH_BREAKOUT:
        warnings.append("price_accepted_above_range_not_bearish_AMD")
    if candidate.classification == AMDClassification.BEARISH_BREAKDOWN:
        warnings.append("price_accepted_below_range_not_bullish_AMD")
    if candidate.classification == AMDClassification.UNCLEAR:
        warnings.append("unclear_manipulation_not_confirmed_AMD")
    if not accumulation["valid"]:
        warnings.append("accumulation_phase_not_fully_valid")
    if not mss["confirmed"]:
        warnings.append("MSS_required_for_confirmed_distribution")
    if not displacement["confirmed"]:
        warnings.append("displacement_required_for_confirmed_distribution")
    if not entry_zone:
        warnings.append("FVG_or_OB_entry_context_required_for_high_quality_AMD")
    return reasons, warnings


def _final_amd_type(
    candidate: _AMDCandidate,
    accumulation: Mapping[str, Any],
    mss: Mapping[str, Any],
    displacement: Mapping[str, Any],
    entry_zone: Mapping[str, Any] | None,
    confidence_score: float,
) -> AMDType:
    if candidate.continuation_confirmed or candidate.reclaim_status == AMDReclaimStatus.UNCLEAR:
        return candidate.amd_type
    if not accumulation["valid"] or not mss["confirmed"]:
        return candidate.amd_type
    if not displacement["confirmed"] or not entry_zone or confidence_score < 7.0:
        return candidate.amd_type
    if candidate.distribution_direction == AMDDistributionDirection.BULLISH:
        return AMDType.BULLISH
    if candidate.distribution_direction == AMDDistributionDirection.BEARISH:
        return AMDType.BEARISH
    return candidate.amd_type


def _parse_accumulation_range(sessions: Mapping[str, Any]) -> _AccumulationRange | None:
    source = _get(sessions, "accumulation_range", None)
    if source is None:
        source = _get(sessions, "asian_range", None)
    if source is None and ("range_high" in sessions or "asian_high" in sessions):
        source = sessions
    if source is None:
        source = _get(sessions, "asian", None)
    if source is None:
        return None
    nested = _get(source, "asian_range", {})
    range_high = _first_float(source, nested, "range_high", "asian_high", "high")
    range_low = _first_float(source, nested, "range_low", "asian_low", "low")
    if range_high is None or range_low is None:
        return None
    midpoint = _first_float(source, nested, "range_midpoint", "asian_midpoint", "midpoint")
    range_size = _first_float(source, nested, "range_size", "asian_range_size")
    return _AccumulationRange(
        session_name=str(_get(source, "session_name", "accumulation_range")),
        range_high=range_high,
        range_low=range_low,
        range_midpoint=midpoint if midpoint is not None else (range_high + range_low) / 2.0,
        range_size=range_size if range_size is not None else max(0.0, range_high - range_low),
        start_index=_optional_int(_get(source, "start_index", None)),
        end_index=_optional_int(_get(source, "end_index", _get(source, "session_end_index", None))),
        session_start=_optional_str(_get(source, "session_start", _get(source, "start", None))),
        session_end=_optional_str(_get(source, "session_end", _get(source, "end", None))),
        timezone=str(_get(source, "timezone", "unknown")),
        range_quality_score=float(_get(source, "quality_score", _get(source, "range_quality_score", 5.0))),
    )


def _range_candles(candles: Sequence[_Candle], accumulation_range: _AccumulationRange) -> list[_Candle]:
    if accumulation_range.start_index is not None and accumulation_range.end_index is not None:
        return [
            candle
            for candle in candles
            if accumulation_range.start_index <= candle.index <= accumulation_range.end_index
        ]
    if accumulation_range.session_start and accumulation_range.session_end:
        start = _parse_clock(accumulation_range.session_start)
        end = _parse_clock(accumulation_range.session_end)
        return [candle for candle in candles if _inside_session(candle.timestamp.time(), start, end)]
    if accumulation_range.end_index is not None:
        return [candle for candle in candles if candle.index <= accumulation_range.end_index]
    return []


def _post_accumulation_candles(candles: Sequence[_Candle], accumulation_range: _AccumulationRange) -> list[_Candle]:
    if accumulation_range.end_index is not None:
        return [candle for candle in candles if candle.index > accumulation_range.end_index]
    if accumulation_range.session_end:
        end = _parse_clock(accumulation_range.session_end)
        return [candle for candle in candles if candle.timestamp.time() > end]
    return list(candles)


def _accumulation_payload(
    accumulation_range: _AccumulationRange,
    accumulation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "session_name": accumulation_range.session_name,
        "range_high": round(accumulation_range.range_high, 5),
        "range_low": round(accumulation_range.range_low, 5),
        "range_midpoint": round(accumulation_range.range_midpoint, 5),
        "range_size": round(accumulation_range.range_size, 5),
        "start_index": accumulation_range.start_index,
        "end_index": accumulation_range.end_index,
        "session_start": accumulation_range.session_start,
        "session_end": accumulation_range.session_end,
        "timezone": accumulation_range.timezone,
        "candles_inside_percent": accumulation.get("candles_inside_percent", 0.0),
        "range_quality_score": round(accumulation_range.range_quality_score, 2),
        "valid_accumulation": accumulation.get("valid", False),
    }


def _manipulation_payload(candidate: _AMDCandidate) -> dict[str, Any]:
    return {
        "manipulation_side": candidate.manipulation_side.value,
        "swept_liquidity": candidate.swept_liquidity,
        "sweep_level": round(candidate.sweep_level, 5),
        "sweep_extreme": round(candidate.sweep_extreme, 5),
        "manipulation_index": candidate.manipulation_candle.index,
        "manipulation_timestamp": candidate.manipulation_candle.timestamp.isoformat(),
        "reclaim_status": candidate.reclaim_status.value,
        "reclaim_close": round(candidate.manipulation_candle.close, 5),
    }


def _unclear_candidate(
    candle: _Candle,
    level: float,
    extreme: float,
    side: AMDManipulationSide,
    liquidity: str,
) -> _AMDCandidate:
    return _AMDCandidate(
        AMDType.NONE,
        AMDClassification.UNCLEAR,
        side,
        liquidity,
        AMDReclaimStatus.UNCLEAR,
        AMDDistributionDirection.NONE,
        candle,
        level,
        extreme,
        False,
        False,
        "wick_beyond_accumulation_range_without_clear_reclaim_or_acceptance",
    )


def _stop_loss_reference(candidate: _AMDCandidate) -> str | None:
    if candidate.distribution_direction == AMDDistributionDirection.BULLISH:
        return "below_manipulation_low_or_bullish_entry_zone_low"
    if candidate.distribution_direction == AMDDistributionDirection.BEARISH:
        return "above_manipulation_high_or_bearish_entry_zone_high"
    return None


def _confidence_grade(score: float) -> AMDConfidenceGrade:
    if score < 2.5:
        return AMDConfidenceGrade.INVALID
    if score < 5.0:
        return AMDConfidenceGrade.WEAK
    if score < 7.0:
        return AMDConfidenceGrade.CANDIDATE
    if score < 9.0:
        return AMDConfidenceGrade.STRONG
    return AMDConfidenceGrade.HIGH_QUALITY


def _amd_id(candidate: _AMDCandidate, amd_type: AMDType) -> str:
    stamp = candidate.manipulation_candle.timestamp.strftime("%Y%m%d_%H%M%S")
    return f"AMD_{amd_type.value.upper()}_{stamp}_{candidate.manipulation_candle.index}"


def _empty_zone(zone_type: str | None = None) -> dict[str, Any]:
    return {"confirmed": False, "type": zone_type, "index": None, "zone_low": None, "zone_high": None}


def _empty_result(reason: str, symbol: str, timeframe: str | None, htf_bias: str) -> dict[str, Any]:
    return {
        "concept_name": "Power of Three / AMD",
        "symbol": symbol,
        "timeframe": timeframe or "unknown",
        "amd_id": None,
        "amd_detected": False,
        "amd_type": AMDType.NONE.value,
        "classification": AMDClassification.NONE.value,
        "htf_bias": (htf_bias or "unknown").lower(),
        "accumulation_range": {},
        "manipulation_side": AMDManipulationSide.NONE.value,
        "swept_liquidity": None,
        "reclaim_status": AMDReclaimStatus.NONE.value,
        "distribution_direction": AMDDistributionDirection.NONE.value,
        "mss_confirmed": False,
        "displacement_confirmed": False,
        "entry_zone": None,
        "target_liquidity": None,
        "target_side": "unknown",
        "confidence_score": 0.0,
        "confidence_grade": AMDConfidenceGrade.INVALID.value,
        "reasons": [],
        "warnings": [reason, "Do not force AMD every day"],
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
