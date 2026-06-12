"""Objective ICT/SMC target selection.

Targets are selected after entry and stop are known. The function ranks real
liquidity pools, avoids swept or invalidated levels, checks HTF POI blockers,
and only marks the trade target as valid when an unblocked liquidity objective
meets the minimum reward-to-risk requirement.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class TargetDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"


class TargetSide(str, Enum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"
    UNKNOWN = "unknown"


class TargetStatus(str, Enum):
    VALID = "valid_target_ladder"
    ENTRY_OR_STOP_MISSING = "entry_or_stop_missing"
    ENTRY_EQUALS_STOP = "entry_equals_stop"
    INVALID_RISK_DISTANCE = "invalid_risk_distance"
    NO_LIQUIDITY_POOLS = "no_liquidity_pools"
    NO_VALID_TARGETS = "no_valid_targets"
    NO_TARGET_MEETS_MIN_RR = "no_target_meets_min_rr"
    FINAL_TARGET_BLOCKED = "final_target_blocked_by_htf_poi"


@dataclass(frozen=True, slots=True)
class _LiquidityTarget:
    liquidity_id: str
    liquidity_type: str
    direction: TargetSide
    target_price: float
    zone_low: float
    zone_mid: float
    zone_high: float
    internal_or_external: str
    swept_status: str
    invalidated: bool
    quality_score: float
    priority_score: float
    timeframe: str
    session_source: str
    distance: float
    rr: float
    blockers: tuple[dict[str, Any], ...]
    target_quality_score: float
    status: str
    raw: Mapping[str, Any]


def select_smc_targets(
    entry: float | int | str | None,
    stop: float | int | str | None,
    liquidity_pools: Sequence[Mapping[str, Any] | Any] | Any,
    poi_zones: Sequence[Mapping[str, Any] | Any] | Any,
    min_rr: float = 1.5,
) -> dict[str, Any]:
    """Select target_1, target_2, and final target from liquidity pools."""

    entry_price = _float(entry)
    stop_price = _float(stop)
    minimum_rr = max(0.0, float(min_rr))
    warnings = [
        "Targets are selected only after entry and stop-loss are known.",
        "Targets must be real liquidity, not random fixed points.",
    ]

    if entry_price is None or stop_price is None:
        return _blocked(TargetStatus.ENTRY_OR_STOP_MISSING, warnings, minimum_rr)
    if entry_price == stop_price:
        return _blocked(
            TargetStatus.ENTRY_EQUALS_STOP,
            warnings,
            minimum_rr,
            entry=entry_price,
            stop=stop_price,
        )

    direction = TargetDirection.BULLISH if entry_price > stop_price else TargetDirection.BEARISH
    risk_distance = abs(entry_price - stop_price)
    if risk_distance <= 0:
        return _blocked(
            TargetStatus.INVALID_RISK_DISTANCE,
            warnings,
            minimum_rr,
            entry=entry_price,
            stop=stop_price,
            direction=direction,
            risk_distance=risk_distance,
        )

    pools = _records(liquidity_pools)
    if not pools:
        return _blocked(
            TargetStatus.NO_LIQUIDITY_POOLS,
            warnings,
            minimum_rr,
            entry=entry_price,
            stop=stop_price,
            direction=direction,
            risk_distance=risk_distance,
        )

    rejected: list[dict[str, Any]] = []
    candidates: list[_LiquidityTarget] = []
    for raw in pools:
        candidate = _candidate(raw, direction, entry_price, risk_distance, rejected)
        if candidate is not None:
            candidates.append(candidate)

    if not candidates:
        return _blocked(
            TargetStatus.NO_VALID_TARGETS,
            warnings + ["No fresh liquidity target exists on the correct side of entry."],
            minimum_rr,
            entry=entry_price,
            stop=stop_price,
            direction=direction,
            risk_distance=risk_distance,
            rejected_targets=rejected,
        )

    blockers = _records(poi_zones)
    scored = [
        _score_target(candidate, blockers, direction, entry_price, minimum_rr)
        for candidate in candidates
    ]
    usable = [target for target in scored if target.status != "blocked"]
    blocked_targets = [
        _blocked_target_dict(target) for target in scored if target.status == "blocked"
    ]

    target_1 = _select_target_1(usable)
    target_2 = _select_target_2(usable, target_1)
    final_target = _select_final_target(usable, minimum_rr)
    best_blocked_final = _select_final_target(scored, minimum_rr)

    status = TargetStatus.VALID
    valid_trade_target_exists = final_target is not None
    if (
        final_target is None
        and best_blocked_final is not None
        and best_blocked_final.status == "blocked"
    ):
        status = TargetStatus.FINAL_TARGET_BLOCKED
        warnings.append("Best RR target is blocked by a strong opposing HTF POI.")
    elif final_target is None:
        status = TargetStatus.NO_TARGET_MEETS_MIN_RR
        warnings.append("No unblocked liquidity target meets minimum RR.")

    final_for_output = final_target or best_blocked_final
    rr_values = {
        "rr_to_target_1": _round(target_1.rr) if target_1 else None,
        "rr_to_target_2": _round(target_2.rr) if target_2 else None,
        "rr_to_final_target": _round(final_for_output.rr) if final_for_output else None,
        "minimum_rr_required": minimum_rr,
    }
    selected_scores = [
        target.target_quality_score
        for target in [target_1, target_2, final_target]
        if target is not None
    ]
    if selected_scores:
        target_quality_score = round(sum(selected_scores) / len(selected_scores), 2)
    elif final_for_output is not None:
        target_quality_score = round(final_for_output.target_quality_score, 2)
    else:
        target_quality_score = 0.0

    return {
        "function": "select_smc_targets",
        "concept_name": "ICT/SMC Target Logic",
        "direction": direction.value,
        "entry": _round(entry_price),
        "stop": _round(stop_price),
        "risk_distance": _round(risk_distance),
        "target_1": _target_dict(target_1, "partial_target"),
        "target_2": _target_dict(target_2, "main_intraday_target"),
        "final_target": _target_dict(final_for_output, "final_target"),
        "practical_final_target": _target_dict(final_target, "practical_final_target"),
        "rr_values": rr_values,
        "target_quality_score": target_quality_score,
        "valid_trade_target_exists": valid_trade_target_exists,
        "blocked_targets": blocked_targets,
        "rejected_targets": rejected,
        "candidate_targets": [_target_dict(target, "candidate") for target in scored],
        "decision": {
            "status": status.value,
            "execution_allowed": valid_trade_target_exists,
            "reason": status.value,
        },
        "warnings": _dedupe(
            warnings + _target_warnings(target_1, target_2, final_target, minimum_rr)
        ),
        "reasons": _reasons(direction, final_target, blocked_targets, minimum_rr),
    }


def _candidate(
    raw: Mapping[str, Any] | Any,
    direction: TargetDirection,
    entry: float,
    risk_distance: float,
    rejected: list[dict[str, Any]],
) -> _LiquidityTarget | None:
    liquidity_id = str(_get(raw, "liquidity_id", "target_id", "id", default="liquidity"))
    liquidity_type = str(_get(raw, "liquidity_type", "type", default="liquidity")).lower()
    swept_status = str(_get(raw, "swept_status", "status", default="unswept")).lower()
    invalidated = bool(_get(raw, "invalidated_status", "invalidated", default=False))
    if swept_status in {"fully_swept", "swept", "cleared", "invalidated"}:
        rejected.append(_reject(liquidity_id, liquidity_type, "target_already_swept"))
        return None
    if invalidated:
        rejected.append(_reject(liquidity_id, liquidity_type, "target_invalidated"))
        return None

    side = _target_side(_get(raw, "direction", "side", default=None), liquidity_type)
    wanted_side = (
        TargetSide.BUY_SIDE
        if direction is TargetDirection.BULLISH
        else TargetSide.SELL_SIDE
    )
    if side is not wanted_side:
        rejected.append(_reject(liquidity_id, liquidity_type, "wrong_liquidity_side"))
        return None

    low, mid, high = _price_zone(raw)
    if low is None or mid is None or high is None:
        rejected.append(_reject(liquidity_id, liquidity_type, "missing_target_price"))
        return None
    target_price = _target_price_for_direction(raw, direction, low, mid, high)
    if direction is TargetDirection.BULLISH and target_price <= entry:
        rejected.append(_reject(liquidity_id, liquidity_type, "wrong_side_or_below_entry"))
        return None
    if direction is TargetDirection.BEARISH and target_price >= entry:
        rejected.append(_reject(liquidity_id, liquidity_type, "wrong_side_or_above_entry"))
        return None

    reward = abs(target_price - entry)
    if reward <= 0:
        rejected.append(_reject(liquidity_id, liquidity_type, "no_reward"))
        return None

    return _LiquidityTarget(
        liquidity_id=liquidity_id,
        liquidity_type=liquidity_type,
        direction=side,
        target_price=target_price,
        zone_low=low,
        zone_mid=mid,
        zone_high=high,
        internal_or_external=str(
            _get(raw, "internal_or_external", "liquidity_role", default="internal")
        ).lower(),
        swept_status=swept_status,
        invalidated=invalidated,
        quality_score=float(_get(raw, "quality_score", default=5.0)),
        priority_score=float(
            _get(raw, "target_priority_score", "priority_score", default=5.0)
        ),
        timeframe=str(_get(raw, "timeframe", default="unknown")).lower(),
        session_source=str(_get(raw, "session_source", default="unknown")).lower(),
        distance=reward,
        rr=reward / risk_distance,
        blockers=(),
        target_quality_score=0.0,
        status="candidate",
        raw=raw if isinstance(raw, Mapping) else {},
    )


def _score_target(
    target: _LiquidityTarget,
    poi_zones: Sequence[Mapping[str, Any] | Any],
    direction: TargetDirection,
    entry: float,
    min_rr: float,
) -> _LiquidityTarget:
    blockers = tuple(_target_blockers(target, poi_zones, direction, entry))
    blocked = bool(blockers)
    score = 1.2
    score += min(10.0, target.quality_score) * 0.30
    score += min(10.0, target.priority_score) * 0.20
    score += _role_score(target.internal_or_external, target.rr)
    score += _timeframe_score(target.timeframe)
    score += _liquidity_type_score(target.liquidity_type)
    score += 1.2 if target.rr >= min_rr else 0.2
    score += 0.8 if target.rr >= max(2.0, min_rr) else 0.0
    score += 0.8 if target.swept_status in {"unswept", "fresh", "active"} else -0.4
    score -= 3.0 if blocked else 0.0
    score = max(0.0, min(10.0, score))
    status = _target_status(target.rr, min_rr, blocked, target.internal_or_external)
    return _replace_target(
        target,
        blockers=blockers,
        target_quality_score=round(score, 2),
        status=status,
    )


def _target_blockers(
    target: _LiquidityTarget,
    poi_zones: Sequence[Mapping[str, Any] | Any],
    direction: TargetDirection,
    entry: float,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for poi in poi_zones:
        poi_direction = _direction_text(_get(poi, "direction", default=""))
        quality = float(_get(poi, "quality_score", default=0.0))
        invalidated = bool(_get(poi, "invalidated_status", "invalidated", default=False))
        if invalidated or quality < 7.0:
            continue
        low = _float(_get(poi, "zone_low", "low", default=None))
        high = _float(_get(poi, "zone_high", "high", default=None))
        if low is None or high is None:
            continue
        zone_low, zone_high = sorted([low, high])
        if direction is TargetDirection.BULLISH:
            if poi_direction == "bearish" and zone_low > entry and zone_low < target.target_price:
                blockers.append(
                    _poi_blocker_dict(poi, target, "strong_bearish_htf_poi_blocks_upside")
                )
        elif (
            poi_direction == "bullish"
            and zone_high < entry
            and zone_high > target.target_price
        ):
            blockers.append(
                _poi_blocker_dict(poi, target, "strong_bullish_htf_poi_blocks_downside")
            )
    return blockers


def _select_target_1(targets: Sequence[_LiquidityTarget]) -> _LiquidityTarget | None:
    if not targets:
        return None
    internal = [target for target in targets if target.internal_or_external == "internal"]
    pool = internal or list(targets)
    return sorted(pool, key=lambda target: (target.distance, -target.target_quality_score))[0]


def _select_target_2(
    targets: Sequence[_LiquidityTarget],
    target_1: _LiquidityTarget | None,
) -> _LiquidityTarget | None:
    remaining = [target for target in targets if target is not target_1]
    if not remaining:
        return None
    external = [target for target in remaining if target.internal_or_external == "external"]
    pool = external or remaining
    return sorted(pool, key=lambda target: (-target.target_quality_score, target.distance))[0]


def _select_final_target(
    targets: Sequence[_LiquidityTarget],
    min_rr: float,
) -> _LiquidityTarget | None:
    valid = [
        target for target in targets if target.rr >= min_rr and target.status != "blocked"
    ]
    if not valid:
        blocked_valid = [
            target for target in targets if target.rr >= min_rr and target.status == "blocked"
        ]
        if blocked_valid:
            return sorted(blocked_valid, key=lambda t: (-t.rr, -t.target_quality_score))[0]
        return None
    external = [target for target in valid if target.internal_or_external == "external"]
    pool = external or valid
    return sorted(pool, key=lambda target: (-target.target_quality_score, -target.rr))[0]


def _target_status(rr: float, min_rr: float, blocked: bool, role: str) -> str:
    if blocked:
        return "blocked"
    if rr >= min_rr:
        return "valid_final_target"
    if role == "internal":
        return "usable_partial_target"
    return "good_target_but_below_min_rr"


def _target_dict(target: _LiquidityTarget | None, role: str) -> dict[str, Any] | None:
    if target is None:
        return None
    return {
        "liquidity_id": target.liquidity_id,
        "liquidity_type": target.liquidity_type,
        "direction": target.direction.value,
        "target_price": _round(target.target_price),
        "zone_low": _round(target.zone_low),
        "zone_mid": _round(target.zone_mid),
        "zone_high": _round(target.zone_high),
        "internal_or_external": target.internal_or_external,
        "role": role,
        "rr": _round(target.rr),
        "target_quality_score": target.target_quality_score,
        "timeframe": target.timeframe,
        "session_source": target.session_source,
        "status": target.status,
        "blockers": list(target.blockers),
    }


def _blocked_target_dict(target: _LiquidityTarget) -> dict[str, Any]:
    return {
        "blocked_target_id": target.liquidity_id,
        "liquidity_type": target.liquidity_type,
        "target_price": _round(target.target_price),
        "rr": _round(target.rr),
        "blockers": list(target.blockers),
        "reason": "Strong opposing HTF POI exists between entry and target.",
    }


def _target_warnings(
    target_1: _LiquidityTarget | None,
    target_2: _LiquidityTarget | None,
    final_target: _LiquidityTarget | None,
    min_rr: float,
) -> list[str]:
    warnings: list[str] = []
    for label, target in [("Target 1", target_1), ("Target 2", target_2)]:
        if target is not None and target.rr < min_rr:
            warnings.append(
                f"{label} is suitable only as partial profit because RR is below minimum."
            )
    if final_target is not None:
        warnings.append("Final target meets minimum RR requirement.")
    return warnings


def _reasons(
    direction: TargetDirection,
    final_target: _LiquidityTarget | None,
    blocked_targets: Sequence[Mapping[str, Any]],
    min_rr: float,
) -> list[str]:
    side = (
        "buy-side liquidity above entry"
        if direction is TargetDirection.BULLISH
        else "sell-side liquidity below entry"
    )
    reasons = [f"{direction.value.title()} trade targets {side}."]
    if final_target:
        reasons.append(
            f"{final_target.liquidity_id} is an unblocked liquidity target with RR >= {min_rr}."
        )
    if blocked_targets:
        reasons.append("One or more targets were downgraded because HTF POI blocks the path.")
    return reasons


def _price_zone(raw: Mapping[str, Any] | Any) -> tuple[float | None, float | None, float | None]:
    price = _float(_get(raw, "price", "target_price", default=None))
    low = _float(_get(raw, "zone_low", "low", default=price))
    high = _float(_get(raw, "zone_high", "high", default=price))
    mid = _float(_get(raw, "zone_mid", "mid", "midpoint", default=price))
    if low is None or high is None:
        return None, None, None
    zone_low, zone_high = sorted([low, high])
    zone_mid = mid if mid is not None else (zone_low + zone_high) / 2
    return zone_low, zone_mid, zone_high


def _target_price_for_direction(
    raw: Mapping[str, Any] | Any,
    direction: TargetDirection,
    low: float,
    mid: float,
    high: float,
) -> float:
    style = str(_get(raw, "target_style", default="conservative")).lower()
    if direction is TargetDirection.BULLISH:
        if style == "aggressive":
            return high
        if style == "normal":
            return mid
        return low
    if style == "aggressive":
        return low
    if style == "normal":
        return mid
    return high


def _target_side(value: Any, liquidity_type: str) -> TargetSide:
    text = str(value or "").lower()
    if text in {"buy_side", "buyside", "bsl", "bullish", "above", "high"}:
        return TargetSide.BUY_SIDE
    if text in {"sell_side", "sellside", "ssl", "bearish", "below", "low"}:
        return TargetSide.SELL_SIDE
    if any(term in liquidity_type for term in ["high", "pdh", "buy_side", "eqh"]):
        return TargetSide.BUY_SIDE
    if any(term in liquidity_type for term in ["low", "pdl", "sell_side", "eql"]):
        return TargetSide.SELL_SIDE
    return TargetSide.UNKNOWN


def _role_score(role: str, rr: float) -> float:
    if role == "external":
        return 1.2 if rr >= 1.0 else 0.5
    if role == "internal":
        return 0.9 if rr < 1.5 else 0.6
    return 0.4


def _timeframe_score(timeframe: str) -> float:
    return {
        "daily": 1.4,
        "d1": 1.4,
        "4h": 1.2,
        "h4": 1.2,
        "1h": 0.9,
        "h1": 0.9,
        "15m": 0.5,
        "m15": 0.5,
        "5m": 0.3,
        "m5": 0.3,
    }.get(timeframe, 0.5)


def _liquidity_type_score(liquidity_type: str) -> float:
    if any(term in liquidity_type for term in ["previous_day", "pdh", "pdl", "htf"]):
        return 1.2
    if any(term in liquidity_type for term in ["asian", "london", "new_york", "session"]):
        return 0.9
    if any(term in liquidity_type for term in ["equal", "range"]):
        return 0.8
    return 0.4


def _replace_target(target: _LiquidityTarget, **changes: Any) -> _LiquidityTarget:
    values = {
        "liquidity_id": target.liquidity_id,
        "liquidity_type": target.liquidity_type,
        "direction": target.direction,
        "target_price": target.target_price,
        "zone_low": target.zone_low,
        "zone_mid": target.zone_mid,
        "zone_high": target.zone_high,
        "internal_or_external": target.internal_or_external,
        "swept_status": target.swept_status,
        "invalidated": target.invalidated,
        "quality_score": target.quality_score,
        "priority_score": target.priority_score,
        "timeframe": target.timeframe,
        "session_source": target.session_source,
        "distance": target.distance,
        "rr": target.rr,
        "blockers": target.blockers,
        "target_quality_score": target.target_quality_score,
        "status": target.status,
        "raw": target.raw,
    }
    values.update(changes)
    return _LiquidityTarget(**values)


def _poi_blocker_dict(
    poi: Mapping[str, Any] | Any,
    target: _LiquidityTarget,
    reason: str,
) -> dict[str, Any]:
    return {
        "blocked_target_id": target.liquidity_id,
        "poi_id": str(_get(poi, "poi_id", "zone_id", "id", default="poi")),
        "poi_type": str(_get(poi, "poi_type", "zone_type", "type", default="poi")),
        "direction": _direction_text(_get(poi, "direction", default="")),
        "zone_low": _round(_float(_get(poi, "zone_low", "low", default=None))),
        "zone_high": _round(_float(_get(poi, "zone_high", "high", default=None))),
        "timeframe": str(_get(poi, "timeframe", default="unknown")),
        "quality_score": float(_get(poi, "quality_score", default=0.0)),
        "reason": reason,
    }


def _direction_text(value: Any) -> str:
    text = str(value or "").lower()
    if text in {"bearish", "sell", "supply"}:
        return "bearish"
    if text in {"bullish", "buy", "demand"}:
        return "bullish"
    return text


def _reject(liquidity_id: str, liquidity_type: str, reason: str) -> dict[str, Any]:
    return {
        "liquidity_id": liquidity_id,
        "liquidity_type": liquidity_type,
        "reason": reason,
    }


def _blocked(
    status: TargetStatus,
    warnings: Sequence[str],
    min_rr: float,
    *,
    entry: float | None = None,
    stop: float | None = None,
    direction: TargetDirection = TargetDirection.NONE,
    risk_distance: float | None = None,
    rejected_targets: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "function": "select_smc_targets",
        "concept_name": "ICT/SMC Target Logic",
        "direction": direction.value if direction is not TargetDirection.NONE else None,
        "entry": _round(entry),
        "stop": _round(stop),
        "risk_distance": _round(risk_distance),
        "target_1": None,
        "target_2": None,
        "final_target": None,
        "rr_values": {
            "rr_to_target_1": None,
            "rr_to_target_2": None,
            "rr_to_final_target": None,
            "minimum_rr_required": min_rr,
        },
        "target_quality_score": 0.0,
        "valid_trade_target_exists": False,
        "blocked_targets": [],
        "rejected_targets": list(rejected_targets or []),
        "decision": {
            "status": status.value,
            "execution_allowed": False,
            "reason": status.value,
        },
        "warnings": _dedupe(list(warnings)),
        "reasons": [status.value],
    }


def _records(rows: Sequence[Mapping[str, Any] | Any] | Any) -> list[Any]:
    if hasattr(rows, "to_dict"):
        return list(rows.to_dict("records"))  # type: ignore[call-arg, union-attr]
    return list(rows or [])


def _get(row: Mapping[str, Any] | Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if isinstance(row, Mapping) and key in row and row[key] is not None:
            return row[key]
        if not isinstance(row, Mapping) and hasattr(row, key):
            value = getattr(row, key)
            if value is not None:
                return value
    return default


def _float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value: float | None) -> float | None:
    return round(value, 5) if value is not None else None


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
