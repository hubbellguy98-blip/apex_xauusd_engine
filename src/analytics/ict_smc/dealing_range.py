"""Deterministic ICT/SMC dealing range selection.

A dealing range is a structural map, not an entry trigger. It selects the active
high/low boundaries, premium/discount zones, and internal/external liquidity so
other concepts can decide whether a POI is in the right location.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from src.analytics.ict_smc.premium_discount import calculate_premium_discount


class DealingRangeDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class ICTDealingRangeType(str, Enum):
    BULLISH_MSS = "bullish_MSS_dealing_range"
    BEARISH_MSS = "bearish_MSS_dealing_range"
    BULLISH_BOS = "bullish_BOS_dealing_range"
    BEARISH_BOS = "bearish_BOS_dealing_range"
    HTF_STRUCTURAL = "HTF_structural_range"
    LOCAL_SWING = "local_swing_range"
    NOISY_LOCAL = "noisy_local_range"


class RangeLiquidityLocation(str, Enum):
    INTERNAL = "internal"
    EXTERNAL_BUY_SIDE = "external_buy_side"
    EXTERNAL_SELL_SIDE = "external_sell_side"


class ICTDealingRangeQualityGrade(str, Enum):
    INVALID = "invalid"
    WEAK = "weak"
    USABLE = "usable"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True, slots=True)
class RangeSwingAnchor:
    index: int
    timestamp: datetime | None
    price: float
    type: str
    strength_score: float
    timeframe: str
    confirmed_status: bool
    structural_importance: str = "unknown"
    source: str = "provided_swing"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.timestamp is not None:
            payload["timestamp"] = self.timestamp.isoformat()
        return payload


@dataclass(frozen=True, slots=True)
class RangeLiquidityLevel:
    price: float
    liquidity_type: str
    location: RangeLiquidityLocation
    source: str
    index: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "price": round(self.price, 5),
            "liquidity_type": self.liquidity_type,
            "location": self.location.value,
            "source": self.source,
            "index": self.index,
        }


@dataclass(frozen=True, slots=True)
class DealingRangeCandidate:
    range_low_anchor: RangeSwingAnchor
    range_high_anchor: RangeSwingAnchor
    first_anchor: RangeSwingAnchor
    second_anchor: RangeSwingAnchor
    direction: DealingRangeDirection
    range_type: ICTDealingRangeType
    range_size: float
    quality_score: float
    quality_grade: ICTDealingRangeQualityGrade
    range_valid: bool
    structure_event: dict[str, Any] | None
    warnings: tuple[str, ...]
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "range_low": round(self.range_low_anchor.price, 5),
            "range_high": round(self.range_high_anchor.price, 5),
            "range_size": round(self.range_size, 5),
            "range_type": self.range_type.value,
            "range_direction": self.direction.value,
            "quality_score": round(self.quality_score, 2),
            "quality_grade": self.quality_grade.value,
            "range_valid": self.range_valid,
            "structure_event": self.structure_event,
            "selected_from": {
                "first_anchor": self.first_anchor.as_dict(),
                "second_anchor": self.second_anchor.as_dict(),
                "swing_low": self.range_low_anchor.as_dict(),
                "swing_high": self.range_high_anchor.as_dict(),
            },
            "warnings": list(self.warnings),
            "reasons": list(self.reasons),
        }


def identify_dealing_range(
    df: Sequence[Mapping[str, Any]] | Any,
    swings: Sequence[Mapping[str, Any] | RangeSwingAnchor] | Any,
    timeframe: str,
    *,
    structure_events: Sequence[Mapping[str, Any] | str] | None = None,
    liquidity_pools: Sequence[Mapping[str, Any] | RangeLiquidityLevel] | None = None,
    current_price: float | None = None,
    atr: float | None = None,
    minimum_swing_strength: float = 5.0,
    minimum_range_atr_multiplier: float = 2.0,
    minimum_candle_distance: int = 5,
    symbol: str = "unknown",
    htf_dealing_range: Mapping[str, Any] | None = None,
    max_alternatives: int = 5,
) -> dict[str, Any]:
    """Select the active ICT/SMC dealing range from confirmed structural swings."""
    candles = _normalize_candles(df)
    normalized_swings = [_swing_anchor(item, timeframe) for item in _iter_records(swings)]
    normalized_swings = [item for item in normalized_swings if item is not None]
    structures = [_structure_event(item) for item in (structure_events or ())]
    structures = [item for item in structures if item is not None]
    pools = [_liquidity_level(item) for item in (liquidity_pools or ())]
    pools = [item for item in pools if item is not None]

    if current_price is None:
        current_price = _latest_close(candles)
    if atr is None:
        atr = _estimate_atr(candles)

    candidates = _build_candidates(
        normalized_swings,
        structures,
        timeframe,
        atr,
        minimum_swing_strength,
        minimum_range_atr_multiplier,
        minimum_candle_distance,
    )
    if not candidates:
        return _empty_result(symbol, timeframe, current_price)

    selected = max(candidates, key=lambda item: (item.quality_score, item.second_anchor.index, item.range_size))
    premium_discount = calculate_premium_discount(
        selected.range_low_anchor.as_dict(),
        selected.range_high_anchor.as_dict(),
        current_price=current_price,
        atr=atr,
        minimum_range_atr_multiplier=minimum_range_atr_multiplier,
        minimum_strength_score=minimum_swing_strength,
        symbol=symbol,
        timeframe=timeframe,
        dealing_range_type=selected.range_type.value,
        context_direction=selected.direction.value,
        htf_result=htf_dealing_range,
    )
    liquidity = _classify_liquidity(selected.range_low_anchor.price, selected.range_high_anchor.price, pools)
    htf_alignment = _htf_alignment(selected.direction, htf_dealing_range)

    return {
        "concept_name": "ict_smc_dealing_range",
        "symbol": symbol,
        "timeframe": timeframe,
        "range_low": round(selected.range_low_anchor.price, 5),
        "range_high": round(selected.range_high_anchor.price, 5),
        "range_size": round(selected.range_size, 5),
        "equilibrium": premium_discount["equilibrium"],
        "discount_zone": premium_discount["discount_zone"],
        "premium_zone": premium_discount["premium_zone"],
        "deep_discount_zone": premium_discount["deep_discount_zone"],
        "deep_premium_zone": premium_discount["deep_premium_zone"],
        "range_type": selected.range_type.value,
        "range_direction": selected.direction.value,
        "range_valid": selected.range_valid,
        "quality_score": round(selected.quality_score, 2),
        "quality_grade": selected.quality_grade.value,
        "current_price": current_price,
        "current_price_location": premium_discount["current_price_location"],
        "position_percent": premium_discount["position_percent"],
        "internal_liquidity": [item.as_dict() for item in liquidity["internal"]],
        "external_liquidity": {
            "buy_side": [item.as_dict() for item in liquidity["external_buy_side"]],
            "sell_side": [item.as_dict() for item in liquidity["external_sell_side"]],
        },
        "selected_from": selected.as_dict()["selected_from"],
        "structure_event": selected.structure_event,
        "htf_alignment": htf_alignment,
        "alternative_ranges": [
            item.as_dict()
            for item in sorted(candidates, key=lambda candidate: candidate.quality_score, reverse=True)[
                1 : max_alternatives + 1
            ]
        ],
        "warnings": list(selected.warnings),
        "reasons": list(selected.reasons),
        "entry_allowed_from_dealing_range_alone": False,
    }


def _build_candidates(
    swings: list[RangeSwingAnchor],
    structures: list[dict[str, Any]],
    timeframe: str,
    atr: float | None,
    minimum_swing_strength: float,
    minimum_range_atr_multiplier: float,
    minimum_candle_distance: int,
) -> list[DealingRangeCandidate]:
    candidates: list[DealingRangeCandidate] = []
    ordered = sorted(swings, key=lambda item: item.index)
    for first in ordered:
        for second in ordered:
            if second.index <= first.index:
                continue
            if _is_swing_low(first) and _is_swing_high(second):
                candidates.append(
                    _candidate(
                        first,
                        second,
                        first,
                        second,
                        DealingRangeDirection.BULLISH,
                        structures,
                        timeframe,
                        atr,
                        minimum_swing_strength,
                        minimum_range_atr_multiplier,
                        minimum_candle_distance,
                    )
                )
            if _is_swing_high(first) and _is_swing_low(second):
                candidates.append(
                    _candidate(
                        second,
                        first,
                        first,
                        second,
                        DealingRangeDirection.BEARISH,
                        structures,
                        timeframe,
                        atr,
                        minimum_swing_strength,
                        minimum_range_atr_multiplier,
                        minimum_candle_distance,
                    )
                )
    return candidates


def _candidate(
    low_anchor: RangeSwingAnchor,
    high_anchor: RangeSwingAnchor,
    first_anchor: RangeSwingAnchor,
    second_anchor: RangeSwingAnchor,
    direction: DealingRangeDirection,
    structures: list[dict[str, Any]],
    timeframe: str,
    atr: float | None,
    minimum_swing_strength: float,
    minimum_range_atr_multiplier: float,
    minimum_candle_distance: int,
) -> DealingRangeCandidate:
    range_size = high_anchor.price - low_anchor.price
    distance = abs(second_anchor.index - first_anchor.index)
    structure = _matching_structure(direction, first_anchor.index, second_anchor.index, structures)
    range_type = _range_type(direction, structure, timeframe)
    warnings = _candidate_warnings(
        low_anchor,
        high_anchor,
        range_size,
        distance,
        atr,
        minimum_swing_strength,
        minimum_range_atr_multiplier,
        minimum_candle_distance,
    )
    if _has_invalidating_warning(warnings):
        range_type = ICTDealingRangeType.NOISY_LOCAL
    score, reasons = _quality_score(
        low_anchor,
        high_anchor,
        range_size,
        distance,
        direction,
        range_type,
        structure,
        timeframe,
        atr,
        minimum_range_atr_multiplier,
        warnings,
    )
    valid = score >= 5.0 and not _has_invalidating_warning(warnings)
    return DealingRangeCandidate(
        range_low_anchor=low_anchor,
        range_high_anchor=high_anchor,
        first_anchor=first_anchor,
        second_anchor=second_anchor,
        direction=direction,
        range_type=range_type,
        range_size=range_size,
        quality_score=score,
        quality_grade=_quality_grade(score, valid),
        range_valid=valid,
        structure_event=structure,
        warnings=tuple(warnings),
        reasons=tuple(reasons),
    )


def _candidate_warnings(
    low_anchor: RangeSwingAnchor,
    high_anchor: RangeSwingAnchor,
    range_size: float,
    candle_distance: int,
    atr: float | None,
    minimum_swing_strength: float,
    minimum_range_atr_multiplier: float,
    minimum_candle_distance: int,
) -> list[str]:
    warnings = ["dealing_range_is_structural_map_not_entry_signal"]
    invalid = False
    if range_size <= 0:
        warnings.append("invalid_negative_or_zero_range")
        invalid = True
    if not low_anchor.confirmed_status or not high_anchor.confirmed_status:
        warnings.append("unconfirmed_swing_anchor")
        invalid = True
    if low_anchor.strength_score < minimum_swing_strength or high_anchor.strength_score < minimum_swing_strength:
        warnings.append("weak_swing_anchor")
        invalid = True
    if candle_distance < minimum_candle_distance:
        warnings.append("insufficient_candle_distance_between_range_anchors")
        invalid = True
    if atr is not None and atr > 0 and range_size < atr * minimum_range_atr_multiplier:
        warnings.append("dealing_range_too_small_vs_atr")
        invalid = True
    if invalid:
        warnings.append("avoid_tiny_noisy_range")
    return warnings


def _quality_score(
    low_anchor: RangeSwingAnchor,
    high_anchor: RangeSwingAnchor,
    range_size: float,
    candle_distance: int,
    direction: DealingRangeDirection,
    range_type: ICTDealingRangeType,
    structure: Mapping[str, Any] | None,
    timeframe: str,
    atr: float | None,
    minimum_range_atr_multiplier: float,
    warnings: Sequence[str],
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    if low_anchor.confirmed_status and high_anchor.confirmed_status:
        score += 1.0
        reasons.append("confirmed_range_anchors")
    if low_anchor.strength_score >= 7.0 and high_anchor.strength_score >= 7.0:
        score += 1.0
        reasons.append("strong_swing_anchors")
    elif low_anchor.strength_score >= 5.0 and high_anchor.strength_score >= 5.0:
        score += 0.5
        reasons.append("acceptable_swing_anchors")

    if atr is None or atr <= 0:
        score += 1.0
        reasons.append("range_size_accepted_without_atr_reference")
    else:
        atr_ratio = range_size / atr
        if atr_ratio >= minimum_range_atr_multiplier * 2.0:
            score += 1.5
            reasons.append("range_clears_strong_atr_threshold")
        elif atr_ratio >= minimum_range_atr_multiplier:
            score += 0.75
            reasons.append("range_clears_minimum_atr_threshold")

    if structure is not None:
        kind = str(structure.get("event_type", "")).lower()
        if "mss" in kind or "market_structure_shift" in kind:
            score += 2.25
            reasons.append("range_updated_after_mss")
        elif "bos" in kind or "break_of_structure" in kind:
            score += 1.35
            reasons.append("range_updated_after_bos")
        else:
            score += 0.75
            reasons.append("range_linked_to_structure_event")
    elif range_type == ICTDealingRangeType.HTF_STRUCTURAL:
        score += 1.25
        reasons.append("htf_structural_range")
    else:
        score += 0.25
        reasons.append("local_range_without_structure_event")

    tf = timeframe.lower()
    if any(token in tf for token in ("4h", "1h", "daily", "d1", "weekly")):
        score += 1.0
        reasons.append("higher_timeframe_context")
    elif any(token in tf for token in ("15m", "5m", "1m")):
        score += 0.75
        reasons.append("intraday_execution_context")
    else:
        score += 0.5

    if candle_distance >= 20:
        score += 0.75
        reasons.append("well_spaced_range_anchors")
    elif candle_distance >= 5:
        score += 0.5
        reasons.append("minimum_anchor_spacing_met")
    if direction != DealingRangeDirection.NEUTRAL:
        score += 0.5
        reasons.append("directional_range_context")
    score += 0.75
    reasons.append("range_boundaries_define_external_liquidity")

    if _has_invalidating_warning(warnings):
        score = min(score, 3.5)
        reasons.append("noisy_range_score_capped")
    return max(0.0, min(10.0, score)), reasons


def _range_type(
    direction: DealingRangeDirection,
    structure: Mapping[str, Any] | None,
    timeframe: str,
) -> ICTDealingRangeType:
    if structure is not None:
        kind = str(structure.get("event_type", "")).lower()
        if direction == DealingRangeDirection.BULLISH and ("mss" in kind or "market_structure_shift" in kind):
            return ICTDealingRangeType.BULLISH_MSS
        if direction == DealingRangeDirection.BEARISH and ("mss" in kind or "market_structure_shift" in kind):
            return ICTDealingRangeType.BEARISH_MSS
        if direction == DealingRangeDirection.BULLISH and ("bos" in kind or "break_of_structure" in kind):
            return ICTDealingRangeType.BULLISH_BOS
        if direction == DealingRangeDirection.BEARISH and ("bos" in kind or "break_of_structure" in kind):
            return ICTDealingRangeType.BEARISH_BOS
    if any(token in timeframe.lower() for token in ("4h", "1h", "daily", "d1", "weekly")):
        return ICTDealingRangeType.HTF_STRUCTURAL
    return ICTDealingRangeType.LOCAL_SWING


def _has_invalidating_warning(warnings: Sequence[str]) -> bool:
    return any(
        warning
        in {
            "invalid_negative_or_zero_range",
            "unconfirmed_swing_anchor",
            "weak_swing_anchor",
            "insufficient_candle_distance_between_range_anchors",
            "dealing_range_too_small_vs_atr",
            "avoid_tiny_noisy_range",
        }
        for warning in warnings
    )


def _matching_structure(
    direction: DealingRangeDirection,
    start_index: int,
    end_index: int,
    structures: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for event in structures:
        event_direction = _direction_value(event.get("direction", event.get("bias", event.get("side", ""))))
        if event_direction != direction:
            continue
        event_index = _event_index(event)
        if event_index is None or start_index - 3 <= event_index <= end_index + 3:
            matches.append(dict(event))
    if not matches:
        return None
    return max(matches, key=lambda item: (_structure_priority(item), _event_index(item) or -1))


def _structure_priority(event: Mapping[str, Any]) -> int:
    kind = str(event.get("event_type", "")).lower()
    if "mss" in kind or "market_structure_shift" in kind:
        return 3
    if "bos" in kind or "break_of_structure" in kind:
        return 2
    return 1


def _classify_liquidity(
    range_low: float,
    range_high: float,
    pools: Sequence[RangeLiquidityLevel],
) -> dict[str, list[RangeLiquidityLevel]]:
    internal: list[RangeLiquidityLevel] = []
    buy_side: list[RangeLiquidityLevel] = [
        RangeLiquidityLevel(
            range_high,
            "range_high_buy_side_liquidity",
            RangeLiquidityLocation.EXTERNAL_BUY_SIDE,
            "range_boundary",
        )
    ]
    sell_side: list[RangeLiquidityLevel] = [
        RangeLiquidityLevel(
            range_low,
            "range_low_sell_side_liquidity",
            RangeLiquidityLocation.EXTERNAL_SELL_SIDE,
            "range_boundary",
        )
    ]
    for pool in pools:
        if range_low < pool.price < range_high:
            internal.append(_relocate(pool, RangeLiquidityLocation.INTERNAL))
        elif pool.price >= range_high:
            buy_side.append(_relocate(pool, RangeLiquidityLocation.EXTERNAL_BUY_SIDE))
        elif pool.price <= range_low:
            sell_side.append(_relocate(pool, RangeLiquidityLocation.EXTERNAL_SELL_SIDE))
    return {"internal": internal, "external_buy_side": buy_side, "external_sell_side": sell_side}


def _relocate(level: RangeLiquidityLevel, location: RangeLiquidityLocation) -> RangeLiquidityLevel:
    return RangeLiquidityLevel(level.price, level.liquidity_type, location, level.source, level.index)


def _htf_alignment(direction: DealingRangeDirection, htf_dealing_range: Mapping[str, Any] | None) -> dict[str, Any]:
    if not htf_dealing_range:
        return {
            "has_htf_context": False,
            "alignment": "unknown",
            "bullish_poi_quality_adjustment": "none",
            "bearish_ltf_setups_reduced": False,
        }
    location = str(htf_dealing_range.get("current_price_location", "unknown"))
    if direction == DealingRangeDirection.BULLISH and location in {"discount", "deep_discount"}:
        return {
            "has_htf_context": True,
            "htf_current_price_location": location,
            "alignment": "strong",
            "bullish_poi_quality_adjustment": "increase",
            "bearish_ltf_setups_reduced": True,
            "reason": "HTF discount supports bullish LTF POI filtering.",
        }
    if direction == DealingRangeDirection.BEARISH and location in {"premium", "deep_premium"}:
        return {
            "has_htf_context": True,
            "htf_current_price_location": location,
            "alignment": "strong",
            "bullish_poi_quality_adjustment": "reduce",
            "bearish_ltf_setups_reduced": False,
            "reason": "HTF premium supports bearish LTF POI filtering.",
        }
    return {
        "has_htf_context": True,
        "htf_current_price_location": location,
        "alignment": "conflict_or_neutral",
        "bullish_poi_quality_adjustment": "neutral" if location == "equilibrium_zone" else "reduce",
        "bearish_ltf_setups_reduced": direction == DealingRangeDirection.BULLISH,
        "reason": "HTF location does not strongly support the selected LTF range direction.",
    }


def _empty_result(symbol: str, timeframe: str, current_price: float | None) -> dict[str, Any]:
    return {
        "concept_name": "ict_smc_dealing_range",
        "symbol": symbol,
        "timeframe": timeframe,
        "range_low": None,
        "range_high": None,
        "range_size": None,
        "equilibrium": None,
        "discount_zone": None,
        "premium_zone": None,
        "range_type": ICTDealingRangeType.NOISY_LOCAL.value,
        "range_direction": DealingRangeDirection.NEUTRAL.value,
        "range_valid": False,
        "quality_score": 0.0,
        "quality_grade": ICTDealingRangeQualityGrade.INVALID.value,
        "current_price": current_price,
        "internal_liquidity": [],
        "external_liquidity": {"buy_side": [], "sell_side": []},
        "alternative_ranges": [],
        "warnings": ["no_valid_swing_pair_available"],
        "reasons": [],
        "entry_allowed_from_dealing_range_alone": False,
    }


def _swing_anchor(source: Mapping[str, Any] | RangeSwingAnchor, timeframe: str) -> RangeSwingAnchor | None:
    if isinstance(source, RangeSwingAnchor):
        return source
    if not isinstance(source, Mapping) or "price" not in source:
        return None
    timestamp = source.get("timestamp")
    if timestamp is not None and not isinstance(timestamp, datetime):
        timestamp = datetime.fromisoformat(str(timestamp))
    return RangeSwingAnchor(
        index=int(source.get("index", source.get("confirmation_index", -1))),
        timestamp=timestamp,
        price=float(source["price"]),
        type=str(source.get("type", source.get("swing_type", "unknown"))).lower(),
        strength_score=float(source.get("strength_score", source.get("score", source.get("strength", 0.0)))),
        timeframe=str(source.get("timeframe", timeframe)),
        confirmed_status=_confirmed(source),
        structural_importance=str(source.get("structural_importance", "unknown")),
        source=str(source.get("source", "provided_swing")),
    )


def _liquidity_level(source: Mapping[str, Any] | RangeLiquidityLevel) -> RangeLiquidityLevel | None:
    if isinstance(source, RangeLiquidityLevel):
        return source
    if not isinstance(source, Mapping):
        return None
    price = source.get("price", source.get("level", source.get("liquidity_price")))
    if price is None:
        return None
    return RangeLiquidityLevel(
        price=float(price),
        liquidity_type=str(source.get("liquidity_type", source.get("type", source.get("pool_type", "liquidity_pool")))),
        location=RangeLiquidityLocation.INTERNAL,
        source=str(source.get("source", "provided_liquidity_pool")),
        index=None if source.get("index") is None else int(source["index"]),
    )


def _structure_event(source: Mapping[str, Any] | str) -> dict[str, Any] | None:
    if isinstance(source, str):
        return {"event_type": source, "direction": _direction_value(source).value}
    if not isinstance(source, Mapping):
        return None
    event = dict(source)
    event["direction"] = _direction_value(event.get("direction", event.get("bias", event.get("side", "")))).value
    event["event_type"] = str(event.get("event_type", event.get("type", "structure_event")))
    return event


def _event_index(event: Mapping[str, Any]) -> int | None:
    for key in ("confirmation_candle_index", "confirmation_index", "index", "end_index", "break_index"):
        if event.get(key) is not None:
            return int(event[key])
    return None


def _direction_value(value: Any) -> DealingRangeDirection:
    lowered = str(value).lower()
    if "bull" in lowered or lowered == "buy" or "buy_side_swept" in lowered:
        return DealingRangeDirection.BULLISH
    if "bear" in lowered or lowered == "sell" or "sell_side_swept" in lowered:
        return DealingRangeDirection.BEARISH
    return DealingRangeDirection.NEUTRAL


def _confirmed(source: Mapping[str, Any]) -> bool:
    if "confirmed_status" in source:
        value = source["confirmed_status"]
    elif "is_confirmed" in source:
        value = source["is_confirmed"]
    elif "confirmed" in source:
        value = source["confirmed"]
    else:
        value = True
    if isinstance(value, str):
        return value.lower() in {"true", "confirmed", "yes", "1"}
    return bool(value)


def _is_swing_low(swing: RangeSwingAnchor) -> bool:
    return "low" in swing.type


def _is_swing_high(swing: RangeSwingAnchor) -> bool:
    return "high" in swing.type


def _quality_grade(score: float, valid: bool) -> ICTDealingRangeQualityGrade:
    if not valid:
        return ICTDealingRangeQualityGrade.INVALID
    if score >= 9.0:
        return ICTDealingRangeQualityGrade.HIGH_QUALITY
    if score >= 7.0:
        return ICTDealingRangeQualityGrade.STRONG
    if score >= 5.0:
        return ICTDealingRangeQualityGrade.USABLE
    return ICTDealingRangeQualityGrade.WEAK


def _iter_records(source: Any) -> Iterable[Any]:
    if source is None:
        return []
    if hasattr(source, "to_dict"):
        records = source.to_dict("records")
        if isinstance(records, list):
            return records
    return source


def _normalize_candles(df: Sequence[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    if df is None:
        return []
    if hasattr(df, "to_dict"):
        records = df.to_dict("records")
    else:
        records = list(df)
    candles: list[dict[str, Any]] = []
    for index, item in enumerate(records):
        if not isinstance(item, Mapping):
            continue
        high = item.get("high")
        low = item.get("low")
        close = item.get("close")
        if high is None or low is None or close is None:
            continue
        candles.append(
            {
                "index": int(item.get("index", index)),
                "high": float(high),
                "low": float(low),
                "close": float(close),
            }
        )
    return candles


def _latest_close(candles: Sequence[Mapping[str, Any]]) -> float | None:
    return None if not candles else float(candles[-1]["close"])


def _estimate_atr(candles: Sequence[Mapping[str, Any]], period: int = 14) -> float | None:
    if not candles:
        return None
    ranges = [float(item["high"]) - float(item["low"]) for item in candles[-period:]]
    return sum(ranges) / len(ranges) if ranges else None
