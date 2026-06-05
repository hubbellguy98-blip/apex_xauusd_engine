"""Rule-based ICT/SMC Fair Value Gap detection.

Fair Value Gap is a three-candle wick-to-wick imbalance, not a normal open/close
session gap. The detector only uses closed candles, tracks fill/invalidation,
and keeps FVG as a point of interest rather than an automatic entry signal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from src.core.domain.market_data import CandleNode


class FVGType(str, Enum):
    BULLISH = "bullish_fvg"
    BEARISH = "bearish_fvg"


class FVGActiveStatus(str, Enum):
    UNTOUCHED = "untouched"
    PARTIALLY_FILLED = "partially_filled"
    HALF_FILLED = "half_filled"
    FULLY_FILLED = "fully_filled"
    RESPECTED = "respected"
    INVALIDATED = "invalidated"
    STALE = "stale"


class FVGDisplacementStrength(str, Enum):
    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    VERY_STRONG = "very_strong"


class FVGQualityGrade(str, Enum):
    INVALID = "invalid"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True, slots=True)
class FairValueGap:
    concept_name: str
    symbol: str
    timeframe: str
    fvg_id: str
    fvg_type: FVGType
    direction: str
    zone_low: float
    zone_high: float
    zone_mid: float
    fvg_size: float
    fvg_size_atr: float
    creation_index: int
    creation_timestamp: datetime
    candle_1_index: int
    candle_2_index: int
    candle_3_index: int
    displacement_candle_index: int
    displacement_strength: FVGDisplacementStrength
    body_to_range_ratio: float
    range_to_atr_ratio: float
    close_position: str
    filled_percent: float
    active_status: FVGActiveStatus
    lowest_low_after_creation: float | None
    highest_high_after_creation: float | None
    filled_at_index: int | None
    invalidated: bool
    invalidated_at_index: int | None
    reaction_confirmed: bool
    created_after_liquidity_sweep: bool
    sweep_type: str
    created_after_structure_event: bool
    structure_event_type: str
    ob_overlap: bool
    premium_discount_alignment: bool
    htf_alignment: str
    target_liquidity_reference: str
    entry_allowed_from_fvg_alone: bool
    entry_allowed_after_reaction: bool
    recommended_entry_style: str
    stop_loss_reference: str
    quality_score: float
    quality_grade: FVGQualityGrade
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fvg_type"] = self.fvg_type.value
        payload["active_status"] = self.active_status.value
        payload["displacement_strength"] = self.displacement_strength.value
        payload["quality_grade"] = self.quality_grade.value
        return payload


def detect_fvg(
    df: Sequence[CandleNode | Mapping[str, Any]] | Any,
    structure_events: Sequence[Mapping[str, Any] | str] | None = None,
    liquidity_sweeps: Sequence[Mapping[str, Any] | str] | None = None,
    order_blocks: Sequence[Mapping[str, Any]] | None = None,
    poi_zones: Sequence[Mapping[str, Any]] | None = None,
    context: Mapping[str, Any] | None = None,
    *,
    symbol: str = "unknown",
    timeframe: str = "unknown",
    atr_period: int = 14,
    displacement_body_threshold: float = 0.55,
    displacement_atr_threshold: float = 1.0,
    close_position_threshold: float = 0.70,
    min_fvg_size_atr: float = 0.05,
    large_fvg_size_atr: float = 2.0,
    invalidation_buffer: float = 0.05,
    stale_after_bars: int = 120,
) -> list[dict[str, Any]]:
    """Detect bullish and bearish three-candle Fair Value Gaps."""
    candles = _normalize_candles(df)
    if len(candles) < 3:
        return []

    atr_values = _atr_values(candles, atr_period)
    structures = [_structure_event(event) for event in (structure_events or ())]
    structures = [event for event in structures if event is not None]
    sweeps = [_liquidity_event(event) for event in (liquidity_sweeps or ())]
    sweeps = [event for event in sweeps if event is not None]
    confluence_zones = [_zone_event(zone) for zone in (*(order_blocks or ()), *(poi_zones or ()))]
    confluence_zones = [zone for zone in confluence_zones if zone is not None]
    active_context = dict(context or {})

    gaps: list[FairValueGap] = []
    for position in range(2, len(candles)):
        candle_1 = candles[position - 2]
        candle_2 = candles[position - 1]
        candle_3 = candles[position]
        atr = atr_values[position - 1]

        if candle_1["high"] < candle_3["low"]:
            gap = _build_fvg(
                candles,
                atr_values,
                position,
                candle_1,
                candle_2,
                candle_3,
                FVGType.BULLISH,
                candle_1["high"],
                candle_3["low"],
                atr,
                structures,
                sweeps,
                confluence_zones,
                active_context,
                symbol,
                timeframe,
                displacement_body_threshold,
                displacement_atr_threshold,
                close_position_threshold,
                min_fvg_size_atr,
                large_fvg_size_atr,
                invalidation_buffer,
                stale_after_bars,
            )
            gaps.append(gap)

        if candle_1["low"] > candle_3["high"]:
            gap = _build_fvg(
                candles,
                atr_values,
                position,
                candle_1,
                candle_2,
                candle_3,
                FVGType.BEARISH,
                candle_3["high"],
                candle_1["low"],
                atr,
                structures,
                sweeps,
                confluence_zones,
                active_context,
                symbol,
                timeframe,
                displacement_body_threshold,
                displacement_atr_threshold,
                close_position_threshold,
                min_fvg_size_atr,
                large_fvg_size_atr,
                invalidation_buffer,
                stale_after_bars,
            )
            gaps.append(gap)

    return [
        gap.as_dict()
        for gap in sorted(
            gaps,
            key=lambda item: (not item.invalidated, item.quality_score, item.creation_index),
            reverse=True,
        )
    ]


def _build_fvg(
    candles: Sequence[dict[str, Any]],
    atr_values: Sequence[float],
    creation_position: int,
    candle_1: Mapping[str, Any],
    candle_2: Mapping[str, Any],
    candle_3: Mapping[str, Any],
    fvg_type: FVGType,
    zone_low: float,
    zone_high: float,
    atr: float,
    structures: Sequence[Mapping[str, Any]],
    sweeps: Sequence[Mapping[str, Any]],
    confluence_zones: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    symbol: str,
    timeframe: str,
    body_threshold: float,
    atr_threshold: float,
    close_threshold: float,
    min_fvg_size_atr: float,
    large_fvg_size_atr: float,
    invalidation_buffer: float,
    stale_after_bars: int,
) -> FairValueGap:
    direction = "bullish" if fvg_type == FVGType.BULLISH else "bearish"
    zone_mid = (zone_high + zone_low) / 2.0
    fvg_size = zone_high - zone_low
    fvg_size_atr = fvg_size / max(atr, 1e-9)
    displacement = _displacement_snapshot(candle_2, atr, direction, body_threshold, atr_threshold, close_threshold)
    fill = _fill_status(
        candles,
        creation_position,
        direction,
        zone_low,
        zone_high,
        zone_mid,
        invalidation_buffer,
        stale_after_bars,
    )
    created_after_sweep, sweep_type = _has_matching_sweep(sweeps, direction, int(candle_3["index"]))
    created_after_structure, structure_type = _has_matching_structure(structures, direction, int(candle_3["index"]))
    ob_overlap = _has_zone_overlap(confluence_zones, direction, zone_low, zone_high)
    pd_alignment = _premium_discount_alignment(context, direction)
    htf_alignment = _htf_alignment(context, direction)
    target = _target_reference(context, direction)
    score, reasons, warnings = _quality_score(
        fvg_type,
        displacement,
        fvg_size_atr,
        min_fvg_size_atr,
        large_fvg_size_atr,
        fill,
        created_after_sweep,
        created_after_structure,
        ob_overlap,
        pd_alignment,
        htf_alignment,
        target,
    )
    entry_after_reaction = fill["reaction_confirmed"] and not fill["invalidated"] and score >= 6.0
    return FairValueGap(
        concept_name="Fair Value Gap",
        symbol=symbol or str(context.get("symbol", "unknown")),
        timeframe=timeframe or str(context.get("timeframe", "unknown")),
        fvg_id=f"FVG_{timeframe}_{direction.upper()}_{candle_3['index']}",
        fvg_type=fvg_type,
        direction=direction,
        zone_low=round(zone_low, 10),
        zone_high=round(zone_high, 10),
        zone_mid=round(zone_mid, 10),
        fvg_size=round(fvg_size, 10),
        fvg_size_atr=round(fvg_size_atr, 3),
        creation_index=int(candle_3["index"]),
        creation_timestamp=candle_3["timestamp"],
        candle_1_index=int(candle_1["index"]),
        candle_2_index=int(candle_2["index"]),
        candle_3_index=int(candle_3["index"]),
        displacement_candle_index=int(candle_2["index"]),
        displacement_strength=displacement["strength"],
        body_to_range_ratio=displacement["body_to_range_ratio"],
        range_to_atr_ratio=displacement["range_to_atr_ratio"],
        close_position=displacement["close_position"],
        filled_percent=fill["filled_percent"],
        active_status=fill["active_status"],
        lowest_low_after_creation=fill["lowest_low_after_creation"],
        highest_high_after_creation=fill["highest_high_after_creation"],
        filled_at_index=fill["filled_at_index"],
        invalidated=fill["invalidated"],
        invalidated_at_index=fill["invalidated_at_index"],
        reaction_confirmed=fill["reaction_confirmed"],
        created_after_liquidity_sweep=created_after_sweep,
        sweep_type=sweep_type,
        created_after_structure_event=created_after_structure,
        structure_event_type=structure_type,
        ob_overlap=ob_overlap,
        premium_discount_alignment=pd_alignment,
        htf_alignment=htf_alignment,
        target_liquidity_reference=target,
        entry_allowed_from_fvg_alone=False,
        entry_allowed_after_reaction=entry_after_reaction,
        recommended_entry_style=f"wait_for_retest_into_fvg_and_{direction}_reaction_confirmation",
        stop_loss_reference="below_fvg_zone_low_or_below_sweep_low" if direction == "bullish" else "above_fvg_zone_high_or_above_sweep_high",
        quality_score=score,
        quality_grade=_quality_grade(score),
        reasons=tuple(dict.fromkeys(reasons)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _fill_status(
    candles: Sequence[dict[str, Any]],
    creation_position: int,
    direction: str,
    zone_low: float,
    zone_high: float,
    zone_mid: float,
    invalidation_buffer: float,
    stale_after_bars: int,
) -> dict[str, Any]:
    fvg_size = zone_high - zone_low
    filled_percent = 0.0
    active_status = FVGActiveStatus.UNTOUCHED
    lowest_low: float | None = None
    highest_high: float | None = None
    filled_at_index: int | None = None
    invalidated_at_index: int | None = None
    reaction_confirmed = False

    for future in candles[creation_position + 1:]:
        if direction == "bullish":
            if future["close"] < zone_low - invalidation_buffer:
                return {
                    "filled_percent": 100.0,
                    "active_status": FVGActiveStatus.INVALIDATED,
                    "lowest_low_after_creation": future["low"],
                    "highest_high_after_creation": highest_high,
                    "filled_at_index": int(future["index"]),
                    "invalidated": True,
                    "invalidated_at_index": int(future["index"]),
                    "reaction_confirmed": False,
                }
            if future["low"] <= zone_high:
                lowest_low = future["low"] if lowest_low is None else min(lowest_low, future["low"])
                filled_percent = _clamp((zone_high - lowest_low) / fvg_size * 100.0, 0.0, 100.0)
                filled_at_index = int(future["index"])
                active_status = _status_from_fill_percent(filled_percent)
                if future["low"] <= zone_low and future["close"] > zone_low:
                    active_status = FVGActiveStatus.FULLY_FILLED
                if future["close"] > future["open"] and (future["close"] > zone_mid or future["close"] > zone_high):
                    active_status = FVGActiveStatus.RESPECTED
                    reaction_confirmed = True
        else:
            if future["close"] > zone_high + invalidation_buffer:
                return {
                    "filled_percent": 100.0,
                    "active_status": FVGActiveStatus.INVALIDATED,
                    "lowest_low_after_creation": lowest_low,
                    "highest_high_after_creation": future["high"],
                    "filled_at_index": int(future["index"]),
                    "invalidated": True,
                    "invalidated_at_index": int(future["index"]),
                    "reaction_confirmed": False,
                }
            if future["high"] >= zone_low:
                highest_high = future["high"] if highest_high is None else max(highest_high, future["high"])
                filled_percent = _clamp((highest_high - zone_low) / fvg_size * 100.0, 0.0, 100.0)
                filled_at_index = int(future["index"])
                active_status = _status_from_fill_percent(filled_percent)
                if future["high"] >= zone_high and future["close"] < zone_high:
                    active_status = FVGActiveStatus.FULLY_FILLED
                if future["close"] < future["open"] and (future["close"] < zone_mid or future["close"] < zone_low):
                    active_status = FVGActiveStatus.RESPECTED
                    reaction_confirmed = True

    if active_status == FVGActiveStatus.UNTOUCHED and len(candles) - creation_position - 1 > stale_after_bars:
        active_status = FVGActiveStatus.STALE

    return {
        "filled_percent": round(filled_percent, 2),
        "active_status": active_status,
        "lowest_low_after_creation": lowest_low,
        "highest_high_after_creation": highest_high,
        "filled_at_index": filled_at_index,
        "invalidated": False,
        "invalidated_at_index": invalidated_at_index,
        "reaction_confirmed": reaction_confirmed,
    }


def _status_from_fill_percent(filled_percent: float) -> FVGActiveStatus:
    if filled_percent <= 0.0:
        return FVGActiveStatus.UNTOUCHED
    if filled_percent < 50.0:
        return FVGActiveStatus.PARTIALLY_FILLED
    if filled_percent < 100.0:
        return FVGActiveStatus.HALF_FILLED
    return FVGActiveStatus.FULLY_FILLED


def _displacement_snapshot(
    candle: Mapping[str, Any],
    atr: float,
    direction: str,
    body_threshold: float,
    atr_threshold: float,
    close_threshold: float,
) -> dict[str, Any]:
    candle_range = max(float(candle["high"]) - float(candle["low"]), 1e-9)
    body = abs(float(candle["close"]) - float(candle["open"]))
    body_ratio = body / candle_range
    range_to_atr = candle_range / max(float(atr), 1e-9)
    if direction == "bullish":
        directional = candle["close"] > candle["open"]
        close_ratio = (float(candle["close"]) - float(candle["low"])) / candle_range
        close_position = "near_high" if close_ratio >= close_threshold else "middle_or_weak"
    else:
        directional = candle["close"] < candle["open"]
        close_ratio = (float(candle["high"]) - float(candle["close"])) / candle_range
        close_position = "near_low" if close_ratio >= close_threshold else "middle_or_weak"

    if directional and body_ratio >= body_threshold and range_to_atr >= atr_threshold * 1.5 and close_ratio >= close_threshold:
        strength = FVGDisplacementStrength.STRONG
    elif directional and body_ratio >= body_threshold and range_to_atr >= atr_threshold and close_ratio >= close_threshold:
        strength = FVGDisplacementStrength.MODERATE
    elif directional and body_ratio >= body_threshold * 0.75:
        strength = FVGDisplacementStrength.WEAK
    else:
        strength = FVGDisplacementStrength.NONE
    return {
        "strength": strength,
        "body_to_range_ratio": round(body_ratio, 3),
        "range_to_atr_ratio": round(range_to_atr, 3),
        "close_position": close_position,
    }


def _quality_score(
    fvg_type: FVGType,
    displacement: Mapping[str, Any],
    fvg_size_atr: float,
    min_fvg_size_atr: float,
    large_fvg_size_atr: float,
    fill: Mapping[str, Any],
    created_after_sweep: bool,
    created_after_structure: bool,
    ob_overlap: bool,
    pd_alignment: bool,
    htf_alignment: str,
    target: str,
) -> tuple[float, list[str], list[str]]:
    direction = "bullish" if fvg_type == FVGType.BULLISH else "bearish"
    reasons = [f"valid_{direction}_three_candle_fvg"]
    warnings = ["fvg_alone_should_not_trigger_entry"]
    score = 2.0
    score_cap = 10.0

    strength = displacement["strength"]
    if strength == FVGDisplacementStrength.STRONG:
        score += 2.0
        reasons.append(f"candle_2_was_strong_{direction}_displacement")
    elif strength == FVGDisplacementStrength.MODERATE:
        score += 1.25
        reasons.append(f"candle_2_was_moderate_{direction}_displacement")
    elif strength == FVGDisplacementStrength.WEAK:
        score += 0.5
        warnings.append("candle_2_displacement_was_weak")
    else:
        warnings.append("no_displacement_candle_confirmation")
        score_cap = min(score_cap, 6.0)

    if fvg_size_atr < min_fvg_size_atr:
        warnings.append("fvg_size_too_small_may_be_noise")
        score -= 1.0
    elif fvg_size_atr > large_fvg_size_atr:
        warnings.append("fvg_size_large_volatility_or_news_risk")
        score -= 0.5
    else:
        score += 0.75
        reasons.append("fvg_size_is_atr_balanced")

    if created_after_structure:
        score += 1.5
        reasons.append("fvg_created_during_or_after_structure_event")
    else:
        warnings.append("no_bos_mss_context_for_fvg")

    if created_after_sweep:
        score += 1.0
        reasons.append("fvg_formed_after_matching_liquidity_sweep")
    else:
        warnings.append("no_liquidity_sweep_context")

    if ob_overlap:
        score += 0.75
        reasons.append("fvg_overlaps_ob_or_poi_context")

    if pd_alignment:
        score += 0.75
        reasons.append("premium_discount_location_supports_fvg_direction")

    if fill["active_status"] == FVGActiveStatus.RESPECTED:
        score += 1.0
        reasons.append("price_retested_fvg_and_confirmed_reaction")
    elif fill["active_status"] == FVGActiveStatus.INVALIDATED:
        warnings.append(f"{direction}_fvg_invalidated")
        score_cap = min(score_cap, 3.0)
    elif fill["active_status"] == FVGActiveStatus.FULLY_FILLED:
        warnings.append("fully_filled_but_not_invalidated")
    elif fill["active_status"] in {FVGActiveStatus.HALF_FILLED, FVGActiveStatus.PARTIALLY_FILLED}:
        reasons.append("fvg_partially_filled_without_invalidation")

    if htf_alignment == "aligned":
        score += 0.75
        reasons.append("htf_alignment_supports_fvg")
    elif htf_alignment == "neutral":
        score += 0.4
    elif htf_alignment == "against":
        warnings.append("htf_bias_against_fvg_direction")
        score -= 1.0

    if target != "none":
        score += 0.75
        reasons.append("target_liquidity_reference_available")
    else:
        warnings.append("no_target_liquidity_reference")
        score -= 0.5

    return max(0.0, min(score_cap, round(score, 2))), reasons, warnings


def _quality_grade(score: float) -> FVGQualityGrade:
    if score >= 9.0:
        return FVGQualityGrade.HIGH_QUALITY
    if score >= 7.0:
        return FVGQualityGrade.STRONG
    if score >= 5.0:
        return FVGQualityGrade.MODERATE
    if score >= 3.0:
        return FVGQualityGrade.WEAK
    return FVGQualityGrade.INVALID


def _has_matching_structure(
    structures: Sequence[Mapping[str, Any]],
    direction: str,
    creation_index: int,
) -> tuple[bool, str]:
    for event in sorted(structures, key=lambda item: item["confirmation_candle_index"], reverse=True):
        if event["direction"] == direction and event["event_type"] in {"BOS", "MSS", "CHOCH"} and event["confirmation_candle_index"] <= creation_index:
            return True, f"{direction}_{event['event_type']}"
    return False, "none"


def _has_matching_sweep(
    sweeps: Sequence[Mapping[str, Any]],
    direction: str,
    creation_index: int,
) -> tuple[bool, str]:
    wanted = "sell_side" if direction == "bullish" else "buy_side"
    for sweep in sorted(sweeps, key=lambda item: item["index"], reverse=True):
        if sweep["index"] <= creation_index and wanted in sweep["sweep_type"]:
            return True, str(sweep["sweep_type"])
    return False, "none"


def _has_zone_overlap(zones: Sequence[Mapping[str, Any]], direction: str, zone_low: float, zone_high: float) -> bool:
    for zone in zones:
        if zone["direction"] not in {direction, "unknown"}:
            continue
        if max(float(zone["zone_low"]), zone_low) <= min(float(zone["zone_high"]), zone_high):
            return True
    return False


def _premium_discount_alignment(context: Mapping[str, Any], direction: str) -> bool:
    location = str(
        context.get(
            "premium_discount_location",
            context.get("poi_location", context.get("location", "")),
        )
    ).lower()
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
    if index is None:
        return None
    return {"direction": direction, "event_type": event_type, "confirmation_candle_index": index}


def _liquidity_event(event: Mapping[str, Any] | str) -> dict[str, Any] | None:
    text = _event_text(event).lower()
    sweep_type = "sell_side_liquidity_sweep" if "sell" in text else "buy_side_liquidity_sweep" if "buy" in text else "unknown"
    index = _int_field(event, "sweep_candle_index", "confirmation_candle_index", "index", "candle_index")
    if index is None:
        return None
    return {"sweep_type": sweep_type, "index": index}


def _zone_event(zone: Mapping[str, Any]) -> dict[str, Any] | None:
    zone_high = _float(zone.get("zone_high", _nested(zone, "zone", "zone_high")))
    zone_low = _float(zone.get("zone_low", _nested(zone, "zone", "zone_low")))
    if zone_high is None or zone_low is None:
        return None
    direction = str(zone.get("direction", "unknown")).lower()
    return {
        "direction": direction if direction in {"bullish", "bearish"} else "unknown",
        "zone_high": max(zone_high, zone_low),
        "zone_low": min(zone_high, zone_low),
    }


def _normalize_candles(candles: Sequence[CandleNode | Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    if hasattr(candles, "to_dict"):
        raw_items: Iterable[Any] = candles.to_dict("records")
    else:
        raw_items = candles
    normalized: list[dict[str, Any]] = []
    for position, raw in enumerate(raw_items):
        if isinstance(raw, CandleNode):
            if not raw.is_closed:
                continue
            timestamp = raw.start_time
            values = {
                "index": raw.sequence_id or position,
                "open": raw.open_p,
                "high": raw.high_p,
                "low": raw.low_p,
                "close": raw.close_p,
                "volume": raw.volume,
            }
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
        window = true_ranges[max(0, len(true_ranges) - period):]
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
