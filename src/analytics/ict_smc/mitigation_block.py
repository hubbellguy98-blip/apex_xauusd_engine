"""Conservative ICT/SMC mitigation block detection.

Mitigation blocks are more subjective than swings, BOS, MSS, sweeps, FVGs, or
order blocks. This module therefore only marks them when a confirmed structure
shift exists first, then price returns to a clear old opposite-side candle and
reacts in the new structure direction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from src.core.domain.market_data import CandleNode


class MitigationType(str, Enum):
    BULLISH = "bullish_mitigation_block"
    BEARISH = "bearish_mitigation_block"
    BULLISH_CANDIDATE = "bullish_mitigation_block_candidate"
    BEARISH_CANDIDATE = "bearish_mitigation_block_candidate"
    INVALID = "invalid_mitigation_block"


class MitigationRetestStatus(str, Enum):
    FRESH = "fresh"
    TOUCHED = "touched"
    PARTIALLY_MITIGATED = "partially_mitigated"
    DEEP_MITIGATION = "deep_mitigation"
    CONFIRMED_REACTION = "confirmed_reaction"
    RETEST_NO_REACTION = "retest_no_reaction"
    FAILED = "failed"


class MitigationQualityGrade(str, Enum):
    INVALID = "invalid"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True, slots=True)
class MitigationBlock:
    concept_name: str
    symbol: str
    timeframe: str
    mitigation_id: str
    mitigation_type: MitigationType
    direction: str
    valid_mitigation_block: bool
    zone_high: float | None
    zone_low: float | None
    mean_threshold: float | None
    body_zone_high: float | None
    body_zone_low: float | None
    refined_zone_high: float | None
    refined_zone_low: float | None
    candidate_candle_index: int | None
    candidate_timestamp: datetime | None
    candidate_source_type: str
    created_by_event: str
    structure_confirmation_index: int | None
    structure_event_type: str
    displacement_strength: str
    created_after_sweep: bool
    sweep_type: str
    fvg_confluence: bool
    ob_confluence: bool
    premium_discount_alignment: bool
    htf_alignment: str
    target_liquidity_reference: str
    retest_status: MitigationRetestStatus
    fresh_status: str
    retest_candle_index: int | None
    mitigation_depth: str
    reaction_confirmed: bool
    reaction_type: str
    entry_allowed_from_mitigation_block_alone: bool
    entry_allowed_after_confirmation: bool
    stop_loss_reference: str
    quality_score: float
    confidence_grade: MitigationQualityGrade
    warnings: tuple[str, ...]
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["mitigation_type"] = self.mitigation_type.value
        payload["retest_status"] = self.retest_status.value
        payload["confidence_grade"] = self.confidence_grade.value
        return payload


def detect_mitigation_blocks(
    df: Sequence[CandleNode | Mapping[str, Any]],
    structure_events: Sequence[Mapping[str, Any] | str] | None = None,
    liquidity_sweeps: Sequence[Mapping[str, Any] | str] | None = None,
    fvg_events: Sequence[Mapping[str, Any] | str] | None = None,
    order_blocks: Sequence[Mapping[str, Any]] | None = None,
    poi_zones: Sequence[Mapping[str, Any]] | None = None,
    context: Mapping[str, Any] | None = None,
    *,
    symbol: str = "unknown",
    timeframe: str = "unknown",
    max_lookback_bars: int = 12,
    atr_period: int = 14,
    max_zone_size_atr: float = 2.5,
    invalidation_buffer: float = 0.05,
) -> list[dict[str, Any]]:
    """Detect conservative mitigation blocks after confirmed BOS/MSS events."""
    candles = _normalize_candles(df)
    if not candles:
        return []

    atr_values = _atr_values(candles, atr_period)
    structures = [_structure_event(event) for event in (structure_events or ())]
    structures = [event for event in structures if event is not None]
    sweeps = [_liquidity_event(event) for event in (liquidity_sweeps or ())]
    sweeps = [event for event in sweeps if event is not None]
    fvgs = [_context_event(event) for event in (fvg_events or ())]
    fvgs = [event for event in fvgs if event is not None]
    confluence_zones = [_zone_event(zone) for zone in (*(order_blocks or ()), *(poi_zones or ()))]
    confluence_zones = [zone for zone in confluence_zones if zone is not None]
    active_context = dict(context or {})

    usable_structures = [
        event
        for event in structures
        if event["event_type"] in {"BOS", "MSS"}
        and event["close_confirmed"]
        and not event["wick_only"]
        and event["displacement_strength"] not in {"none", "weak", ""}
    ]
    if not usable_structures:
        return [_invalid_no_structure(candles[-1], symbol, timeframe).as_dict()]

    blocks: list[MitigationBlock] = []
    for structure in usable_structures:
        position = _position_for_index(candles, structure["confirmation_candle_index"])
        if position is None:
            continue
        structure = dict(structure)
        structure["position"] = position
        direction = structure["direction"]
        candidate = _find_candidate_zone(
            candles,
            atr_values,
            position,
            direction,
            max_lookback_bars,
            max_zone_size_atr,
        )
        if candidate is None:
            continue
        block = _build_mitigation_block(
            candles,
            structure,
            candidate,
            sweeps,
            fvgs,
            confluence_zones,
            active_context,
            symbol,
            timeframe,
            invalidation_buffer,
        )
        blocks.append(block)

    return [
        block.as_dict()
        for block in sorted(
            blocks,
            key=lambda item: (item.valid_mitigation_block, item.quality_score, item.structure_confirmation_index or -1),
            reverse=True,
        )
    ]


def _invalid_no_structure(candle: Mapping[str, Any], symbol: str, timeframe: str) -> MitigationBlock:
    return MitigationBlock(
        concept_name="Mitigation Block",
        symbol=symbol,
        timeframe=timeframe,
        mitigation_id=f"MIT_{timeframe}_INVALID_{candle['index']}",
        mitigation_type=MitigationType.INVALID,
        direction="unknown",
        valid_mitigation_block=False,
        zone_high=None,
        zone_low=None,
        mean_threshold=None,
        body_zone_high=None,
        body_zone_low=None,
        refined_zone_high=None,
        refined_zone_low=None,
        candidate_candle_index=None,
        candidate_timestamp=None,
        candidate_source_type="none",
        created_by_event="none",
        structure_confirmation_index=None,
        structure_event_type="none",
        displacement_strength="none",
        created_after_sweep=False,
        sweep_type="none",
        fvg_confluence=False,
        ob_confluence=False,
        premium_discount_alignment=False,
        htf_alignment="unknown",
        target_liquidity_reference="none",
        retest_status=MitigationRetestStatus.FRESH,
        fresh_status="invalid",
        retest_candle_index=None,
        mitigation_depth="none",
        reaction_confirmed=False,
        reaction_type="none",
        entry_allowed_from_mitigation_block_alone=False,
        entry_allowed_after_confirmation=False,
        stop_loss_reference="none",
        quality_score=0.0,
        confidence_grade=MitigationQualityGrade.INVALID,
        warnings=("no_structure_shift", "mitigation_block_is_secondary_poi_not_primary_signal"),
        reasons=("no_bos_or_mss_available_so_old_candle_was_rejected",),
    )


def _build_mitigation_block(
    candles: Sequence[dict[str, Any]],
    structure: Mapping[str, Any],
    candidate: Mapping[str, Any],
    sweeps: Sequence[Mapping[str, Any]],
    fvg_events: Sequence[Mapping[str, Any]],
    confluence_zones: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    symbol: str,
    timeframe: str,
    invalidation_buffer: float,
) -> MitigationBlock:
    direction = str(structure["direction"])
    retest = _classify_retest(
        candles,
        int(structure["position"]),
        candidate,
        direction,
        invalidation_buffer,
        structure_events=[structure],
    )
    created_after_sweep, sweep_type = _has_matching_sweep(sweeps, direction, int(structure["confirmation_candle_index"]))
    fvg_confluence = _has_fvg_confluence(fvg_events, direction, int(structure["confirmation_candle_index"]), candidate)
    ob_confluence = _has_zone_confluence(confluence_zones, direction, candidate)
    pd_alignment = _premium_discount_alignment(context, direction)
    htf_alignment = _htf_alignment(context, direction)
    target = _target_reference(context, direction)
    score, reasons, warnings = _quality_score(
        structure,
        candidate,
        retest,
        created_after_sweep,
        fvg_confluence,
        ob_confluence,
        pd_alignment,
        htf_alignment,
        target,
    )
    is_confirmed = retest["status"] == MitigationRetestStatus.CONFIRMED_REACTION and score >= 5.0
    mitigation_type = _mitigation_type(direction, confirmed=is_confirmed)
    grade = _quality_grade(score)
    return MitigationBlock(
        concept_name="Mitigation Block",
        symbol=symbol or str(context.get("symbol", "unknown")),
        timeframe=timeframe or str(context.get("timeframe", "unknown")),
        mitigation_id=f"MIT_{timeframe}_{direction.upper()}_{candidate['index']}_{structure['confirmation_candle_index']}",
        mitigation_type=mitigation_type,
        direction=direction,
        valid_mitigation_block=is_confirmed,
        zone_high=float(candidate["zone_high"]),
        zone_low=float(candidate["zone_low"]),
        mean_threshold=float(candidate["mean_threshold"]),
        body_zone_high=float(candidate["body_zone_high"]),
        body_zone_low=float(candidate["body_zone_low"]),
        refined_zone_high=float(candidate["refined_zone_high"]),
        refined_zone_low=float(candidate["refined_zone_low"]),
        candidate_candle_index=int(candidate["index"]),
        candidate_timestamp=candidate["timestamp"],
        candidate_source_type=str(candidate["source_type"]),
        created_by_event=f"{direction}_{structure['event_type']}",
        structure_confirmation_index=int(structure["confirmation_candle_index"]),
        structure_event_type=str(structure["event_type"]),
        displacement_strength=str(structure["displacement_strength"]),
        created_after_sweep=created_after_sweep,
        sweep_type=sweep_type,
        fvg_confluence=fvg_confluence,
        ob_confluence=ob_confluence,
        premium_discount_alignment=pd_alignment,
        htf_alignment=htf_alignment,
        target_liquidity_reference=target,
        retest_status=retest["status"],
        fresh_status="fresh" if retest["status"] == MitigationRetestStatus.FRESH else "mitigated",
        retest_candle_index=retest["candle_index"],
        mitigation_depth=str(retest["mitigation_depth"]),
        reaction_confirmed=bool(retest["reaction_confirmed"]),
        reaction_type=str(retest["reaction_type"]),
        entry_allowed_from_mitigation_block_alone=False,
        entry_allowed_after_confirmation=is_confirmed,
        stop_loss_reference="below_zone_low_or_recent_sweep_low" if direction == "bullish" else "above_zone_high_or_recent_sweep_high",
        quality_score=score,
        confidence_grade=grade,
        warnings=tuple(dict.fromkeys(warnings)),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def _mitigation_type(direction: str, *, confirmed: bool) -> MitigationType:
    if direction == "bullish":
        return MitigationType.BULLISH if confirmed else MitigationType.BULLISH_CANDIDATE
    return MitigationType.BEARISH if confirmed else MitigationType.BEARISH_CANDIDATE


def _find_candidate_zone(
    candles: Sequence[dict[str, Any]],
    atr_values: Sequence[float],
    structure_position: int,
    direction: str,
    max_lookback_bars: int,
    max_zone_size_atr: float,
) -> dict[str, Any] | None:
    start = max(0, structure_position - max_lookback_bars)
    candidate_side = "bearish" if direction == "bullish" else "bullish"
    for position in range(structure_position - 1, start - 1, -1):
        candle = candles[position]
        if candidate_side == "bearish" and candle["close"] >= candle["open"]:
            continue
        if candidate_side == "bullish" and candle["close"] <= candle["open"]:
            continue
        zone_size_atr = (candle["high"] - candle["low"]) / max(atr_values[position], 1e-9)
        if zone_size_atr > max_zone_size_atr:
            continue
        return _candidate_from_candle(candle, direction, zone_size_atr)
    return None


def _candidate_from_candle(candle: Mapping[str, Any], direction: str, zone_size_atr: float) -> dict[str, Any]:
    zone_high = float(candle["high"])
    zone_low = float(candle["low"])
    if direction == "bullish":
        body_high = float(candle["open"])
        body_low = float(candle["close"])
        refined_high = float(candle["open"])
        refined_low = float(candle["low"])
        source_type = "prior_bearish_candle_before_bullish_structure_shift"
    else:
        body_high = float(candle["close"])
        body_low = float(candle["open"])
        refined_high = float(candle["high"])
        refined_low = float(candle["open"])
        source_type = "prior_bullish_candle_before_bearish_structure_shift"
    return {
        "index": int(candle["index"]),
        "timestamp": candle["timestamp"],
        "zone_high": zone_high,
        "zone_low": zone_low,
        "mean_threshold": (zone_high + zone_low) / 2.0,
        "body_zone_high": max(body_high, body_low),
        "body_zone_low": min(body_high, body_low),
        "refined_zone_high": max(refined_high, refined_low),
        "refined_zone_low": min(refined_high, refined_low),
        "source_type": source_type,
        "zone_size_atr": zone_size_atr,
    }


def _classify_retest(
    candles: Sequence[dict[str, Any]],
    structure_position: int,
    candidate: Mapping[str, Any],
    direction: str,
    invalidation_buffer: float,
    structure_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    for candle in candles[structure_position + 1:]:
        if direction == "bullish":
            if candle["close"] < candidate["zone_low"] - invalidation_buffer:
                return _retest(MitigationRetestStatus.FAILED, candle, "closed_below_zone_low", False, "none")
            if candle["low"] <= candidate["zone_high"]:
                status = _bullish_retest_depth(candle, candidate)
                reaction, reaction_type = _reaction(candle, direction, candidate, structure_events)
                if reaction:
                    status = MitigationRetestStatus.CONFIRMED_REACTION
                elif status != MitigationRetestStatus.FAILED:
                    status = MitigationRetestStatus.RETEST_NO_REACTION
                return _retest(status, candle, "bullish_mitigation_retest", reaction, reaction_type)
        else:
            if candle["close"] > candidate["zone_high"] + invalidation_buffer:
                return _retest(MitigationRetestStatus.FAILED, candle, "closed_above_zone_high", False, "none")
            if candle["high"] >= candidate["zone_low"]:
                status = _bearish_retest_depth(candle, candidate)
                reaction, reaction_type = _reaction(candle, direction, candidate, structure_events)
                if reaction:
                    status = MitigationRetestStatus.CONFIRMED_REACTION
                elif status != MitigationRetestStatus.FAILED:
                    status = MitigationRetestStatus.RETEST_NO_REACTION
                return _retest(status, candle, "bearish_mitigation_retest", reaction, reaction_type)
    return _retest(MitigationRetestStatus.FRESH, None, "no_retest_after_structure_shift", False, "none")


def _bullish_retest_depth(candle: Mapping[str, Any], candidate: Mapping[str, Any]) -> MitigationRetestStatus:
    if candle["low"] <= candidate["zone_low"] and candle["close"] > candidate["zone_low"]:
        return MitigationRetestStatus.DEEP_MITIGATION
    if candle["low"] <= candidate["mean_threshold"] and candle["low"] > candidate["zone_low"]:
        return MitigationRetestStatus.PARTIALLY_MITIGATED
    return MitigationRetestStatus.TOUCHED


def _bearish_retest_depth(candle: Mapping[str, Any], candidate: Mapping[str, Any]) -> MitigationRetestStatus:
    if candle["high"] >= candidate["zone_high"] and candle["close"] < candidate["zone_high"]:
        return MitigationRetestStatus.DEEP_MITIGATION
    if candle["high"] >= candidate["mean_threshold"] and candle["high"] < candidate["zone_high"]:
        return MitigationRetestStatus.PARTIALLY_MITIGATED
    return MitigationRetestStatus.TOUCHED


def _reaction(
    candle: Mapping[str, Any],
    direction: str,
    candidate: Mapping[str, Any],
    structure_events: Sequence[Mapping[str, Any]],
) -> tuple[bool, str]:
    if direction == "bullish":
        if candle["close"] > candle["open"] and candle["close"] > candidate["mean_threshold"]:
            return True, "bullish_close_above_mean_threshold"
        if candle["close"] > candidate["zone_high"]:
            return True, "bullish_close_back_above_zone_high"
    else:
        if candle["close"] < candle["open"] and candle["close"] < candidate["mean_threshold"]:
            return True, "bearish_close_below_mean_threshold"
        if candle["close"] < candidate["zone_low"]:
            return True, "bearish_close_back_below_zone_low"
    for event in structure_events:
        if event["direction"] == direction and event["confirmation_candle_index"] >= int(candle["index"]):
            return True, f"{direction}_structure_confirmation_after_retest"
    return False, "none"


def _retest(
    status: MitigationRetestStatus,
    candle: Mapping[str, Any] | None,
    mitigation_depth: str,
    reaction_confirmed: bool,
    reaction_type: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "candle_index": int(candle["index"]) if candle else None,
        "mitigation_depth": mitigation_depth,
        "reaction_confirmed": reaction_confirmed,
        "reaction_type": reaction_type,
    }


def _quality_score(
    structure: Mapping[str, Any],
    candidate: Mapping[str, Any],
    retest: Mapping[str, Any],
    created_after_sweep: bool,
    fvg_confluence: bool,
    ob_confluence: bool,
    pd_alignment: bool,
    htf_alignment: str,
    target: str,
) -> tuple[float, list[str], list[str]]:
    reasons = ["mitigation_block_is_secondary_poi_after_structure_shift"]
    warnings = ["mitigation_block_is_subjective_use_conservative_confirmation"]
    score = 0.0
    score_cap = 10.0

    if structure["event_type"] == "MSS":
        score += 2.0 if created_after_sweep else 1.5
        reasons.append("mss_shift_preceded_mitigation_logic")
    elif structure["event_type"] == "BOS":
        score += 1.0
        reasons.append("bos_shift_preceded_mitigation_logic")

    if structure["displacement_strength"] == "strong":
        score += 1.5
        reasons.append("strong_displacement_after_structure_shift")
    elif structure["displacement_strength"] == "moderate":
        score += 0.75
        reasons.append("moderate_displacement_after_structure_shift")
    else:
        warnings.append("no_clear_displacement")
        score_cap = min(score_cap, 6.0)

    score += 1.0
    reasons.append("clear_opposite_side_candle_before_structure_shift")

    if created_after_sweep:
        score += 1.0
        reasons.append("matching_liquidity_sweep_before_structure_shift")
    else:
        warnings.append("no_matching_liquidity_sweep_context")
        score_cap = min(score_cap, 4.0)

    if fvg_confluence and ob_confluence:
        score += 1.0
        reasons.append("fvg_and_ob_or_poi_confluence")
    elif fvg_confluence or ob_confluence:
        score += 0.5
        reasons.append("partial_fvg_or_ob_confluence")
    else:
        warnings.append("no_fvg_or_ob_confluence")

    if pd_alignment:
        score += 1.0
        reasons.append("premium_discount_alignment_supports_direction")
    else:
        warnings.append("premium_discount_alignment_missing_or_unclear")

    if retest["status"] == MitigationRetestStatus.CONFIRMED_REACTION:
        score += 1.5
        reasons.append("retest_confirmed_reaction_in_new_structure_direction")
    elif retest["status"] == MitigationRetestStatus.FAILED:
        warnings.append("mitigation_block_failed")
        score_cap = min(score_cap, 3.0)
    elif retest["status"] == MitigationRetestStatus.FRESH:
        warnings.append("mitigation_candidate_not_retested")
        score_cap = min(score_cap, 6.0)
    else:
        score += 0.5
        warnings.append("retest_without_reaction")
        score_cap = min(score_cap, 6.0)

    if target != "none":
        score += 0.75
        reasons.append("target_liquidity_reference_available")
    else:
        warnings.append("no_target_liquidity_reference")
        score -= 0.5

    if htf_alignment == "aligned":
        score += 0.75
        reasons.append("htf_bias_aligned")
    elif htf_alignment == "neutral":
        score += 0.4
    elif htf_alignment == "against":
        warnings.append("htf_bias_against_mitigation_direction")
        score -= 1.0

    if float(candidate["zone_size_atr"]) > 2.0:
        warnings.append("mitigation_zone_is_wide_relative_to_atr")
        score -= 0.5

    return max(0.0, min(score_cap, round(score, 2))), reasons, warnings


def _quality_grade(score: float) -> MitigationQualityGrade:
    if score >= 9.0:
        return MitigationQualityGrade.HIGH_QUALITY
    if score >= 7.0:
        return MitigationQualityGrade.STRONG
    if score >= 5.0:
        return MitigationQualityGrade.MODERATE
    if score >= 3.0:
        return MitigationQualityGrade.WEAK
    return MitigationQualityGrade.INVALID


def _has_matching_sweep(
    sweeps: Sequence[Mapping[str, Any]],
    direction: str,
    confirmation_index: int,
) -> tuple[bool, str]:
    wanted = "sell_side" if direction == "bullish" else "buy_side"
    for sweep in sorted(sweeps, key=lambda item: item["index"], reverse=True):
        if sweep["index"] <= confirmation_index and wanted in sweep["sweep_type"]:
            return True, str(sweep["sweep_type"])
    return False, "none"


def _has_fvg_confluence(
    fvg_events: Sequence[Mapping[str, Any]],
    direction: str,
    confirmation_index: int,
    candidate: Mapping[str, Any],
) -> bool:
    for event in fvg_events:
        if event["direction"] not in {direction, "unknown"}:
            continue
        if event["index"] >= confirmation_index or _zones_overlap(event, candidate):
            return True
    return False


def _has_zone_confluence(
    zones: Sequence[Mapping[str, Any]],
    direction: str,
    candidate: Mapping[str, Any],
) -> bool:
    for zone in zones:
        if zone["direction"] not in {direction, "unknown"}:
            continue
        if _zones_overlap(zone, candidate):
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


def _zones_overlap(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_high = _float(left.get("zone_high"))
    left_low = _float(left.get("zone_low"))
    right_high = _float(right.get("zone_high"))
    right_low = _float(right.get("zone_low"))
    if None in {left_high, left_low, right_high, right_low}:
        return False
    return max(left_low, right_low) <= min(left_high, right_high)


def _structure_event(event: Mapping[str, Any] | str) -> dict[str, Any] | None:
    text = _event_text(event).lower()
    direction = "bullish" if "bullish" in text else "bearish" if "bearish" in text else None
    if direction is None:
        return None
    event_type = str(_field(event, "event_type", "type", default="MSS" if "mss" in text else "BOS" if "bos" in text else "UNKNOWN")).upper()
    index = _int_field(event, "confirmation_candle_index", "confirmation_index", "index", "candle_index")
    if index is None:
        return None
    displacement = str(_field(event, "displacement_strength", default="strong" if "strong" in text else "moderate" if "moderate" in text else "none")).lower()
    close_confirmed = bool(_field(event, "close_confirmed", default=True))
    wick_only = bool(_field(event, "wick_only", "wick_only_break", default=False))
    return {
        "direction": direction,
        "event_type": event_type,
        "confirmation_candle_index": index,
        "position": -1,
        "broken_level": _float(_field(event, "broken_level", default=None)),
        "displacement_strength": displacement,
        "close_confirmed": close_confirmed,
        "wick_only": wick_only,
    }


def _liquidity_event(event: Mapping[str, Any] | str) -> dict[str, Any] | None:
    text = _event_text(event).lower()
    sweep_type = "sell_side_liquidity_sweep" if "sell" in text else "buy_side_liquidity_sweep" if "buy" in text else "unknown"
    index = _int_field(event, "sweep_candle_index", "confirmation_candle_index", "index", "candle_index")
    if index is None:
        return None
    return {"sweep_type": sweep_type, "index": index}


def _context_event(event: Mapping[str, Any] | str) -> dict[str, Any] | None:
    text = _event_text(event).lower()
    direction = "bullish" if "bullish" in text else "bearish" if "bearish" in text else "unknown"
    index = _int_field(event, "created_index", "confirmation_candle_index", "index", "candle_index")
    if index is None:
        return None
    return {
        "direction": direction,
        "index": index,
        "zone_high": _float(_field(event, "zone_high", "high", default=None)),
        "zone_low": _float(_field(event, "zone_low", "low", default=None)),
    }


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


def _position_for_index(candles: Sequence[dict[str, Any]], index: int) -> int | None:
    for position, candle in enumerate(candles):
        if candle["index"] == index:
            return position
    return None


def _normalize_candles(candles: Sequence[CandleNode | Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for position, raw in enumerate(candles):
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
