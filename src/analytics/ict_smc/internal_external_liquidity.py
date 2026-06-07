"""Internal/external liquidity classification for ICT/SMC dealing ranges.

This module does not detect liquidity by itself. It classifies already-detected
liquidity pools against an active dealing range so target selection, sweep logic,
and draw-on-liquidity modules can reason about partial and final objectives.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class InternalExternalClassification(str, Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"
    UNCLASSIFIED = "unclassified"


class ExternalLiquiditySide(str, Enum):
    ABOVE_RANGE = "above_range"
    BELOW_RANGE = "below_range"
    NONE = "none"


class ClassifiedLiquidityRole(str, Enum):
    INTERNAL_BUY_SIDE = "internal_buy_side_liquidity"
    INTERNAL_SELL_SIDE = "internal_sell_side_liquidity"
    EXTERNAL_BUY_SIDE = "external_buy_side_liquidity"
    EXTERNAL_SELL_SIDE = "external_sell_side_liquidity"
    UNKNOWN = "unknown_liquidity_role"


class TargetRole(str, Enum):
    PARTIAL_OR_INTERNAL_SWEEP = "partial_target_or_internal_sweep_area"
    INTERNAL_SELL_SIDE_OR_SHORT_PARTIAL = "internal_sweep_area_or_short_partial_target"
    FINAL_BUY_SIDE = "final_target_or_major_buy_side_sweep_area"
    FINAL_SELL_SIDE = "final_target_or_major_sell_side_sweep_area"
    INVALID = "invalid_without_dealing_range"


@dataclass(frozen=True, slots=True)
class LiquidityPoolReference:
    liquidity_id: str
    direction: str
    zone_low: float
    zone_mid: float
    zone_high: float
    liquidity_type: str
    timeframe: str
    swept_status: str
    touched_count: int
    quality_score: float
    source: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LiquidityClassificationResult:
    liquidity_id: str
    internal_or_external: InternalExternalClassification
    external_side: ExternalLiquiditySide
    liquidity_role: ClassifiedLiquidityRole
    target_role: TargetRole
    target_priority_score: float
    target_use: str
    zone_mid: float
    distance_from_range_boundary: float
    boundary_tolerance: float
    swept_status: str
    touched_count: int
    quality_score: float
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    entry_allowed_from_liquidity_classification_alone: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["internal_or_external"] = self.internal_or_external.value
        payload["external_side"] = (
            None if self.external_side == ExternalLiquiditySide.NONE else self.external_side.value
        )
        payload["liquidity_role"] = self.liquidity_role.value
        payload["target_role"] = self.target_role.value
        payload["target_priority_score"] = round(self.target_priority_score, 2)
        payload["zone_mid"] = round(self.zone_mid, 5)
        payload["distance_from_range_boundary"] = round(self.distance_from_range_boundary, 5)
        payload["boundary_tolerance"] = round(self.boundary_tolerance, 5)
        payload["reasons"] = list(self.reasons)
        payload["warnings"] = list(self.warnings)
        return payload


def classify_liquidity_internal_external(
    liquidity_pools: Sequence[Mapping[str, Any] | LiquidityPoolReference] | Any,
    dealing_range: Mapping[str, Any] | None,
    *,
    current_price: float | None = None,
    atr: float | None = None,
    previous_dealing_range: Mapping[str, Any] | None = None,
    trade_direction: str | None = None,
    boundary_tolerance: float | None = None,
    symbol: str = "unknown",
    timeframe: str = "unknown",
) -> dict[str, Any]:
    """Classify liquidity pools as internal or external to the active dealing range."""
    range_context = _dealing_range_context(dealing_range)
    pools = [_pool_reference(item, index) for index, item in enumerate(_iter_records(liquidity_pools))]
    pools = [pool for pool in pools if pool is not None]

    if range_context is None:
        return {
            "concept_name": "ict_smc_internal_external_liquidity",
            "symbol": symbol,
            "timeframe": timeframe,
            "range_valid": False,
            "classified_liquidity": [],
            "internal_liquidity": [],
            "external_liquidity": {"buy_side": [], "sell_side": []},
            "target_priority_order": [],
            "warnings": ["valid_dealing_range_required_before_liquidity_classification"],
            "entry_allowed_from_liquidity_classification_alone": False,
        }

    range_low = range_context["range_low"]
    range_high = range_context["range_high"]
    range_size = range_high - range_low
    tolerance = boundary_tolerance if boundary_tolerance is not None else _boundary_tolerance(range_size, atr)
    recalculated = _range_changed(previous_dealing_range, range_context)

    classified = [
        _classify_pool(pool, range_low, range_high, tolerance, current_price, trade_direction, recalculated)
        for pool in pools
    ]
    classified_dicts = [item.as_dict() for item in classified]
    internal = [
        item.as_dict()
        for item in classified
        if item.internal_or_external == InternalExternalClassification.INTERNAL
    ]
    external_buy = [
        item.as_dict()
        for item in classified
        if item.liquidity_role == ClassifiedLiquidityRole.EXTERNAL_BUY_SIDE
    ]
    external_sell = [
        item.as_dict()
        for item in classified
        if item.liquidity_role == ClassifiedLiquidityRole.EXTERNAL_SELL_SIDE
    ]
    target_order = sorted(classified, key=lambda item: item.target_priority_score, reverse=True)

    warnings = ["liquidity_classification_is_target_mapping_not_entry_signal"]
    if recalculated:
        warnings.append("liquidity_classification_recalculated_after_range_update")

    return {
        "concept_name": "ict_smc_internal_external_liquidity",
        "symbol": symbol,
        "timeframe": timeframe,
        "dealing_range": {
            "range_low": round(range_low, 5),
            "range_high": round(range_high, 5),
            "range_size": round(range_size, 5),
            "range_type": range_context.get("range_type"),
            "range_direction": range_context.get("range_direction"),
            "quality_score": range_context.get("quality_score"),
        },
        "boundary_tolerance": round(tolerance, 5),
        "classified_liquidity": classified_dicts,
        "internal_liquidity": internal,
        "external_liquidity": {"buy_side": external_buy, "sell_side": external_sell},
        "target_priority_order": [item.as_dict() for item in target_order],
        "movement_logic": _movement_logic(range_context.get("range_direction"), classified),
        "warnings": warnings,
        "entry_allowed_from_liquidity_classification_alone": False,
    }


def _classify_pool(
    pool: LiquidityPoolReference,
    range_low: float,
    range_high: float,
    boundary_tolerance: float,
    current_price: float | None,
    trade_direction: str | None,
    recalculated: bool,
) -> LiquidityClassificationResult:
    reasons: list[str] = []
    warnings: list[str] = []
    if pool.zone_mid >= range_high - boundary_tolerance:
        classification = InternalExternalClassification.EXTERNAL
        external_side = ExternalLiquiditySide.ABOVE_RANGE
        role = ClassifiedLiquidityRole.EXTERNAL_BUY_SIDE
        target_role = TargetRole.FINAL_BUY_SIDE
        target_use = "TP2_or_final_target_for_long"
        boundary_distance = abs(pool.zone_mid - range_high)
        reasons.extend([
            "liquidity_at_or_above_range_high",
            "range_high_acts_as_external_buy_side_liquidity",
            "external_liquidity_has_higher_target_importance",
        ])
    elif pool.zone_mid <= range_low + boundary_tolerance:
        classification = InternalExternalClassification.EXTERNAL
        external_side = ExternalLiquiditySide.BELOW_RANGE
        role = ClassifiedLiquidityRole.EXTERNAL_SELL_SIDE
        target_role = TargetRole.FINAL_SELL_SIDE
        target_use = "major_sell_side_sweep_area_or_short_final_target"
        boundary_distance = abs(pool.zone_mid - range_low)
        reasons.extend([
            "liquidity_at_or_below_range_low",
            "range_low_acts_as_external_sell_side_liquidity",
            "external_liquidity_has_higher_target_importance",
        ])
    elif range_low + boundary_tolerance < pool.zone_mid < range_high - boundary_tolerance:
        classification = InternalExternalClassification.INTERNAL
        external_side = ExternalLiquiditySide.NONE
        if _is_buy_side(pool.direction, pool.liquidity_type):
            role = ClassifiedLiquidityRole.INTERNAL_BUY_SIDE
            target_role = TargetRole.PARTIAL_OR_INTERNAL_SWEEP
            target_use = "TP1_or_internal_buy_side_sweep_before_external_target"
            reasons.append("buy_side_liquidity_inside_dealing_range")
        elif _is_sell_side(pool.direction, pool.liquidity_type):
            role = ClassifiedLiquidityRole.INTERNAL_SELL_SIDE
            target_role = TargetRole.INTERNAL_SELL_SIDE_OR_SHORT_PARTIAL
            target_use = "possible_internal_sell_side_sweep_before_continuation"
            reasons.append("sell_side_liquidity_inside_dealing_range")
        else:
            role = ClassifiedLiquidityRole.UNKNOWN
            target_role = TargetRole.PARTIAL_OR_INTERNAL_SWEEP
            target_use = "internal_liquidity_reference"
            warnings.append("liquidity_direction_unknown_inside_range")
        boundary_distance = min(abs(pool.zone_mid - range_low), abs(pool.zone_mid - range_high))
        reasons.append("liquidity_is_inside_the_dealing_range")
    else:
        classification = InternalExternalClassification.UNCLASSIFIED
        external_side = ExternalLiquiditySide.NONE
        role = ClassifiedLiquidityRole.UNKNOWN
        target_role = TargetRole.INVALID
        target_use = "unclassified_liquidity"
        boundary_distance = 0.0
        warnings.append("liquidity_could_not_be_classified")

    if recalculated:
        warnings.append("liquidity_classification_recalculated_after_range_update")

    score, score_reasons, score_warnings = _priority_score(
        pool,
        classification,
        role,
        range_low,
        range_high,
        current_price,
        trade_direction,
    )
    reasons.extend(score_reasons)
    warnings.extend(score_warnings)
    return LiquidityClassificationResult(
        liquidity_id=pool.liquidity_id,
        internal_or_external=classification,
        external_side=external_side,
        liquidity_role=role,
        target_role=target_role,
        target_priority_score=score,
        target_use=target_use,
        zone_mid=pool.zone_mid,
        distance_from_range_boundary=boundary_distance,
        boundary_tolerance=boundary_tolerance,
        swept_status=pool.swept_status,
        touched_count=pool.touched_count,
        quality_score=pool.quality_score,
        reasons=tuple(dict.fromkeys(reasons)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _priority_score(
    pool: LiquidityPoolReference,
    classification: InternalExternalClassification,
    role: ClassifiedLiquidityRole,
    range_low: float,
    range_high: float,
    current_price: float | None,
    trade_direction: str | None,
) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []
    if classification == InternalExternalClassification.EXTERNAL:
        score = 8.0
        reasons.append("external_liquidity_priority_base")
    elif classification == InternalExternalClassification.INTERNAL:
        score = 5.6 if role == ClassifiedLiquidityRole.INTERNAL_SELL_SIDE else 6.1
        reasons.append("internal_liquidity_priority_base")
    else:
        return 0.0, reasons, ["unclassified_liquidity_has_no_target_priority"]

    status = pool.swept_status.lower()
    if status == "unswept":
        score += 0.7
        reasons.append("liquidity_is_unswept")
    elif status == "swept":
        score -= 2.5
        warnings.append("liquidity_already_swept_priority_reduced")
    elif status == "touched":
        score -= 0.5
        warnings.append("liquidity_already_touched_priority_reduced")

    if pool.touched_count >= 3:
        score += 0.4
        reasons.append("multiple_reactions_strengthen_liquidity")
    elif pool.touched_count <= 1:
        score -= 0.35
        warnings.append("low_touch_count_priority_reduced")

    if "equal" in pool.liquidity_type:
        score += 0.45
        reasons.append("equal_highs_or_lows_create_clear_stop_pool")
    if any(token in pool.liquidity_type for token in ("previous_day", "session", "range", "htf")):
        score += 0.35
        reasons.append("session_or_structural_liquidity_reference")

    score += max(-0.75, min(0.75, (pool.quality_score - 6.0) * 0.15))
    if pool.quality_score < 4.0:
        warnings.append("low_source_liquidity_quality")

    if current_price is not None:
        direction = _trade_direction(trade_direction)
        if direction == "long" and role in {
            ClassifiedLiquidityRole.INTERNAL_BUY_SIDE,
            ClassifiedLiquidityRole.EXTERNAL_BUY_SIDE,
        }:
            if pool.zone_mid > current_price:
                score += 0.25
                reasons.append("liquidity_is_in_long_trade_path")
            else:
                score -= 0.75
                warnings.append("buy_side_liquidity_is_not_above_current_price")
        if direction == "short" and role in {
            ClassifiedLiquidityRole.INTERNAL_SELL_SIDE,
            ClassifiedLiquidityRole.EXTERNAL_SELL_SIDE,
        }:
            if pool.zone_mid < current_price:
                score += 0.25
                reasons.append("liquidity_is_in_short_trade_path")
            else:
                score -= 0.75
                warnings.append("sell_side_liquidity_is_not_below_current_price")

        range_size = range_high - range_low
        if range_size > 0 and abs(pool.zone_mid - current_price) > range_size * 2.0:
            score -= 0.75
            warnings.append("liquidity_may_be_too_far_for_current_range")

    if classification == InternalExternalClassification.INTERNAL:
        if role == ClassifiedLiquidityRole.INTERNAL_SELL_SIDE:
            score = min(score, 7.0)
        else:
            score = min(score, 7.9)
    return max(0.0, min(10.0, score)), reasons, warnings


def _movement_logic(range_direction: Any, classified: Sequence[LiquidityClassificationResult]) -> dict[str, Any]:
    has_internal_buy = any(item.liquidity_role == ClassifiedLiquidityRole.INTERNAL_BUY_SIDE for item in classified)
    has_internal_sell = any(item.liquidity_role == ClassifiedLiquidityRole.INTERNAL_SELL_SIDE for item in classified)
    has_external_buy = any(item.liquidity_role == ClassifiedLiquidityRole.EXTERNAL_BUY_SIDE for item in classified)
    has_external_sell = any(item.liquidity_role == ClassifiedLiquidityRole.EXTERNAL_SELL_SIDE for item in classified)
    direction = str(range_direction or "unknown").lower()
    if "bull" in direction:
        sequence = "external_sell_side_sweep_to_internal_buy_side_then_external_buy_side"
    elif "bear" in direction:
        sequence = "external_buy_side_sweep_to_internal_sell_side_then_external_sell_side"
    else:
        sequence = "internal_to_external_or_external_to_internal_depending_on_next_structure_event"
    return {
        "sequence_bias": sequence,
        "has_internal_buy_side": has_internal_buy,
        "has_internal_sell_side": has_internal_sell,
        "has_external_buy_side": has_external_buy,
        "has_external_sell_side": has_external_sell,
    }


def _dealing_range_context(dealing_range: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not dealing_range:
        return None
    if dealing_range.get("range_valid") is False:
        return None
    low = dealing_range.get("range_low")
    high = dealing_range.get("range_high")
    if low is None or high is None:
        nested = dealing_range.get("dealing_range")
        if isinstance(nested, Mapping):
            low = nested.get("range_low")
            high = nested.get("range_high")
    if low is None or high is None:
        return None
    low_f = float(low)
    high_f = float(high)
    if high_f <= low_f:
        return None
    return {
        "range_low": low_f,
        "range_high": high_f,
        "range_type": dealing_range.get("range_type"),
        "range_direction": dealing_range.get("range_direction"),
        "quality_score": dealing_range.get("quality_score"),
    }


def _pool_reference(source: Mapping[str, Any] | LiquidityPoolReference, index: int) -> LiquidityPoolReference | None:
    if isinstance(source, LiquidityPoolReference):
        return source
    if not isinstance(source, Mapping):
        return None
    zone_low, zone_mid, zone_high = _zone_values(source)
    if zone_mid is None:
        return None
    liquidity_type = str(source.get("liquidity_type", source.get("type", "liquidity_pool")))
    direction = str(source.get("direction", "")) or _infer_direction(liquidity_type)
    return LiquidityPoolReference(
        liquidity_id=str(source.get("liquidity_id", source.get("id", f"LQ_CLASSIFIED_{index}"))),
        direction=direction,
        zone_low=zone_mid if zone_low is None else zone_low,
        zone_mid=zone_mid,
        zone_high=zone_mid if zone_high is None else zone_high,
        liquidity_type=liquidity_type,
        timeframe=str(source.get("timeframe", "unknown")),
        swept_status=str(source.get("swept_status", source.get("status", "unswept"))),
        touched_count=int(source.get("touched_count", source.get("touches", 1))),
        quality_score=float(source.get("quality_score", 6.0)),
        source=str(source.get("source", "provided_liquidity_pool")),
    )


def _zone_values(source: Mapping[str, Any]) -> tuple[float | None, float | None, float | None]:
    zone = source.get("price_zone")
    if isinstance(zone, Mapping):
        low = zone.get("zone_low", zone.get("low"))
        mid = zone.get("zone_mid", zone.get("mid", zone.get("price")))
        high = zone.get("zone_high", zone.get("high"))
        return _float_or_none(low), _float_or_none(mid), _float_or_none(high)
    low = source.get("zone_low", source.get("low"))
    mid = source.get("zone_mid", source.get("price", source.get("level")))
    high = source.get("zone_high", source.get("high"))
    return _float_or_none(low), _float_or_none(mid), _float_or_none(high)


def _boundary_tolerance(range_size: float, atr: float | None) -> float:
    if atr is not None and atr > 0:
        return max(atr * 0.10, range_size * 0.002)
    return max(range_size * 0.003, 0.00001)


def _range_changed(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> bool:
    previous_context = _dealing_range_context(previous)
    if previous_context is None:
        return False
    return (
        round(previous_context["range_low"], 5) != round(float(current["range_low"]), 5)
        or round(previous_context["range_high"], 5) != round(float(current["range_high"]), 5)
    )


def _is_buy_side(direction: str, liquidity_type: str) -> bool:
    lowered = f"{direction} {liquidity_type}".lower()
    return "buy" in lowered or "high" in lowered


def _is_sell_side(direction: str, liquidity_type: str) -> bool:
    lowered = f"{direction} {liquidity_type}".lower()
    return "sell" in lowered or "low" in lowered


def _infer_direction(liquidity_type: str) -> str:
    lowered = liquidity_type.lower()
    if "high" in lowered or "buy" in lowered:
        return "buy_side"
    if "low" in lowered or "sell" in lowered:
        return "sell_side"
    return "unknown"


def _trade_direction(value: str | None) -> str:
    lowered = str(value or "").lower()
    if lowered in {"long", "buy", "bullish"}:
        return "long"
    if lowered in {"short", "sell", "bearish"}:
        return "short"
    return "unknown"


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _iter_records(source: Any) -> Sequence[Any]:
    if source is None:
        return []
    if isinstance(source, Mapping):
        return [source]
    if hasattr(source, "to_dict"):
        records = source.to_dict("records")
        if isinstance(records, list):
            return records
    return list(source)
