"""Rule-based ICT/SMC displacement detection.

Displacement measures aggressive directional price delivery. It is useful as
confirmation for BOS, MSS, CHoCH, FVG, order blocks, liquidity-sweep reversals,
and continuation logic, but it is not an entry signal by itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from src.core.domain.market_data import CandleNode


class DisplacementDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class DisplacementMode(str, Enum):
    SINGLE_CANDLE = "single_candle"
    MULTI_CANDLE = "multi_candle"


class DisplacementStrengthLabel(str, Enum):
    WEAK_OR_NONE = "weak_or_none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    VERY_STRONG = "very_strong"


class AggressionEstimate(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class DisplacementEvent:
    concept_name: str
    symbol: str
    timeframe: str
    displacement_id: str
    direction: DisplacementDirection
    displacement_mode: DisplacementMode
    start_index: int
    end_index: int
    start_timestamp: datetime
    end_timestamp: datetime
    candle_count: int
    strength_score: float
    strength_label: DisplacementStrengthLabel
    institutional_aggression_estimate: AggressionEstimate
    fvg_created: bool
    structure_broken: bool
    liquidity_sweep_before: bool
    structure_event_type: str
    broken_level: float | None
    sweep_type: str
    fvg_reference: dict[str, Any] | None
    metrics: dict[str, Any]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    entry_allowed_from_displacement_alone: bool

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["direction"] = self.direction.value
        payload["displacement_mode"] = self.displacement_mode.value
        payload["strength_label"] = self.strength_label.value
        payload["institutional_aggression_estimate"] = self.institutional_aggression_estimate.value
        return payload


def detect_displacement(
    df: Sequence[CandleNode | Mapping[str, Any]] | Any,
    atr_period: int = 14,
    multiplier: float = 1.5,
    *,
    structure_events: Sequence[Mapping[str, Any] | str] | None = None,
    liquidity_sweeps: Sequence[Mapping[str, Any] | str] | None = None,
    fvg_events: Sequence[Mapping[str, Any]] | None = None,
    symbol: str = "unknown",
    timeframe: str = "unknown",
    average_body_period: int | None = None,
    average_range_period: int | None = None,
    body_ratio_threshold: float = 0.55,
    close_position_threshold: float = 0.70,
    max_sequence_length: int = 5,
    pullback_threshold: float = 0.35,
    include_weak: bool = False,
) -> list[dict[str, Any]]:
    """Detect aggressive bullish or bearish displacement events from OHLCV data."""
    candles = _normalize_candles(df)
    if len(candles) < 2:
        return []

    average_body_period = average_body_period or atr_period
    average_range_period = average_range_period or atr_period
    atr_values = _atr_values(candles, atr_period)
    avg_bodies = _rolling_average([candle["body"] for candle in candles], average_body_period)
    avg_ranges = _rolling_average([candle["range"] for candle in candles], average_range_period)
    structures = [event for event in (_structure_event(item) for item in (structure_events or ())) if event is not None]
    sweeps = [event for event in (_liquidity_event(item) for item in (liquidity_sweeps or ())) if event is not None]
    external_fvgs = [zone for zone in (_fvg_event(item) for item in (fvg_events or ())) if zone is not None]

    events: list[DisplacementEvent] = []
    for position, candle in enumerate(candles):
        for direction in (DisplacementDirection.BULLISH, DisplacementDirection.BEARISH):
            event = _single_candle_event(
                candles,
                position,
                direction,
                atr_values[position],
                avg_bodies[position],
                avg_ranges[position],
                multiplier,
                body_ratio_threshold,
                close_position_threshold,
                structures,
                sweeps,
                external_fvgs,
                symbol,
                timeframe,
            )
            if event is not None and (include_weak or event.strength_score >= 5.0):
                events.append(event)

    for start in range(0, len(candles) - 1):
        for length in range(2, min(max_sequence_length, len(candles) - start) + 1):
            window = candles[start : start + length]
            direction = _sequence_direction(window)
            if direction is None:
                continue
            event = _multi_candle_event(
                candles,
                start,
                start + length - 1,
                direction,
                atr_values[start + length - 1],
                avg_bodies[start + length - 1],
                avg_ranges[start + length - 1],
                multiplier,
                body_ratio_threshold,
                close_position_threshold,
                pullback_threshold,
                structures,
                sweeps,
                external_fvgs,
                symbol,
                timeframe,
            )
            if event is not None and (include_weak or event.strength_score >= 5.0):
                events.append(event)

    return [event.as_dict() for event in _dedupe_events(events)]


def _single_candle_event(
    candles: Sequence[dict[str, Any]],
    position: int,
    direction: DisplacementDirection,
    atr: float,
    average_body: float,
    average_range: float,
    multiplier: float,
    body_threshold: float,
    close_threshold: float,
    structures: Sequence[Mapping[str, Any]],
    sweeps: Sequence[Mapping[str, Any]],
    external_fvgs: Sequence[Mapping[str, Any]],
    symbol: str,
    timeframe: str,
) -> DisplacementEvent | None:
    candle = candles[position]
    if not _is_directional(candle, direction):
        return None
    metrics = _single_metrics(candle, atr, average_body, average_range, direction)
    expansion_ok = (
        metrics["range_to_atr_ratio"] >= multiplier
        or metrics["range_to_average_range_ratio"] >= multiplier
        or metrics["body_to_average_body_ratio"] >= multiplier
    )
    if metrics["body_to_range_ratio"] < body_threshold or metrics["close_position_ratio"] < close_threshold or not expansion_ok:
        if metrics["range_to_atr_ratio"] >= multiplier and metrics["body_to_range_ratio"] < 0.30:
            return _weak_wick_event(candles, position, direction, metrics, symbol, timeframe)
        return None
    context = _context(candles, position, position, direction, structures, sweeps, external_fvgs)
    score, reasons, warnings = _score_single(metrics, context, direction)
    return _event(
        candles,
        position,
        position,
        direction,
        DisplacementMode.SINGLE_CANDLE,
        metrics,
        context,
        score,
        reasons,
        warnings,
        symbol,
        timeframe,
    )


def _multi_candle_event(
    candles: Sequence[dict[str, Any]],
    start: int,
    end: int,
    direction: DisplacementDirection,
    atr: float,
    average_body: float,
    average_range: float,
    multiplier: float,
    body_threshold: float,
    close_threshold: float,
    pullback_threshold: float,
    structures: Sequence[Mapping[str, Any]],
    sweeps: Sequence[Mapping[str, Any]],
    external_fvgs: Sequence[Mapping[str, Any]],
    symbol: str,
    timeframe: str,
) -> DisplacementEvent | None:
    window = candles[start : end + 1]
    metrics = _sequence_metrics(window, atr, average_body, average_range, direction)
    if metrics["directional_candle_ratio"] < 0.60:
        return None
    if metrics["cumulative_range_to_atr_ratio"] < multiplier * 2.0:
        return None
    if metrics["cumulative_body_to_average_body_ratio"] < multiplier:
        return None
    if metrics["max_pullback_ratio"] > pullback_threshold:
        return None
    if metrics["sequence_close_position_ratio"] < close_threshold:
        return None
    if metrics["body_to_range_ratio"] < body_threshold:
        return None
    context = _context(candles, start, end, direction, structures, sweeps, external_fvgs)
    score, reasons, warnings = _score_multi(metrics, context, direction)
    return _event(
        candles,
        start,
        end,
        direction,
        DisplacementMode.MULTI_CANDLE,
        metrics,
        context,
        score,
        reasons,
        warnings,
        symbol,
        timeframe,
    )


def _weak_wick_event(
    candles: Sequence[dict[str, Any]],
    position: int,
    direction: DisplacementDirection,
    metrics: Mapping[str, Any],
    symbol: str,
    timeframe: str,
) -> DisplacementEvent:
    warnings = (
        "large_range_without_body_dominance",
        "close_location_does_not_confirm_directional_control",
        "displacement_confirms_aggression_but_is_not_entry_signal",
    )
    return _event(
        candles,
        position,
        position,
        direction,
        DisplacementMode.SINGLE_CANDLE,
        dict(metrics),
        _empty_context(),
        2.5,
        ("large_range_detected_without_clean_displacement",),
        list(warnings),
        symbol,
        timeframe,
    )


def _event(
    candles: Sequence[dict[str, Any]],
    start: int,
    end: int,
    direction: DisplacementDirection,
    mode: DisplacementMode,
    metrics: Mapping[str, Any],
    context: Mapping[str, Any],
    score: float,
    reasons: Sequence[str],
    warnings: Sequence[str],
    symbol: str,
    timeframe: str,
) -> DisplacementEvent:
    start_candle = candles[start]
    end_candle = candles[end]
    return DisplacementEvent(
        concept_name="ict_smc_displacement",
        symbol=symbol,
        timeframe=timeframe,
        displacement_id=f"DISP_{timeframe}_{direction.value}_{mode.value}_{start_candle['index']}_{end_candle['index']}",
        direction=direction,
        displacement_mode=mode,
        start_index=int(start_candle["index"]),
        end_index=int(end_candle["index"]),
        start_timestamp=start_candle["timestamp"],
        end_timestamp=end_candle["timestamp"],
        candle_count=end - start + 1,
        strength_score=round(_clamp(score, 0.0, 10.0), 2),
        strength_label=_strength_label(score),
        institutional_aggression_estimate=_aggression(score),
        fvg_created=bool(context["fvg_created"]),
        structure_broken=bool(context["structure_broken"]),
        liquidity_sweep_before=bool(context["liquidity_sweep_before"]),
        structure_event_type=str(context["structure_event_type"]),
        broken_level=context["broken_level"],
        sweep_type=str(context["sweep_type"]),
        fvg_reference=context["fvg_reference"],
        metrics=dict(metrics),
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        entry_allowed_from_displacement_alone=False,
    )


def _score_single(
    metrics: Mapping[str, Any],
    context: Mapping[str, Any],
    direction: DisplacementDirection,
) -> tuple[float, list[str], list[str]]:
    score = 0.0
    reasons: list[str] = []
    warnings = ["displacement_confirms_aggression_but_is_not_entry_signal"]

    body_ratio = metrics["body_to_range_ratio"]
    if body_ratio >= 0.80:
        score += 2.5
        reasons.append("very_strong_body_dominance")
    elif body_ratio >= 0.65:
        score += 2.0
        reasons.append("strong_body_dominance")
    elif body_ratio >= 0.55:
        score += 1.25
        reasons.append("acceptable_body_dominance")

    if metrics["range_to_atr_ratio"] >= 2.0:
        score += 2.0
        reasons.append("very_strong_range_expansion_vs_atr")
    elif metrics["range_to_atr_ratio"] >= 1.5:
        score += 1.75
        reasons.append("strong_range_expansion_vs_atr")
    elif metrics["range_to_atr_ratio"] >= 1.2:
        score += 1.0
        reasons.append("moderate_range_expansion_vs_atr")

    if metrics["body_to_average_body_ratio"] >= 2.0:
        score += 1.25
        reasons.append("body_expanded_above_recent_average")
    elif metrics["body_to_average_body_ratio"] >= 1.5:
        score += 1.0
        reasons.append("body_expanded_vs_recent_average")

    if metrics["close_position_ratio"] >= 0.90:
        score += 1.25
        reasons.append(f"{direction.value}_close_very_near_extreme")
    elif metrics["close_position_ratio"] >= 0.80:
        score += 1.0
        reasons.append(f"{direction.value}_close_near_extreme")
    elif metrics["close_position_ratio"] >= 0.70:
        score += 0.75
        reasons.append(f"{direction.value}_close_in_directional_zone")

    score = _add_context_score(score, reasons, warnings, context, direction)
    if not context["structure_broken"] and not context["fvg_created"]:
        score = min(score, 6.5)
        warnings.append("displacement_without_structure_break_or_fvg_is_confirmation_only")
    if body_ratio < 0.30:
        score = min(score, 3.0)
        warnings.append("large_range_without_body_dominance")
    return score, reasons, warnings


def _score_multi(
    metrics: Mapping[str, Any],
    context: Mapping[str, Any],
    direction: DisplacementDirection,
) -> tuple[float, list[str], list[str]]:
    score = 1.0
    reasons = [f"{direction.value}_candle_sequence_moved_aggressively"]
    warnings = [
        "multi_candle_displacement_should_be_cross_checked_with_structure_and_fvg_modules",
        "displacement_confirms_aggression_but_is_not_entry_signal",
    ]
    if metrics["directional_candle_ratio"] >= 0.75:
        score += 1.5
        reasons.append("majority_of_sequence_closed_in_displacement_direction")
    if metrics["cumulative_range_to_atr_ratio"] >= 3.0:
        score += 2.0
        reasons.append("cumulative_range_was_large_vs_atr")
    if metrics["cumulative_body_to_average_body_ratio"] >= 2.0:
        score += 1.25
        reasons.append("cumulative_body_expanded_vs_average")
    if metrics["sequence_close_position_ratio"] >= 0.80:
        score += 1.25
        reasons.append("sequence_closed_near_directional_extreme")
    if metrics["max_pullback_ratio"] <= 0.25:
        score += 0.75
        reasons.append("sequence_pullbacks_were_shallow")
    score = _add_context_score(score, reasons, warnings, context, direction)
    return score, reasons, warnings


def _add_context_score(
    score: float,
    reasons: list[str],
    warnings: list[str],
    context: Mapping[str, Any],
    direction: DisplacementDirection,
) -> float:
    if context["liquidity_sweep_before"]:
        score += 0.75
        reasons.append(f"displacement_followed_{context['sweep_type']}")
    if context["structure_broken"]:
        score += 1.25
        reasons.append(f"{context['structure_event_type']}_confirmed")
    else:
        warnings.append("no_structure_break_confirmation")
    if context["fvg_created"]:
        score += 1.0
        reasons.append(f"{direction.value}_fvg_created")
    else:
        warnings.append("no_fvg_created")
    return score


def _context(
    candles: Sequence[dict[str, Any]],
    start: int,
    end: int,
    direction: DisplacementDirection,
    structures: Sequence[Mapping[str, Any]],
    sweeps: Sequence[Mapping[str, Any]],
    external_fvgs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    start_index = int(candles[start]["index"])
    end_index = int(candles[end]["index"])
    structure = _matching_structure(structures, direction, start_index, end_index)
    sweep = _matching_sweep(sweeps, direction, start_index)
    fvg = _matching_fvg(candles, start, end, direction, external_fvgs)
    return {
        "structure_broken": structure is not None,
        "structure_event_type": "none" if structure is None else f"{direction.value}_{structure['event_type']}",
        "broken_level": None if structure is None else structure["broken_level"],
        "liquidity_sweep_before": sweep is not None,
        "sweep_type": "none" if sweep is None else sweep["sweep_type"],
        "fvg_created": fvg is not None,
        "fvg_reference": fvg,
    }


def _empty_context() -> dict[str, Any]:
    return {
        "structure_broken": False,
        "structure_event_type": "none",
        "broken_level": None,
        "liquidity_sweep_before": False,
        "sweep_type": "none",
        "fvg_created": False,
        "fvg_reference": None,
    }


def _matching_structure(
    structures: Sequence[Mapping[str, Any]],
    direction: DisplacementDirection,
    start_index: int,
    end_index: int,
) -> Mapping[str, Any] | None:
    for event in sorted(structures, key=lambda item: item["confirmation_candle_index"], reverse=True):
        if event["direction"] == direction.value and start_index <= event["confirmation_candle_index"] <= end_index:
            return event
    return None


def _matching_sweep(
    sweeps: Sequence[Mapping[str, Any]],
    direction: DisplacementDirection,
    start_index: int,
) -> Mapping[str, Any] | None:
    wanted = "sell_side" if direction == DisplacementDirection.BULLISH else "buy_side"
    for sweep in sorted(sweeps, key=lambda item: item["index"], reverse=True):
        if sweep["index"] <= start_index and wanted in sweep["sweep_type"]:
            return sweep
    return None


def _matching_fvg(
    candles: Sequence[dict[str, Any]],
    start: int,
    end: int,
    direction: DisplacementDirection,
    external_fvgs: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    for fvg in external_fvgs:
        if fvg["direction"] == direction.value and start <= fvg["index"] <= end:
            return dict(fvg)
    for position in range(max(1, start), min(end, len(candles) - 2) + 1):
        c1, c3 = candles[position - 1], candles[position + 1]
        if direction == DisplacementDirection.BULLISH and c1["high"] < c3["low"]:
            return {"direction": "bullish", "index": int(candles[position]["index"]), "fvg_type": "bullish_fvg", "fvg_zone_low": c1["high"], "fvg_zone_high": c3["low"]}
        if direction == DisplacementDirection.BEARISH and c1["low"] > c3["high"]:
            return {"direction": "bearish", "index": int(candles[position]["index"]), "fvg_type": "bearish_fvg", "fvg_zone_low": c3["high"], "fvg_zone_high": c1["low"]}
    return None


def _single_metrics(
    candle: Mapping[str, Any],
    atr: float,
    average_body: float,
    average_range: float,
    direction: DisplacementDirection,
) -> dict[str, Any]:
    close_position = _close_position_ratio(candle, direction)
    return {
        "candle_body": round(candle["body"], 5),
        "candle_range": round(candle["range"], 5),
        "body_to_range_ratio": round(candle["body"] / max(candle["range"], 1e-9), 3),
        "atr": round(atr, 5),
        "range_to_atr_ratio": round(candle["range"] / max(atr, 1e-9), 3),
        "average_body": round(average_body, 5),
        "body_to_average_body_ratio": round(candle["body"] / max(average_body, 1e-9), 3),
        "average_range": round(average_range, 5),
        "range_to_average_range_ratio": round(candle["range"] / max(average_range, 1e-9), 3),
        "close_position_ratio": round(close_position, 3),
    }


def _sequence_metrics(
    window: Sequence[Mapping[str, Any]],
    atr: float,
    average_body: float,
    average_range: float,
    direction: DisplacementDirection,
) -> dict[str, Any]:
    directional_count = sum(1 for candle in window if _is_directional(candle, direction))
    cumulative_body = sum(candle["body"] for candle in window)
    sequence_high = max(candle["high"] for candle in window)
    sequence_low = min(candle["low"] for candle in window)
    cumulative_range = sequence_high - sequence_low
    sequence_body = abs(window[-1]["close"] - window[0]["open"])
    close_position = (window[-1]["close"] - sequence_low) / max(cumulative_range, 1e-9)
    if direction == DisplacementDirection.BEARISH:
        close_position = (sequence_high - window[-1]["close"]) / max(cumulative_range, 1e-9)
    return {
        "directional_candle_count": directional_count,
        "directional_candle_ratio": round(directional_count / len(window), 3),
        "cumulative_body": round(cumulative_body, 5),
        "cumulative_range": round(cumulative_range, 5),
        "body_to_range_ratio": round(sequence_body / max(cumulative_range, 1e-9), 3),
        "atr": round(atr, 5),
        "cumulative_range_to_atr_ratio": round(cumulative_range / max(atr, 1e-9), 3),
        "average_body": round(average_body, 5),
        "cumulative_body_to_average_body_ratio": round(cumulative_body / max(average_body, 1e-9), 3),
        "average_range": round(average_range, 5),
        "cumulative_range_to_average_range_ratio": round(cumulative_range / max(average_range, 1e-9), 3),
        "sequence_close_position_ratio": round(close_position, 3),
        "max_pullback_ratio": round(_max_pullback_ratio(window, direction), 3),
    }


def _is_directional(candle: Mapping[str, Any], direction: DisplacementDirection) -> bool:
    return candle["close"] > candle["open"] if direction == DisplacementDirection.BULLISH else candle["close"] < candle["open"]


def _sequence_direction(window: Sequence[Mapping[str, Any]]) -> DisplacementDirection | None:
    bullish = sum(1 for candle in window if candle["close"] > candle["open"])
    bearish = sum(1 for candle in window if candle["close"] < candle["open"])
    if bullish / len(window) >= 0.60 and window[-1]["close"] > window[0]["open"]:
        return DisplacementDirection.BULLISH
    if bearish / len(window) >= 0.60 and window[-1]["close"] < window[0]["open"]:
        return DisplacementDirection.BEARISH
    return None


def _close_position_ratio(candle: Mapping[str, Any], direction: DisplacementDirection) -> float:
    if direction == DisplacementDirection.BULLISH:
        return (candle["close"] - candle["low"]) / max(candle["range"], 1e-9)
    return (candle["high"] - candle["close"]) / max(candle["range"], 1e-9)


def _max_pullback_ratio(window: Sequence[Mapping[str, Any]], direction: DisplacementDirection) -> float:
    total_move = abs(window[-1]["close"] - window[0]["open"])
    if total_move <= 1e-9:
        return 1.0
    worst = 0.0
    previous_close = window[0]["close"]
    for candle in window[1:]:
        adverse = max(0.0, previous_close - candle["low"]) if direction == DisplacementDirection.BULLISH else max(0.0, candle["high"] - previous_close)
        worst = max(worst, adverse / total_move)
        previous_close = candle["close"]
    return worst


def _dedupe_events(events: Sequence[DisplacementEvent]) -> list[DisplacementEvent]:
    winners: dict[tuple[str, int, int], DisplacementEvent] = {}
    for event in events:
        key = (event.direction.value, event.start_index, event.end_index)
        if key not in winners or event.strength_score > winners[key].strength_score:
            winners[key] = event
    return sorted(winners.values(), key=lambda item: (item.strength_score, item.end_index), reverse=True)


def _strength_label(score: float) -> DisplacementStrengthLabel:
    if score >= 9:
        return DisplacementStrengthLabel.VERY_STRONG
    if score >= 7:
        return DisplacementStrengthLabel.STRONG
    if score >= 5:
        return DisplacementStrengthLabel.MODERATE
    if score >= 3:
        return DisplacementStrengthLabel.WEAK
    return DisplacementStrengthLabel.WEAK_OR_NONE


def _aggression(score: float) -> AggressionEstimate:
    if score >= 7:
        return AggressionEstimate.HIGH
    if score >= 5:
        return AggressionEstimate.MODERATE
    return AggressionEstimate.LOW


def _structure_event(event: Mapping[str, Any] | str) -> dict[str, Any] | None:
    text = _event_text(event).lower()
    direction = "bullish" if "bullish" in text else "bearish" if "bearish" in text else None
    if direction is None:
        return None
    event_type = str(_field(event, "event_type", "type", default="CHOCH" if "choch" in text else "MSS" if "mss" in text else "BOS" if "bos" in text else "UNKNOWN")).upper()
    index = _int_field(event, "confirmation_candle_index", "confirmation_index", "index", "candle_index")
    if index is None:
        return None
    broken_level = _float(_field(event, "broken_level", "level", "swing_level", default=None))
    return {"direction": direction, "event_type": event_type, "confirmation_candle_index": index, "broken_level": broken_level}


def _liquidity_event(event: Mapping[str, Any] | str) -> dict[str, Any] | None:
    text = _event_text(event).lower()
    sweep_type = "sell_side_liquidity_sweep" if "sell" in text else "buy_side_liquidity_sweep" if "buy" in text else "unknown"
    index = _int_field(event, "sweep_candle_index", "confirmation_candle_index", "index", "candle_index")
    return None if index is None else {"sweep_type": sweep_type, "index": index}


def _fvg_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    direction = str(event.get("direction", "unknown")).lower()
    if direction not in {"bullish", "bearish"}:
        fvg_type = str(event.get("fvg_type", event.get("type", ""))).lower()
        direction = "bullish" if "bullish" in fvg_type else "bearish" if "bearish" in fvg_type else "unknown"
    index = _int_field(event, "index", "creation_index", "candle_index", "fvg_creation_index")
    if direction == "unknown" or index is None:
        return None
    return {
        "direction": direction,
        "index": index,
        "fvg_type": f"{direction}_fvg",
        "fvg_zone_low": _float(event.get("zone_low", event.get("fvg_zone_low"))),
        "fvg_zone_high": _float(event.get("zone_high", event.get("fvg_zone_high"))),
    }


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
        high = float(values["high"])
        low = float(values["low"])
        open_ = float(values["open"])
        close = float(values["close"])
        normalized.append(
            {
                "position": len(normalized),
                "index": int(values.get("index", position)),
                "timestamp": timestamp,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": float(values.get("volume", 0.0)),
                "range": max(high - low, 1e-9),
                "body": abs(close - open_),
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


def _rolling_average(values: Sequence[float], period: int) -> list[float]:
    averages: list[float] = []
    for position, _ in enumerate(values):
        window = values[max(0, position - period) : position + 1]
        averages.append(sum(window) / max(len(window), 1))
    return averages


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
