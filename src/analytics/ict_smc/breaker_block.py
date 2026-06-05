"""ICT/SMC breaker block detection.

A breaker block is a failed order block that flips into a reaction zone in the
opposite direction. This module detects acceptance beyond the failed OB,
approximates trapped-trader context, classifies retests, and scores confidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from src.core.domain.market_data import CandleNode


class BreakerType(str, Enum):
    BULLISH = "bullish_breaker"
    BEARISH = "bearish_breaker"
    BULLISH_ATTEMPT = "bullish_breaker_attempt"
    BEARISH_ATTEMPT = "bearish_breaker_attempt"


class BreakerRetestStatus(str, Enum):
    NOT_RETESTED = "not_retested"
    TOUCHED = "touched"
    MEAN_THRESHOLD_RETEST = "mean_threshold_retest"
    DEEP_RETEST = "deep_retest"
    CONFIRMED_REACTION = "confirmed_reaction"
    WICK_ONLY_FAILURE_ATTEMPT = "wick_only_failure_attempt"
    FAILED = "failed"


class BreakerConfidenceGrade(str, Enum):
    INVALID = "invalid"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True, slots=True)
class BreakerBlock:
    concept_name: str
    symbol: str
    timeframe: str
    breaker_id: str
    breaker_type: BreakerType
    direction: str
    original_ob_id: str
    original_ob_direction: str
    zone_high: float
    zone_low: float
    mean_threshold: float
    failed_at_index: int | None
    failed_at_timestamp: datetime | None
    retest_status: BreakerRetestStatus
    confidence_score: float
    confidence_grade: BreakerConfidenceGrade
    confirmed_breaker: bool
    original_ob_quality: float
    acceptance_close: float | None
    acceptance_type: str
    displacement_through_failed_ob: bool
    displacement_strength: str
    structure_event_after_failure: bool
    structure_event_type: str
    retest_candle_index: int | None
    reaction_confirmed: bool
    reaction_type: str
    trapped_side: str
    invalidation_level: float
    entry_allowed_from_breaker_alone: bool
    entry_allowed_after_reaction: bool
    target_liquidity_reference: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["breaker_type"] = self.breaker_type.value
        payload["retest_status"] = self.retest_status.value
        payload["confidence_grade"] = self.confidence_grade.value
        return payload


def detect_breaker_blocks(
    df: Sequence[CandleNode | Mapping[str, Any]],
    order_blocks: Sequence[Mapping[str, Any]],
    structure_events: Sequence[Mapping[str, Any] | str] | None = None,
    *,
    symbol: str = "unknown",
    timeframe: str = "unknown",
    break_buffer: float = 0.05,
    invalidation_buffer: float = 0.05,
    atr_period: int = 14,
) -> list[dict[str, Any]]:
    """Detect breaker blocks from failed order blocks using closed candles only."""
    candles = _normalize_candles(df)
    if not candles:
        return []
    atr_values = _atr_values(candles, atr_period)
    structures = [_structure_event(event) for event in (structure_events or ())]
    structures = [event for event in structures if event is not None]
    breakers: list[dict[str, Any]] = []

    for raw_ob in order_blocks:
        ob = _normalize_order_block(raw_ob)
        if ob is None:
            continue
        if ob["direction"] == "bearish":
            breaker = _detect_from_bearish_ob(
                candles,
                atr_values,
                structures,
                ob,
                symbol,
                timeframe,
                break_buffer,
                invalidation_buffer,
            )
        elif ob["direction"] == "bullish":
            breaker = _detect_from_bullish_ob(
                candles,
                atr_values,
                structures,
                ob,
                symbol,
                timeframe,
                break_buffer,
                invalidation_buffer,
            )
        else:
            breaker = None
        if breaker is not None:
            breakers.append(breaker.as_dict())

    return sorted(
        breakers,
        key=lambda item: (item["confirmed_breaker"], item["confidence_score"], item.get("failed_at_index") or -1),
        reverse=True,
    )


def _detect_from_bearish_ob(
    candles: Sequence[dict[str, Any]],
    atr_values: Sequence[float],
    structures: Sequence[dict[str, Any]],
    ob: dict[str, Any],
    symbol: str,
    timeframe: str,
    break_buffer: float,
    invalidation_buffer: float,
) -> BreakerBlock | None:
    failure = _first_failure_or_wick_attempt(candles, ob, "bullish", break_buffer)
    if failure is None:
        return None
    candle, wick_only = failure
    return _build_breaker(
        candles,
        atr_values,
        structures,
        ob,
        candle,
        wick_only,
        BreakerType.BULLISH_ATTEMPT if wick_only else BreakerType.BULLISH,
        symbol,
        timeframe,
        break_buffer,
        invalidation_buffer,
    )


def _detect_from_bullish_ob(
    candles: Sequence[dict[str, Any]],
    atr_values: Sequence[float],
    structures: Sequence[dict[str, Any]],
    ob: dict[str, Any],
    symbol: str,
    timeframe: str,
    break_buffer: float,
    invalidation_buffer: float,
) -> BreakerBlock | None:
    failure = _first_failure_or_wick_attempt(candles, ob, "bearish", break_buffer)
    if failure is None:
        return None
    candle, wick_only = failure
    return _build_breaker(
        candles,
        atr_values,
        structures,
        ob,
        candle,
        wick_only,
        BreakerType.BEARISH_ATTEMPT if wick_only else BreakerType.BEARISH,
        symbol,
        timeframe,
        break_buffer,
        invalidation_buffer,
    )


def _build_breaker(
    candles: Sequence[dict[str, Any]],
    atr_values: Sequence[float],
    structures: Sequence[dict[str, Any]],
    ob: dict[str, Any],
    failure_candle: dict[str, Any],
    wick_only: bool,
    breaker_type: BreakerType,
    symbol: str,
    timeframe: str,
    break_buffer: float,
    invalidation_buffer: float,
) -> BreakerBlock:
    direction = "bullish" if breaker_type in {BreakerType.BULLISH, BreakerType.BULLISH_ATTEMPT} else "bearish"
    failure_index = int(failure_candle["index"])
    failure_position = int(failure_candle["position"])
    displacement = _displacement_snapshot(failure_candle, atr_values[failure_position], direction)
    structure = _structure_after_failure(structures, direction, failure_index)
    if wick_only:
        retest = _retest(
            BreakerRetestStatus.WICK_ONLY_FAILURE_ATTEMPT,
            failure_candle,
            "wick_only_no_acceptance_close_beyond_ob",
            False,
            "none",
        )
    else:
        retest = _classify_retest(candles, failure_position, ob, direction, invalidation_buffer, structures)
    confirmed = not wick_only
    score, reasons, warnings = _confidence_score(
        ob,
        direction,
        wick_only,
        displacement,
        structure,
        retest,
    )
    if wick_only:
        score = min(score, 3.0)
    if retest["status"] == BreakerRetestStatus.FAILED:
        score = min(score, 3.0)
    grade = _confidence_grade(score)
    original_direction = ob["direction"]
    trapped_side = "sellers" if direction == "bullish" else "buyers"
    breaker = BreakerBlock(
        concept_name="Breaker Block",
        symbol=symbol or str(ob.get("symbol", "unknown")),
        timeframe=timeframe or str(ob.get("timeframe", "unknown")),
        breaker_id=f"BRK_{timeframe}_{direction.upper()}_{failure_index}",
        breaker_type=breaker_type,
        direction=direction,
        original_ob_id=str(ob["ob_id"]),
        original_ob_direction=original_direction,
        zone_high=ob["zone_high"],
        zone_low=ob["zone_low"],
        mean_threshold=ob["mean_threshold"],
        failed_at_index=None if wick_only else failure_index,
        failed_at_timestamp=None if wick_only else failure_candle["timestamp"],
        retest_status=retest["status"],
        confidence_score=score,
        confidence_grade=grade,
        confirmed_breaker=confirmed,
        original_ob_quality=ob["quality_score"],
        acceptance_close=None if wick_only else failure_candle["close"],
        acceptance_type=_acceptance_type(wick_only, direction, displacement),
        displacement_through_failed_ob=displacement["present"],
        displacement_strength=displacement["strength"],
        structure_event_after_failure=structure is not None,
        structure_event_type=str(structure.get("event_type", "none")) if structure else "none",
        retest_candle_index=retest["candle_index"],
        reaction_confirmed=bool(retest["reaction_confirmed"]),
        reaction_type=str(retest["reaction_type"]),
        trapped_side=trapped_side,
        invalidation_level=ob["zone_low"] if direction == "bullish" else ob["zone_high"],
        entry_allowed_from_breaker_alone=False,
        entry_allowed_after_reaction=confirmed and bool(retest["reaction_confirmed"]) and retest["status"] != BreakerRetestStatus.FAILED,
        target_liquidity_reference=(
            "nearest_buy_side_liquidity_above" if direction == "bullish" else "nearest_sell_side_liquidity_below"
        ),
        reasons=tuple(dict.fromkeys(reasons)),
        warnings=tuple(dict.fromkeys(warnings)),
    )
    return breaker


def _first_failure_or_wick_attempt(
    candles: Sequence[dict[str, Any]],
    ob: Mapping[str, Any],
    new_direction: str,
    break_buffer: float,
) -> tuple[dict[str, Any], bool] | None:
    created_index = int(ob["created_index"])
    wick_attempt: tuple[dict[str, Any], bool] | None = None
    for candle in candles:
        if int(candle["index"]) <= created_index:
            continue
        if new_direction == "bullish":
            if candle["close"] > ob["zone_high"] + break_buffer:
                return candle, False
            if candle["high"] > ob["zone_high"] + break_buffer and candle["close"] <= ob["zone_high"] and wick_attempt is None:
                wick_attempt = candle, True
        else:
            if candle["close"] < ob["zone_low"] - break_buffer:
                return candle, False
            if candle["low"] < ob["zone_low"] - break_buffer and candle["close"] >= ob["zone_low"] and wick_attempt is None:
                wick_attempt = candle, True
    return wick_attempt


def _classify_retest(
    candles: Sequence[dict[str, Any]],
    failure_position: int,
    ob: Mapping[str, Any],
    direction: str,
    invalidation_buffer: float,
    structures: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    for candle in candles[failure_position + 1:]:
        if direction == "bullish":
            if candle["close"] < ob["zone_low"] - invalidation_buffer:
                return _retest(BreakerRetestStatus.FAILED, candle, "closed_below_bullish_breaker_zone", False, "none")
            if candle["low"] <= ob["zone_high"]:
                status = BreakerRetestStatus.TOUCHED
                if candle["low"] <= ob["zone_low"] and candle["close"] > ob["zone_low"]:
                    status = BreakerRetestStatus.DEEP_RETEST
                elif candle["low"] <= ob["mean_threshold"]:
                    status = BreakerRetestStatus.MEAN_THRESHOLD_RETEST
                reaction, reaction_type = _breaker_reaction(candle, direction, ob, structures)
                if reaction:
                    status = BreakerRetestStatus.CONFIRMED_REACTION
                return _retest(status, candle, "bullish_breaker_retest", reaction, reaction_type)
        else:
            if candle["close"] > ob["zone_high"] + invalidation_buffer:
                return _retest(BreakerRetestStatus.FAILED, candle, "closed_above_bearish_breaker_zone", False, "none")
            if candle["high"] >= ob["zone_low"]:
                status = BreakerRetestStatus.TOUCHED
                if candle["high"] >= ob["zone_high"] and candle["close"] < ob["zone_high"]:
                    status = BreakerRetestStatus.DEEP_RETEST
                elif candle["high"] >= ob["mean_threshold"]:
                    status = BreakerRetestStatus.MEAN_THRESHOLD_RETEST
                reaction, reaction_type = _breaker_reaction(candle, direction, ob, structures)
                if reaction:
                    status = BreakerRetestStatus.CONFIRMED_REACTION
                return _retest(status, candle, "bearish_breaker_retest", reaction, reaction_type)
    return _retest(BreakerRetestStatus.NOT_RETESTED, None, "no_retest_after_failure", False, "none")


def _retest(
    status: BreakerRetestStatus,
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


def _breaker_reaction(
    candle: Mapping[str, Any],
    direction: str,
    ob: Mapping[str, Any],
    structures: Sequence[dict[str, Any]],
) -> tuple[bool, str]:
    event = _structure_after_failure(structures, direction, int(candle["index"]))
    if event:
        return True, f"{direction}_structure_confirmation_after_breaker_retest"
    if direction == "bullish":
        if candle["close"] > ob["zone_high"] and candle["close"] > candle["open"]:
            return True, "bullish_close_back_above_breaker_zone_high"
        if candle["close"] > ob["mean_threshold"] and candle["close"] > candle["open"]:
            return True, "bullish_reaction_from_failed_bearish_ob"
    else:
        if candle["close"] < ob["zone_low"] and candle["close"] < candle["open"]:
            return True, "bearish_close_back_below_breaker_zone_low"
        if candle["close"] < ob["mean_threshold"] and candle["close"] < candle["open"]:
            return True, "bearish_rejection_from_failed_bullish_ob"
    return False, "none"


def _confidence_score(
    ob: Mapping[str, Any],
    direction: str,
    wick_only: bool,
    displacement: Mapping[str, Any],
    structure: Mapping[str, Any] | None,
    retest: Mapping[str, Any],
) -> tuple[float, list[str], list[str]]:
    reasons = ["breaker_is_failed_order_block_not_generic_sr_flip"]
    warnings = ["breaker_is_reaction_zone_not_automatic_entry"]
    score = 0.0
    score_cap = 10.0

    ob_quality = float(ob.get("quality_score", 0.0))
    if ob_quality >= 8.0:
        score += 2.0
        reasons.append("original_order_block_was_high_quality")
    elif ob_quality >= 5.0:
        score += 1.0
        reasons.append("original_order_block_was_moderate_quality")
    else:
        warnings.append("original_order_block_was_weak")

    if wick_only:
        warnings.append("no_acceptance_close_beyond_ob")
        return min(score + 0.5, 3.0), reasons, warnings

    score += 1.5
    reasons.append("price_closed_beyond_failed_order_block_with_buffer")

    if displacement["present"]:
        score += 1.5
        reasons.append(f"{direction}_displacement_confirmed_acceptance")
    elif displacement["strength"] == "moderate":
        score += 0.75
    else:
        warnings.append("no_clear_displacement_through_failed_ob")

    if structure:
        score += 1.5
        reasons.append(f"{direction}_structure_event_followed_failure")
    else:
        warnings.append("no_structure_event_after_failure")
        score_cap = min(score_cap, 5.0)

    if retest["status"] == BreakerRetestStatus.CONFIRMED_REACTION:
        score += 1.5
        reasons.append("retest_of_breaker_zone_confirmed_reaction")
    elif retest["status"] == BreakerRetestStatus.NOT_RETESTED:
        warnings.append("breaker_candidate_without_retest")
        score_cap = min(score_cap, 6.5)
    elif retest["status"] == BreakerRetestStatus.FAILED:
        warnings.append("breaker_failed_after_retest")
    else:
        score += 0.75
        warnings.append("breaker_retested_without_confirmed_reaction")

    if "sweep" in str(ob.get("created_by_event", "")).lower():
        score += 0.75
        reasons.append("liquidity_sweep_context_supports_trapped_trader_logic")
    if bool(_nested(ob, "fvg_context", "fvg_created_after_displacement", default=False)):
        score += 0.75
        reasons.append("imbalance_context_present_from_original_ob")
    if _pd_alignment(ob, direction):
        score += 0.75
        reasons.append("premium_discount_or_htf_context_supports_new_direction")
    if (float(ob["zone_high"]) - float(ob["zone_low"])) <= max(abs(float(ob["mean_threshold"])) * 0.01, 5.0):
        score += 0.5
        reasons.append("breaker_zone_has_acceptable_efficiency")

    return max(0.0, min(score_cap, round(score, 2))), reasons, warnings


def _confidence_grade(score: float) -> BreakerConfidenceGrade:
    if score >= 9.0:
        return BreakerConfidenceGrade.HIGH_QUALITY
    if score >= 7.0:
        return BreakerConfidenceGrade.STRONG
    if score >= 5.0:
        return BreakerConfidenceGrade.MODERATE
    if score >= 3.0:
        return BreakerConfidenceGrade.WEAK
    return BreakerConfidenceGrade.INVALID


def _displacement_snapshot(candle: Mapping[str, Any], atr: float, direction: str) -> dict[str, Any]:
    candle_range = max(float(candle["high"]) - float(candle["low"]), 1e-9)
    body = abs(float(candle["close"]) - float(candle["open"]))
    body_ratio = body / candle_range
    range_to_atr = candle_range / max(float(atr), 1e-9)
    if direction == "bullish":
        directional = candle["close"] > candle["open"]
        close_position_ok = (float(candle["close"]) - float(candle["low"])) / candle_range >= 0.70
    else:
        directional = candle["close"] < candle["open"]
        close_position_ok = (float(candle["high"]) - float(candle["close"])) / candle_range >= 0.70
    present = directional and body_ratio >= 0.55 and range_to_atr >= 1.0 and close_position_ok
    strength = "strong" if present and range_to_atr >= 1.25 else "moderate" if directional and body_ratio >= 0.45 else "weak"
    return {
        "present": present,
        "strength": strength,
        "body_to_range_ratio": round(body_ratio, 3),
        "range_to_atr_ratio": round(range_to_atr, 3),
    }


def _structure_after_failure(structures: Sequence[dict[str, Any]], direction: str, failed_at_index: int) -> dict[str, Any] | None:
    valid_types = {"BOS", "MSS", "CHOCH"}
    for event in sorted(structures, key=lambda item: item["confirmation_candle_index"]):
        if event["direction"] == direction and event["confirmation_candle_index"] >= failed_at_index and event["event_type"] in valid_types:
            return event
    return None


def _normalize_order_block(ob: Mapping[str, Any]) -> dict[str, Any] | None:
    direction = str(ob.get("direction", "")).lower()
    if direction not in {"bullish", "bearish"}:
        return None
    zone_high = _float(ob.get("zone_high", _nested(ob, "zone_definition", "zone_high")))
    zone_low = _float(ob.get("zone_low", _nested(ob, "zone_definition", "zone_low")))
    if zone_high is None or zone_low is None:
        return None
    mean = _float(ob.get("mean_threshold", ob.get("zone_mid", (zone_high + zone_low) / 2.0)))
    created_index = _created_index(ob)
    if created_index is None:
        return None
    return {
        "ob_id": ob.get("ob_id", ob.get("id", f"OB_{direction}_{created_index}")),
        "direction": direction,
        "zone_high": max(zone_high, zone_low),
        "zone_low": min(zone_high, zone_low),
        "mean_threshold": float(mean if mean is not None else (zone_high + zone_low) / 2.0),
        "created_index": int(created_index),
        "quality_score": float(ob.get("quality_score", 0.0) or 0.0),
        "created_by_event": ob.get("created_by_event", "unknown"),
        "symbol": ob.get("symbol", "unknown"),
        "timeframe": ob.get("timeframe", "unknown"),
        "fvg_context": ob.get("fvg_context", {}),
        "premium_discount_context": ob.get("premium_discount_context", {}),
    }


def _created_index(ob: Mapping[str, Any]) -> int | None:
    for key in ("created_index", "confirmation_candle_index", "failed_at_index"):
        value = ob.get(key)
        if value is not None:
            return int(value)
    structure = ob.get("structure_event_reference")
    if isinstance(structure, Mapping) and structure.get("confirmation_candle_index") is not None:
        return int(structure["confirmation_candle_index"])
    formation = ob.get("formation_context")
    if isinstance(formation, Mapping) and formation.get("structure_confirmation_candle_index") is not None:
        return int(formation["structure_confirmation_candle_index"])
    candle = ob.get("ob_candle")
    if isinstance(candle, Mapping) and candle.get("index") is not None:
        return int(candle["index"])
    return None


def _structure_event(event: Mapping[str, Any] | str) -> dict[str, Any] | None:
    text = _event_text(event).lower()
    direction = "bullish" if "bullish" in text else "bearish" if "bearish" in text else None
    if direction is None:
        return None
    event_type = str(_field(event, "event_type", "type", default="CHOCH" if "choch" in text else "MSS" if "mss" in text else "BOS" if "bos" in text else "UNKNOWN")).upper()
    index = _int_field(event, "confirmation_candle_index", "confirmation_index", "index", "candle_index")
    if index is None:
        return None
    return {
        "direction": direction,
        "event_type": event_type,
        "confirmation_candle_index": index,
        "broken_level": _float(_field(event, "broken_level", default=None)),
        "quality_score": _float(_field(event, "quality_score", "confidence_score", default=0.0)) or 0.0,
    }


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


def _acceptance_type(wick_only: bool, direction: str, displacement: Mapping[str, Any]) -> str:
    if wick_only:
        return "wick_only_no_acceptance_close_beyond_ob"
    if displacement.get("present"):
        return f"strong_close_{'above' if direction == 'bullish' else 'below'}_zone"
    return f"close_{'above' if direction == 'bullish' else 'below'}_zone"


def _pd_alignment(ob: Mapping[str, Any], direction: str) -> bool:
    pd = ob.get("premium_discount_context", {})
    if not isinstance(pd, Mapping):
        return False
    location = str(pd.get("poi_location", "")).lower()
    return (direction == "bullish" and "discount" in location) or (direction == "bearish" and "premium" in location)


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
