"""Rule-based ICT/SMC imbalance detection.

Imbalance is broader than FVG: every valid FVG is an imbalance, but not every
imbalance is a strict three-candle FVG. This detector keeps those meanings
separate and treats imbalance as a point-of-interest zone, not an entry signal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from src.core.domain.market_data import CandleNode


class ImbalanceType(str, Enum):
    BULLISH_FVG = "bullish_fvg_imbalance"
    BEARISH_FVG = "bearish_fvg_imbalance"
    BULLISH_DISPLACEMENT = "bullish_displacement_imbalance"
    BEARISH_DISPLACEMENT = "bearish_displacement_imbalance"
    BULLISH_MULTI_CANDLE = "bullish_multi_candle_imbalance"
    BEARISH_MULTI_CANDLE = "bearish_multi_candle_imbalance"


class ImbalanceDetectionMethod(str, Enum):
    FVG_THREE_CANDLE = "fvg_three_candle"
    DISPLACEMENT_CANDLE = "displacement_candle"
    MULTI_CANDLE_DISPLACEMENT = "multi_candle_displacement"


class ImbalanceActiveStatus(str, Enum):
    UNFILLED = "unfilled"
    PARTIALLY_FILLED = "partially_filled"
    HALF_FILLED = "half_filled"
    FULLY_FILLED = "fully_filled"
    RESPECTED = "respected"
    INVALIDATED = "invalidated"
    STALE = "stale"


class ImbalanceDisplacementStrength(str, Enum):
    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    VERY_STRONG = "very_strong"


class ImbalanceQualityGrade(str, Enum):
    INVALID = "invalid"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True, slots=True)
class ImbalanceZone:
    concept_name: str
    symbol: str
    timeframe: str
    imbalance_id: str
    imbalance_type: ImbalanceType
    direction: str
    detection_method: ImbalanceDetectionMethod
    zone_low: float
    zone_high: float
    zone_mid: float
    creation_index: int
    creation_timestamp: datetime
    displacement_candle_index: int
    displacement_strength: ImbalanceDisplacementStrength
    filled_percent: float
    active_status: ImbalanceActiveStatus
    invalidated_at_index: int | None
    respected: bool
    quality_score: float
    quality_grade: ImbalanceQualityGrade
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    body_to_range_ratio: float
    range_to_atr_ratio: float
    close_position: str
    zone_size: float
    zone_size_atr: float
    lowest_low_after_creation: float | None
    highest_high_after_creation: float | None
    filled_at_index: int | None
    invalidated: bool
    created_after_liquidity_sweep: bool
    sweep_type: str
    created_after_structure_event: bool
    structure_event_type: str
    ob_overlap: bool
    fvg_confluence: bool
    premium_discount_alignment: bool
    htf_alignment: str
    target_liquidity_reference: str
    entry_allowed_from_imbalance_alone: bool
    entry_allowed_after_reaction: bool
    recommended_entry_style: str
    stop_loss_reference: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["imbalance_type"] = self.imbalance_type.value
        payload["detection_method"] = self.detection_method.value
        payload["displacement_strength"] = self.displacement_strength.value
        payload["active_status"] = self.active_status.value
        payload["quality_grade"] = self.quality_grade.value
        return payload


def detect_imbalances(
    df: Sequence[CandleNode | Mapping[str, Any]] | Any,
    structure_events: Sequence[Mapping[str, Any] | str] | None = None,
    liquidity_sweeps: Sequence[Mapping[str, Any] | str] | None = None,
    order_blocks: Sequence[Mapping[str, Any]] | None = None,
    fvg_events: Sequence[Mapping[str, Any]] | None = None,
    poi_zones: Sequence[Mapping[str, Any]] | None = None,
    context: Mapping[str, Any] | None = None,
    *,
    symbol: str = "unknown",
    timeframe: str = "unknown",
    atr_period: int = 14,
    displacement_body_threshold: float = 0.60,
    displacement_atr_threshold: float = 1.25,
    close_position_threshold: float = 0.70,
    invalidation_buffer: float = 0.05,
    stale_after_bars: int = 120,
    detect_displacement: bool = True,
    detect_multi_candle: bool = True,
) -> list[dict[str, Any]]:
    """Detect FVG, displacement-candle, and conservative multi-candle imbalances."""
    candles = _normalize_candles(df)
    if len(candles) < 3:
        return []

    atr_values = _atr_values(candles, atr_period)
    structures = [event for event in (_structure_event(item) for item in (structure_events or ())) if event is not None]
    sweeps = [event for event in (_liquidity_event(item) for item in (liquidity_sweeps or ())) if event is not None]
    zones = [zone for zone in (_zone_event(item) for item in (*(order_blocks or ()), *(poi_zones or ()), *(fvg_events or ()))) if zone is not None]
    fvg_zones = [zone for zone in (_zone_event(item) for item in (fvg_events or ())) if zone is not None]
    active_context = dict(context or {})

    imbalances: list[ImbalanceZone] = []
    fvg_displacement_keys: set[tuple[str, int]] = set()

    for position in range(2, len(candles)):
        c1, c2, c3 = candles[position - 2], candles[position - 1], candles[position]
        if c1["high"] < c3["low"]:
            imbalances.append(
                _build_zone(
                    candles, atr_values, position, ImbalanceType.BULLISH_FVG,
                    ImbalanceDetectionMethod.FVG_THREE_CANDLE, "bullish", c1["high"], c3["low"], c2["position"],
                    structures, sweeps, zones, fvg_zones, active_context, symbol, timeframe,
                    displacement_body_threshold, displacement_atr_threshold, close_position_threshold,
                    invalidation_buffer, stale_after_bars,
                )
            )
            fvg_displacement_keys.add(("bullish", c2["position"]))
        if c1["low"] > c3["high"]:
            imbalances.append(
                _build_zone(
                    candles, atr_values, position, ImbalanceType.BEARISH_FVG,
                    ImbalanceDetectionMethod.FVG_THREE_CANDLE, "bearish", c3["high"], c1["low"], c2["position"],
                    structures, sweeps, zones, fvg_zones, active_context, symbol, timeframe,
                    displacement_body_threshold, displacement_atr_threshold, close_position_threshold,
                    invalidation_buffer, stale_after_bars,
                )
            )
            fvg_displacement_keys.add(("bearish", c2["position"]))

    if detect_displacement:
        imbalances.extend(
            _detect_displacement_zones(
                candles, atr_values, fvg_displacement_keys, structures, sweeps, zones, fvg_zones, active_context,
                symbol, timeframe, displacement_body_threshold, displacement_atr_threshold,
                close_position_threshold, invalidation_buffer, stale_after_bars,
            )
        )
    if detect_multi_candle:
        imbalances.extend(
            _detect_multi_candle_zones(
                candles, atr_values, structures, sweeps, zones, fvg_zones, active_context,
                symbol, timeframe, displacement_body_threshold, displacement_atr_threshold,
                close_position_threshold, invalidation_buffer, stale_after_bars,
            )
        )

    return [
        item.as_dict()
        for item in sorted(
            imbalances,
            key=lambda zone: (not zone.invalidated, zone.quality_score, zone.creation_index),
            reverse=True,
        )
    ]


def _detect_displacement_zones(
    candles: Sequence[dict[str, Any]],
    atr_values: Sequence[float],
    fvg_displacement_keys: set[tuple[str, int]],
    structures: Sequence[Mapping[str, Any]],
    sweeps: Sequence[Mapping[str, Any]],
    zones: Sequence[Mapping[str, Any]],
    fvg_zones: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    symbol: str,
    timeframe: str,
    body_threshold: float,
    atr_threshold: float,
    close_threshold: float,
    invalidation_buffer: float,
    stale_after_bars: int,
) -> list[ImbalanceZone]:
    results: list[ImbalanceZone] = []
    for position, candle in enumerate(candles[1:], start=1):
        for direction, zone_type in (
            ("bullish", ImbalanceType.BULLISH_DISPLACEMENT),
            ("bearish", ImbalanceType.BEARISH_DISPLACEMENT),
        ):
            if (direction, position) in fvg_displacement_keys:
                continue
            snapshot = _displacement_snapshot(candle, atr_values[position], direction, body_threshold, atr_threshold, close_threshold)
            if snapshot["strength"] not in {ImbalanceDisplacementStrength.MODERATE, ImbalanceDisplacementStrength.STRONG, ImbalanceDisplacementStrength.VERY_STRONG}:
                continue
            low, high = (candle["open"], candle["close"]) if direction == "bullish" else (candle["close"], candle["open"])
            results.append(
                _build_zone(
                    candles, atr_values, position, zone_type, ImbalanceDetectionMethod.DISPLACEMENT_CANDLE, direction,
                    min(low, high), max(low, high), position, structures, sweeps, zones, fvg_zones, context, symbol,
                    timeframe, body_threshold, atr_threshold, close_threshold, invalidation_buffer, stale_after_bars,
                )
            )
    return results


def _detect_multi_candle_zones(
    candles: Sequence[dict[str, Any]],
    atr_values: Sequence[float],
    structures: Sequence[Mapping[str, Any]],
    sweeps: Sequence[Mapping[str, Any]],
    zones: Sequence[Mapping[str, Any]],
    fvg_zones: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    symbol: str,
    timeframe: str,
    body_threshold: float,
    atr_threshold: float,
    close_threshold: float,
    invalidation_buffer: float,
    stale_after_bars: int,
) -> list[ImbalanceZone]:
    results: list[ImbalanceZone] = []
    for start in range(max(0, len(candles) - 5)):
        for length in range(2, min(5, len(candles) - start) + 1):
            window = candles[start : start + length]
            end = start + length - 1
            direction = _sequence_direction(window)
            if direction is None:
                continue
            move = abs(window[-1]["close"] - window[0]["open"])
            if move < atr_values[end] * atr_threshold * 1.4 or not _close_progresses(window, direction):
                continue
            has_structure, _ = _has_matching_structure(structures, direction, int(candles[end]["index"]))
            has_sweep, _ = _has_matching_sweep(sweeps, direction, int(candles[end]["index"]))
            low = min(min(c["open"], c["close"]) for c in window)
            high = max(max(c["open"], c["close"]) for c in window)
            if not (has_structure or has_sweep or _has_zone_overlap(fvg_zones, direction, low, high)):
                continue
            zone_type = ImbalanceType.BULLISH_MULTI_CANDLE if direction == "bullish" else ImbalanceType.BEARISH_MULTI_CANDLE
            results.append(
                _build_zone(
                    candles, atr_values, end, zone_type, ImbalanceDetectionMethod.MULTI_CANDLE_DISPLACEMENT, direction,
                    low, high, end, structures, sweeps, zones, fvg_zones, context, symbol, timeframe,
                    body_threshold, atr_threshold, close_threshold, invalidation_buffer, stale_after_bars,
                )
            )
    return results


def _build_zone(
    candles: Sequence[dict[str, Any]],
    atr_values: Sequence[float],
    creation_position: int,
    zone_type: ImbalanceType,
    method: ImbalanceDetectionMethod,
    direction: str,
    zone_low: float,
    zone_high: float,
    displacement_position: int,
    structures: Sequence[Mapping[str, Any]],
    sweeps: Sequence[Mapping[str, Any]],
    zones: Sequence[Mapping[str, Any]],
    fvg_zones: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    symbol: str,
    timeframe: str,
    body_threshold: float,
    atr_threshold: float,
    close_threshold: float,
    invalidation_buffer: float,
    stale_after_bars: int,
) -> ImbalanceZone:
    creation = candles[creation_position]
    displacement_candle = candles[displacement_position]
    zone_low, zone_high = min(float(zone_low), float(zone_high)), max(float(zone_low), float(zone_high))
    zone_size = max(zone_high - zone_low, 1e-9)
    atr = max(atr_values[displacement_position], 1e-9)
    displacement = _displacement_snapshot(displacement_candle, atr, direction, body_threshold, atr_threshold, close_threshold)
    fill = _track_fill(candles, creation_position, direction, zone_low, zone_high, invalidation_buffer, stale_after_bars)
    has_structure, structure_type = _has_matching_structure(structures, direction, int(creation["index"]))
    has_sweep, sweep_type = _has_matching_sweep(sweeps, direction, int(creation["index"]))
    ob_overlap = _has_zone_overlap(zones, direction, zone_low, zone_high)
    fvg_confluence = method == ImbalanceDetectionMethod.FVG_THREE_CANDLE or _has_zone_overlap(fvg_zones, direction, zone_low, zone_high)
    pd_alignment = _premium_discount_alignment(context, direction)
    htf_alignment = _htf_alignment(context, direction)
    target = _target_reference(context, direction)
    score, reasons, warnings = _quality_score(
        zone_type, method, displacement, zone_size / atr, fill, has_sweep, has_structure,
        ob_overlap, fvg_confluence, pd_alignment, htf_alignment, target,
    )
    return ImbalanceZone(
        concept_name="ict_smc_imbalance",
        symbol=symbol,
        timeframe=timeframe,
        imbalance_id=f"IMB_{direction}_{method.value}_{int(creation['index'])}_{round(zone_low, 5)}_{round(zone_high, 5)}",
        imbalance_type=zone_type,
        direction=direction,
        detection_method=method,
        zone_low=round(zone_low, 5),
        zone_high=round(zone_high, 5),
        zone_mid=round((zone_low + zone_high) / 2.0, 5),
        creation_index=int(creation["index"]),
        creation_timestamp=creation["timestamp"],
        displacement_candle_index=int(displacement_candle["index"]),
        displacement_strength=displacement["strength"],
        filled_percent=fill["filled_percent"],
        active_status=fill["active_status"],
        invalidated_at_index=fill["invalidated_at_index"],
        respected=fill["reaction_confirmed"],
        quality_score=score,
        quality_grade=_quality_grade(score),
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        body_to_range_ratio=displacement["body_to_range_ratio"],
        range_to_atr_ratio=displacement["range_to_atr_ratio"],
        close_position=displacement["close_position"],
        zone_size=round(zone_size, 5),
        zone_size_atr=round(zone_size / atr, 3),
        lowest_low_after_creation=fill["lowest_low_after_creation"],
        highest_high_after_creation=fill["highest_high_after_creation"],
        filled_at_index=fill["filled_at_index"],
        invalidated=fill["invalidated"],
        created_after_liquidity_sweep=has_sweep,
        sweep_type=sweep_type,
        created_after_structure_event=has_structure,
        structure_event_type=structure_type,
        ob_overlap=ob_overlap,
        fvg_confluence=fvg_confluence,
        premium_discount_alignment=pd_alignment,
        htf_alignment=htf_alignment,
        target_liquidity_reference=target,
        entry_allowed_from_imbalance_alone=False,
        entry_allowed_after_reaction=fill["active_status"] == ImbalanceActiveStatus.RESPECTED and score >= 7.0,
        recommended_entry_style="wait_for_retest_reaction_structure_and_risk_confirmation",
        stop_loss_reference="outside_imbalance_extreme_after_reaction" if fill["active_status"] == ImbalanceActiveStatus.RESPECTED else "not_actionable_until_reaction",
    )


def _track_fill(
    candles: Sequence[dict[str, Any]],
    creation_position: int,
    direction: str,
    zone_low: float,
    zone_high: float,
    invalidation_buffer: float,
    stale_after_bars: int,
) -> dict[str, Any]:
    future = candles[creation_position + 1 :]
    if not future:
        return _fill_payload(0.0, ImbalanceActiveStatus.UNFILLED, None, None, None, False, None, False)

    zone_size = max(zone_high - zone_low, 1e-9)
    filled_percent = 0.0
    filled_at_index: int | None = None
    invalidated = False
    invalidated_at_index: int | None = None
    reaction = False
    lowest_low = min(candle["low"] for candle in future)
    highest_high = max(candle["high"] for candle in future)

    for candle in future:
        if direction == "bullish":
            if candle["low"] <= zone_high:
                filled_at_index = filled_at_index or int(candle["index"])
                filled_percent = max(filled_percent, _clamp(((zone_high - candle["low"]) / zone_size) * 100.0, 0.0, 100.0))
                reaction = reaction or (candle["close"] > zone_low and candle["close"] > candle["open"] and candle["close"] >= (zone_low + zone_high) / 2.0)
            if candle["close"] < zone_low - invalidation_buffer:
                invalidated, invalidated_at_index, filled_percent = True, int(candle["index"]), 100.0
                break
        else:
            if candle["high"] >= zone_low:
                filled_at_index = filled_at_index or int(candle["index"])
                filled_percent = max(filled_percent, _clamp(((candle["high"] - zone_low) / zone_size) * 100.0, 0.0, 100.0))
                reaction = reaction or (candle["close"] < zone_high and candle["close"] < candle["open"] and candle["close"] <= (zone_low + zone_high) / 2.0)
            if candle["close"] > zone_high + invalidation_buffer:
                invalidated, invalidated_at_index, filled_percent = True, int(candle["index"]), 100.0
                break

    if invalidated:
        status = ImbalanceActiveStatus.INVALIDATED
    elif reaction:
        status = ImbalanceActiveStatus.RESPECTED
    elif filled_percent >= 100.0:
        status = ImbalanceActiveStatus.FULLY_FILLED
    elif filled_percent >= 50.0:
        status = ImbalanceActiveStatus.HALF_FILLED
    elif filled_percent > 0.0:
        status = ImbalanceActiveStatus.PARTIALLY_FILLED
    elif len(future) > stale_after_bars:
        status = ImbalanceActiveStatus.STALE
    else:
        status = ImbalanceActiveStatus.UNFILLED
    return _fill_payload(filled_percent, status, lowest_low, highest_high, filled_at_index, invalidated, invalidated_at_index, reaction)


def _fill_payload(
    filled_percent: float,
    status: ImbalanceActiveStatus,
    lowest_low: float | None,
    highest_high: float | None,
    filled_at_index: int | None,
    invalidated: bool,
    invalidated_at_index: int | None,
    reaction: bool,
) -> dict[str, Any]:
    return {
        "filled_percent": round(filled_percent, 2),
        "active_status": status,
        "lowest_low_after_creation": round(float(lowest_low), 5) if lowest_low is not None else None,
        "highest_high_after_creation": round(float(highest_high), 5) if highest_high is not None else None,
        "filled_at_index": filled_at_index,
        "invalidated": invalidated,
        "invalidated_at_index": invalidated_at_index,
        "reaction_confirmed": reaction,
    }


def _displacement_snapshot(
    candle: Mapping[str, Any],
    atr: float,
    direction: str,
    body_threshold: float,
    atr_threshold: float,
    close_threshold: float,
) -> dict[str, Any]:
    candle_range = max(float(candle["high"]) - float(candle["low"]), 1e-9)
    body_ratio = abs(float(candle["close"]) - float(candle["open"])) / candle_range
    range_to_atr = candle_range / max(float(atr), 1e-9)
    if direction == "bullish":
        directional = candle["close"] > candle["open"]
        close_ratio = (float(candle["close"]) - float(candle["low"])) / candle_range
        close_position = "near_high" if close_ratio >= close_threshold else "middle_or_weak"
    else:
        directional = candle["close"] < candle["open"]
        close_ratio = (float(candle["high"]) - float(candle["close"])) / candle_range
        close_position = "near_low" if close_ratio >= close_threshold else "middle_or_weak"
    if directional and body_ratio >= body_threshold * 1.15 and range_to_atr >= atr_threshold * 1.8 and close_ratio >= close_threshold:
        strength = ImbalanceDisplacementStrength.VERY_STRONG
    elif directional and body_ratio >= body_threshold and range_to_atr >= atr_threshold * 1.5 and close_ratio >= close_threshold:
        strength = ImbalanceDisplacementStrength.STRONG
    elif directional and body_ratio >= body_threshold and range_to_atr >= atr_threshold and close_ratio >= close_threshold:
        strength = ImbalanceDisplacementStrength.MODERATE
    elif directional and body_ratio >= body_threshold * 0.75:
        strength = ImbalanceDisplacementStrength.WEAK
    else:
        strength = ImbalanceDisplacementStrength.NONE
    return {
        "strength": strength,
        "body_to_range_ratio": round(body_ratio, 3),
        "range_to_atr_ratio": round(range_to_atr, 3),
        "close_position": close_position,
    }


def _quality_score(
    zone_type: ImbalanceType,
    method: ImbalanceDetectionMethod,
    displacement: Mapping[str, Any],
    zone_size_atr: float,
    fill: Mapping[str, Any],
    has_sweep: bool,
    has_structure: bool,
    ob_overlap: bool,
    fvg_confluence: bool,
    pd_alignment: bool,
    htf_alignment: str,
    target: str,
) -> tuple[float, list[str], list[str]]:
    direction = "bullish" if "bullish" in zone_type.value else "bearish"
    score = 2.5 if method == ImbalanceDetectionMethod.FVG_THREE_CANDLE else 1.5
    cap = 10.0
    reasons = [f"valid_{direction}_{method.value}_imbalance_zone"]
    warnings = ["imbalance_alone_should_not_trigger_entry"]

    if displacement["strength"] in {ImbalanceDisplacementStrength.STRONG, ImbalanceDisplacementStrength.VERY_STRONG}:
        score += 2.0
        reasons.append("strong_displacement_created_imbalance")
    elif displacement["strength"] == ImbalanceDisplacementStrength.MODERATE:
        score += 1.5
        reasons.append("moderate_displacement_created_imbalance")
    elif displacement["strength"] == ImbalanceDisplacementStrength.WEAK:
        score += 0.5
        warnings.append("displacement_confirmation_is_weak")
    else:
        warnings.append("no_displacement_confirmation")
        cap = min(cap, 5.5)

    if method == ImbalanceDetectionMethod.FVG_THREE_CANDLE:
        score += 0.75
        reasons.append("strict_three_candle_fvg_boundary_detected")
    elif not fvg_confluence:
        warnings.append("subjective_non_fvg_imbalance_requires_extra_confirmation")
        cap = min(cap, 6.0)
    if 0.05 <= zone_size_atr <= 2.5:
        score += 0.75
        reasons.append("imbalance_size_is_atr_balanced")
    elif zone_size_atr > 2.5:
        score -= 0.5
        warnings.append("large_imbalance_may_be_news_or_volatility_expansion")
    else:
        score -= 1.0
        warnings.append("imbalance_size_too_small_may_be_noise")
    if has_structure:
        score += 1.5
        reasons.append("imbalance_created_after_bos_mss_or_choch")
    else:
        warnings.append("no_bos_mss_choch_context_for_imbalance")
        cap = min(cap, 6.5)
    if has_sweep:
        score += 1.0
        reasons.append("imbalance_formed_after_matching_liquidity_sweep")
    else:
        warnings.append("no_liquidity_sweep_context")
    if ob_overlap:
        score += 0.75
        reasons.append("imbalance_overlaps_ob_poi_or_fvg_context")
    if fvg_confluence:
        score += 0.5
        reasons.append("fvg_confluence_supports_imbalance")
    if pd_alignment:
        score += 0.75
        reasons.append("premium_discount_location_supports_direction")
    if fill["active_status"] == ImbalanceActiveStatus.RESPECTED:
        score += 1.0
        reasons.append("price_retested_imbalance_and_confirmed_reaction")
    elif fill["active_status"] == ImbalanceActiveStatus.INVALIDATED:
        warnings.append(f"{direction}_imbalance_invalidated")
        cap = min(cap, 3.0)
    elif fill["active_status"] == ImbalanceActiveStatus.FULLY_FILLED:
        warnings.append("full_fill_without_close_invalidation")
        cap = min(cap, 6.0)
    elif fill["active_status"] in {ImbalanceActiveStatus.HALF_FILLED, ImbalanceActiveStatus.PARTIALLY_FILLED}:
        reasons.append("imbalance_partially_filled_without_invalidation")
    else:
        score += 0.5
        reasons.append("imbalance_remains_unfilled_and_actionable_as_poi")
    if htf_alignment == "aligned":
        score += 0.75
        reasons.append("htf_alignment_supports_imbalance")
    elif htf_alignment == "against":
        score -= 1.0
        warnings.append("htf_bias_against_imbalance_direction")
    if target != "none":
        score += 0.75
        reasons.append("target_liquidity_reference_available")
    return max(0.0, min(cap, round(score, 2))), reasons, warnings


def _quality_grade(score: float) -> ImbalanceQualityGrade:
    if score >= 9.0:
        return ImbalanceQualityGrade.HIGH_QUALITY
    if score >= 7.0:
        return ImbalanceQualityGrade.STRONG
    if score >= 5.0:
        return ImbalanceQualityGrade.MODERATE
    if score >= 3.0:
        return ImbalanceQualityGrade.WEAK
    return ImbalanceQualityGrade.INVALID


def _sequence_direction(candles: Sequence[Mapping[str, Any]]) -> str | None:
    bullish = sum(1 for candle in candles if candle["close"] > candle["open"])
    bearish = sum(1 for candle in candles if candle["close"] < candle["open"])
    if bullish >= max(2, len(candles) - 1):
        return "bullish"
    if bearish >= max(2, len(candles) - 1):
        return "bearish"
    return None


def _close_progresses(candles: Sequence[Mapping[str, Any]], direction: str) -> bool:
    closes = [float(candle["close"]) for candle in candles]
    if direction == "bullish":
        return closes[-1] > closes[0] and sum(right >= left for left, right in zip(closes, closes[1:])) >= len(closes) - 2
    return closes[-1] < closes[0] and sum(right <= left for left, right in zip(closes, closes[1:])) >= len(closes) - 2


def _has_matching_structure(events: Sequence[Mapping[str, Any]], direction: str, index: int) -> tuple[bool, str]:
    for event in sorted(events, key=lambda item: item["confirmation_candle_index"], reverse=True):
        if event["direction"] == direction and event["event_type"] in {"BOS", "MSS", "CHOCH"} and event["confirmation_candle_index"] <= index:
            return True, f"{direction}_{event['event_type']}"
    return False, "none"


def _has_matching_sweep(events: Sequence[Mapping[str, Any]], direction: str, index: int) -> tuple[bool, str]:
    wanted = "sell_side" if direction == "bullish" else "buy_side"
    for event in sorted(events, key=lambda item: item["index"], reverse=True):
        if event["index"] <= index and wanted in event["sweep_type"]:
            return True, str(event["sweep_type"])
    return False, "none"


def _has_zone_overlap(zones: Sequence[Mapping[str, Any]], direction: str, low: float, high: float) -> bool:
    return any(
        zone["direction"] in {direction, "unknown"} and max(float(zone["zone_low"]), low) <= min(float(zone["zone_high"]), high)
        for zone in zones
    )


def _premium_discount_alignment(context: Mapping[str, Any], direction: str) -> bool:
    location = str(context.get("premium_discount_location", context.get("poi_location", context.get("location", "")))).lower()
    return (direction == "bullish" and "discount" in location) or (direction == "bearish" and "premium" in location)


def _htf_alignment(context: Mapping[str, Any], direction: str) -> str:
    bias = str(context.get("htf_bias", context.get("higher_timeframe_bias", context.get("draw_bias", "")))).lower()
    if not bias:
        return "unknown"
    if "neutral" in bias:
        return "neutral"
    if direction in bias:
        return "aligned"
    if ("bullish" in bias and direction == "bearish") or ("bearish" in bias and direction == "bullish"):
        return "against"
    return "unknown"


def _target_reference(context: Mapping[str, Any], direction: str) -> str:
    target = context.get("target_liquidity", context.get("target_liquidity_reference"))
    if target:
        return str(target)
    return "nearest_buy_side_liquidity_above" if direction == "bullish" else "nearest_sell_side_liquidity_below"


def _structure_event(event: Mapping[str, Any] | str) -> dict[str, Any] | None:
    text = _event_text(event).lower()
    direction = "bullish" if "bullish" in text else "bearish" if "bearish" in text else None
    if direction is None:
        return None
    event_type = str(_field(event, "event_type", "type", default="CHOCH" if "choch" in text else "MSS" if "mss" in text else "BOS" if "bos" in text else "UNKNOWN")).upper()
    index = _int_field(event, "confirmation_candle_index", "confirmation_index", "index", "candle_index")
    return None if index is None else {"direction": direction, "event_type": event_type, "confirmation_candle_index": index}


def _liquidity_event(event: Mapping[str, Any] | str) -> dict[str, Any] | None:
    text = _event_text(event).lower()
    sweep_type = "sell_side_liquidity_sweep" if "sell" in text else "buy_side_liquidity_sweep" if "buy" in text else "unknown"
    index = _int_field(event, "sweep_candle_index", "confirmation_candle_index", "index", "candle_index")
    return None if index is None else {"sweep_type": sweep_type, "index": index}


def _zone_event(zone: Mapping[str, Any]) -> dict[str, Any] | None:
    high = _float(zone.get("zone_high", _nested(zone, "zone", "zone_high")))
    low = _float(zone.get("zone_low", _nested(zone, "zone", "zone_low")))
    if high is None or low is None:
        return None
    direction = str(zone.get("direction", "unknown")).lower()
    return {"direction": direction if direction in {"bullish", "bearish"} else "unknown", "zone_low": min(low, high), "zone_high": max(low, high)}


def _normalize_candles(candles: Sequence[CandleNode | Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    raw_items: Iterable[Any] = candles.to_dict("records") if hasattr(candles, "to_dict") else candles
    normalized: list[dict[str, Any]] = []
    for position, raw in enumerate(raw_items):
        if isinstance(raw, CandleNode):
            if not raw.is_closed:
                continue
            timestamp = raw.start_time
            values = {"index": raw.sequence_id or position, "open": raw.open_p, "high": raw.high_p, "low": raw.low_p, "close": raw.close_p, "volume": raw.volume}
        else:
            if not bool(raw.get("is_closed", raw.get("closed", True))):
                continue
            timestamp = raw.get("timestamp", raw.get("time", datetime.fromtimestamp(position)))
            values = raw
        if not isinstance(timestamp, datetime):
            timestamp = datetime.fromisoformat(str(timestamp)) if isinstance(timestamp, str) else datetime.fromtimestamp(float(timestamp))
        normalized.append(
            {
                "position": len(normalized),
                "index": int(values.get("index", position)),
                "timestamp": timestamp,
                "open": float(values["open"]),
                "high": float(values["high"]),
                "low": float(values["low"]),
                "close": float(values["close"]),
                "volume": float(values.get("volume", 0.0)),
            }
        )
    return normalized


def _atr_values(candles: Sequence[dict[str, Any]], period: int) -> list[float]:
    true_ranges: list[float] = []
    values: list[float] = []
    for position, candle in enumerate(candles):
        previous_close = candles[position - 1]["close"] if position > 0 else candle["close"]
        true_range = max(candle["high"] - candle["low"], abs(candle["high"] - previous_close), abs(candle["low"] - previous_close), 1e-9)
        true_ranges.append(true_range)
        window = true_ranges[max(0, len(true_ranges) - period) :]
        values.append(sum(window) / len(window))
    return values


def _nested(mapping: Mapping[str, Any], *path: str, default: Any = None) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return default
        current = current[key]
    return current


def _field(source: Mapping[str, Any] | str, *names: str, default: Any = None) -> Any:
    if isinstance(source, str):
        return default
    for name in names:
        if name in source and source[name] is not None:
            return source[name]
    return default


def _int_field(source: Mapping[str, Any] | str, *names: str) -> int | None:
    value = _field(source, *names, default=None)
    return int(value) if value is not None else None


def _float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _event_text(event: Mapping[str, Any] | str) -> str:
    if isinstance(event, str):
        return event
    return " ".join(str(value) for value in event.values() if value is not None)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
