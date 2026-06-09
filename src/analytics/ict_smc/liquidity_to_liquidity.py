"""ICT/SMC liquidity-to-liquidity path mapping.

This module maps the likely draw from a recently interacted liquidity pool to a
candidate target pool. It is deterministic analytics only: a liquidity-to-
liquidity path is target-selection context, not an entry signal by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class LiquidityPathBias(str, Enum):
    UNCLEAR = "unclear"
    BULLISH = "bullish"
    BEARISH = "bearish"
    BULLISH_CANDIDATE = "bullish_candidate"
    BEARISH_CANDIDATE = "bearish_candidate"
    BULLISH_CONTINUATION = "bullish_continuation"
    BEARISH_CONTINUATION = "bearish_continuation"


class LiquiditySide(str, Enum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


class LiquidityTargetRole(str, Enum):
    PARTIAL_TARGET = "partial_target"
    FINAL_TARGET = "final_target"
    WATCHLIST_TARGET = "watchlist_target"


class LiquidityPathGrade(str, Enum):
    INVALID = "invalid"
    WATCHLIST = "watchlist"
    VALID = "valid"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class _LiquidityPool:
    liquidity_id: str
    liquidity_type: str
    direction: LiquiditySide
    zone_low: float
    zone_mid: float
    zone_high: float
    internal_or_external: str
    swept_status: str
    quality_score: float
    target_priority_score: float
    timeframe: str
    session_source: str
    touched_count: int
    created_index: int | None
    last_touched_index: int | None
    invalidated: bool


@dataclass(frozen=True, slots=True)
class _PoiZone:
    poi_id: str
    poi_type: str
    direction: str
    zone_low: float
    zone_high: float
    zone_mid: float
    timeframe: str
    fresh_status: str
    mitigated_status: str
    quality_score: float
    invalidated: bool
    premium_discount_location: str
    blocking_strength: str


_VALID_SWEEP_STATUSES = {
    "swept",
    "swept_reclaimed",
    "swept_rejected",
    "swept_and_reclaimed",
    "swept_and_rejected",
    "sell_side_sweep_reclaimed",
    "buy_side_sweep_rejected",
    "raid_reclaimed",
    "raid_rejected",
    "accepted_breakout",
    "accepted_breakdown",
}

_INVALID_TARGET_STATUSES = {
    "fully_swept",
    "invalid",
    "invalidated",
    "cleared",
    "consumed",
}

_HTF_TIMEFRAMES = {"1h", "h1", "4h", "h4", "daily", "1d", "d1", "weekly", "1w", "w1"}


def map_liquidity_to_liquidity_path(
    context: Mapping[str, Any],
    liquidity_pools: Sequence[Mapping[str, Any] | Any],
    poi_zones: Sequence[Mapping[str, Any] | Any],
    *,
    minimum_start_quality: float = 5.0,
    minimum_target_score: float = 6.0,
    minimum_rr: float = 1.5,
    blocker_quality_threshold: float = 7.0,
    strong_blocker_quality: float = 8.5,
) -> dict[str, Any]:
    """Map a likely liquidity-to-liquidity path and score the target.

    The function intentionally separates likely directional path from valid
    trade entry. It never marks the path as an executable entry signal.
    """
    warnings = [
        "Liquidity-to-liquidity path is not an entry signal",
        "Risk-to-reward and entry model must be validated before execution",
    ]
    reasons: list[str] = []
    pools = _normalize_liquidity_pools(liquidity_pools)
    pois = _normalize_poi_zones(poi_zones)
    if not pools:
        return _empty_result(context, "no_liquidity_pools_provided", warnings)

    current_price = _float(context.get("current_price"), _infer_current_price(pools))
    entry_price = _float(context.get("entry_price"), current_price)
    stop_loss = _optional_float(context.get("stop_loss"))
    start = _select_start_liquidity(context, pools, minimum_start_quality)
    if start is None:
        warnings.append("no_recent_start_liquidity_confirmed")
        both_side_blockers = _blockers_on_both_sides(pois, entry_price, blocker_quality_threshold)
        return {
            **_empty_result(context, "no_valid_start_liquidity", warnings),
            "blockers": both_side_blockers,
            "reasons": ["No confirmed swept/rejected/reclaimed starting liquidity was found"],
        }

    path_bias, structure = _derive_path_bias(context, start, warnings, reasons)
    target_candidates = _build_target_candidates(
        context,
        pools,
        pois,
        start,
        path_bias,
        entry_price,
        stop_loss,
        minimum_rr,
        blocker_quality_threshold,
        strong_blocker_quality,
        warnings,
    )
    if not target_candidates:
        warnings.append("no_valid_target_liquidity")
        return {
            **_empty_result(context, "no_valid_target_liquidity", warnings),
            "start_liquidity": _pool_to_dict(start),
            "path_bias": path_bias.value,
            "structure_confirmation": structure,
            "reasons": reasons,
        }

    target_candidates.sort(
        key=lambda item: (
            item["target_score"],
            not item["blocked_by_strong_poi"],
            item["risk_to_reward"]["rr_valid"] is True,
            item["liquidity"]["target_priority_score"],
            item["liquidity"]["quality_score"],
        ),
        reverse=True,
    )
    best = target_candidates[0]
    path_valid = _path_valid(best, path_bias, minimum_target_score)
    if best["target_score"] < minimum_target_score:
        warnings.append("no_high_quality_target_found")
    if best["blocked_by_strong_poi"]:
        warnings.append("strong_opposing_poi_blocks_selected_target")
    if best["target_role"] == LiquidityTargetRole.PARTIAL_TARGET.value:
        warnings.append("internal_target_only")

    grade = _path_grade(best["target_score"], path_valid)
    recommendation = _recommendation(best, path_valid)
    return {
        "concept_name": "Liquidity-to-Liquidity Model",
        "symbol": str(context.get("symbol") or "unknown"),
        "timeframe": str(context.get("timeframe") or "unknown"),
        "path_id": _path_id(path_bias, start, best["liquidity"]),
        "path_valid": path_valid,
        "entry_allowed_from_liquidity_path_alone": False,
        "path_bias": path_bias.value,
        "path_grade": grade.value,
        "start_liquidity": _pool_to_dict(start),
        "target_liquidity": best["liquidity"],
        "blockers": best["blockers"],
        "target_score": best["target_score"],
        "target_ladder": _target_ladder(target_candidates),
        "target_candidates": target_candidates,
        "risk_to_reward": best["risk_to_reward"],
        "rr_to_target": best["risk_to_reward"]["rr_to_target"],
        "structure_confirmation": structure,
        "path_confidence": _path_confidence(best["target_score"], path_bias, path_valid),
        "recommendation": recommendation,
        "warnings": _dedupe(warnings),
        "reasons": _dedupe(reasons + best["reasons"]),
    }


def _empty_result(context: Mapping[str, Any], status: str, warnings: list[str]) -> dict[str, Any]:
    return {
        "concept_name": "Liquidity-to-Liquidity Model",
        "symbol": str(context.get("symbol") or "unknown"),
        "timeframe": str(context.get("timeframe") or "unknown"),
        "path_id": None,
        "path_valid": False,
        "entry_allowed_from_liquidity_path_alone": False,
        "path_bias": LiquidityPathBias.UNCLEAR.value,
        "path_grade": LiquidityPathGrade.INVALID.value,
        "start_liquidity": None,
        "target_liquidity": None,
        "blockers": [],
        "target_score": 0.0,
        "target_ladder": [],
        "target_candidates": [],
        "risk_to_reward": None,
        "rr_to_target": None,
        "structure_confirmation": {
            "mss_confirmed": False,
            "mss_direction": None,
            "bos_confirmed": False,
            "bos_direction": None,
            "displacement_confirmed": False,
            "displacement_direction": None,
        },
        "path_confidence": 0.0,
        "recommendation": {"use_target": False, "reason": status},
        "warnings": _dedupe(warnings),
        "reasons": [],
        "status": status,
    }


def _select_start_liquidity(
    context: Mapping[str, Any],
    pools: Sequence[_LiquidityPool],
    minimum_start_quality: float,
) -> _LiquidityPool | None:
    sweep_event = _mapping(context.get("latest_sweep_event"))
    swept_id = str(sweep_event.get("swept_liquidity_id") or sweep_event.get("liquidity_id") or "")
    if swept_id:
        matched = next((pool for pool in pools if pool.liquidity_id == swept_id), None)
        if matched and _valid_start_pool(matched, minimum_start_quality):
            return matched

    current_index = _optional_int(context.get("current_index"))
    candidates = [pool for pool in pools if _valid_start_pool(pool, minimum_start_quality)]
    if not candidates:
        return None
    candidates.sort(
        key=lambda pool: (
            _recent_score(pool, current_index),
            _start_status_score(pool.swept_status),
            pool.quality_score,
            pool.target_priority_score,
        ),
        reverse=True,
    )
    return candidates[0]


def _valid_start_pool(pool: _LiquidityPool, minimum_start_quality: float) -> bool:
    return (
        not pool.invalidated
        and pool.quality_score >= minimum_start_quality
        and pool.swept_status in _VALID_SWEEP_STATUSES
    )


def _derive_path_bias(
    context: Mapping[str, Any],
    start: _LiquidityPool,
    warnings: list[str],
    reasons: list[str],
) -> tuple[LiquidityPathBias, dict[str, Any]]:
    mss = _mapping(context.get("latest_mss_event") or context.get("latest_mss"))
    bos = _mapping(context.get("latest_bos_event") or context.get("latest_bos"))
    displacement = _mapping(context.get("displacement") or context.get("latest_displacement"))
    mss_direction = _direction_text(mss.get("direction"))
    bos_direction = _direction_text(bos.get("direction"))
    displacement_direction = _direction_text(displacement.get("direction"))
    mss_confirmed = _truthy(mss.get("confirmed"), bool(mss_direction))
    bos_confirmed = _truthy(bos.get("confirmed"), bool(bos_direction))
    displacement_confirmed = _truthy(displacement.get("confirmed"), bool(displacement_direction))
    structure = {
        "mss_confirmed": mss_confirmed,
        "mss_direction": mss_direction,
        "bos_confirmed": bos_confirmed,
        "bos_direction": bos_direction,
        "displacement_confirmed": displacement_confirmed,
        "displacement_direction": displacement_direction,
    }
    if start.direction is LiquiditySide.SELL_SIDE:
        if _bullish_structure(mss_direction, bos_direction, displacement_direction, structure):
            reasons.append("Start liquidity was sell-side and bullish structure confirmed")
            return LiquidityPathBias.BULLISH, structure
        warnings.append("path_not_confirmed_by_structure")
        return LiquidityPathBias.BULLISH_CANDIDATE, structure
    if start.direction is LiquiditySide.BUY_SIDE:
        if _bearish_structure(mss_direction, bos_direction, displacement_direction, structure):
            reasons.append("Start liquidity was buy-side and bearish structure confirmed")
            return LiquidityPathBias.BEARISH, structure
        warnings.append("path_not_confirmed_by_structure")
        return LiquidityPathBias.BEARISH_CANDIDATE, structure
    warnings.append("start_liquidity_direction_unclear")
    return LiquidityPathBias.UNCLEAR, structure


def _bullish_structure(
    mss_direction: str | None,
    bos_direction: str | None,
    displacement_direction: str | None,
    structure: Mapping[str, Any],
) -> bool:
    confirmed = (
        (structure["mss_confirmed"] and mss_direction == "bullish")
        or (structure["bos_confirmed"] and bos_direction == "bullish")
    )
    return confirmed and (
        not structure["displacement_confirmed"] or displacement_direction == "bullish"
    )


def _bearish_structure(
    mss_direction: str | None,
    bos_direction: str | None,
    displacement_direction: str | None,
    structure: Mapping[str, Any],
) -> bool:
    confirmed = (
        (structure["mss_confirmed"] and mss_direction == "bearish")
        or (structure["bos_confirmed"] and bos_direction == "bearish")
    )
    return confirmed and (
        not structure["displacement_confirmed"] or displacement_direction == "bearish"
    )


def _build_target_candidates(
    context: Mapping[str, Any],
    pools: Sequence[_LiquidityPool],
    pois: Sequence[_PoiZone],
    start: _LiquidityPool,
    path_bias: LiquidityPathBias,
    entry_price: float,
    stop_loss: float | None,
    minimum_rr: float,
    blocker_quality_threshold: float,
    strong_blocker_quality: float,
    warnings: list[str],
) -> list[dict[str, Any]]:
    target_side = _target_side(path_bias)
    if target_side is None:
        return []
    candidates: list[dict[str, Any]] = []
    for pool in pools:
        if pool.liquidity_id == start.liquidity_id:
            continue
        if pool.direction is not target_side:
            continue
        if pool.invalidated or pool.swept_status in _INVALID_TARGET_STATUSES:
            continue
        if target_side is LiquiditySide.BUY_SIDE and pool.zone_mid <= entry_price:
            continue
        if target_side is LiquiditySide.SELL_SIDE and pool.zone_mid >= entry_price:
            continue
        blockers = _detect_blockers(
            pois,
            entry_price,
            pool,
            path_bias,
            blocker_quality_threshold,
            strong_blocker_quality,
        )
        rr = _risk_to_reward(entry_price, stop_loss, pool.zone_mid, path_bias, minimum_rr, warnings)
        score, reasons = _score_target(context, pool, path_bias, blockers, rr)
        candidates.append(
            {
                "liquidity": _pool_to_dict(pool),
                "target_role": _target_role(pool, context),
                "blockers": blockers,
                "blocked_by_strong_poi": any(b["blocker_strength"] == "strong" for b in blockers),
                "risk_to_reward": rr,
                "target_score": score,
                "reasons": reasons,
            }
        )
    return candidates


def _target_side(path_bias: LiquidityPathBias) -> LiquiditySide | None:
    if path_bias in {
        LiquidityPathBias.BULLISH,
        LiquidityPathBias.BULLISH_CANDIDATE,
        LiquidityPathBias.BULLISH_CONTINUATION,
    }:
        return LiquiditySide.BUY_SIDE
    if path_bias in {
        LiquidityPathBias.BEARISH,
        LiquidityPathBias.BEARISH_CANDIDATE,
        LiquidityPathBias.BEARISH_CONTINUATION,
    }:
        return LiquiditySide.SELL_SIDE
    return None


def _detect_blockers(
    pois: Sequence[_PoiZone],
    entry_price: float,
    target: _LiquidityPool,
    path_bias: LiquidityPathBias,
    blocker_quality_threshold: float,
    strong_blocker_quality: float,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    bullish_path = path_bias in {
        LiquidityPathBias.BULLISH,
        LiquidityPathBias.BULLISH_CANDIDATE,
        LiquidityPathBias.BULLISH_CONTINUATION,
    }
    bearish_path = path_bias in {
        LiquidityPathBias.BEARISH,
        LiquidityPathBias.BEARISH_CANDIDATE,
        LiquidityPathBias.BEARISH_CONTINUATION,
    }
    for poi in pois:
        if poi.invalidated or poi.quality_score < blocker_quality_threshold:
            continue
        if (
            bullish_path
            and poi.direction == "bearish"
            and entry_price < poi.zone_low < target.zone_mid
        ):
            blockers.append(_blocker_dict(poi, target, strong_blocker_quality))
        if (
            bearish_path
            and poi.direction == "bullish"
            and target.zone_mid < poi.zone_high < entry_price
        ):
            blockers.append(_blocker_dict(poi, target, strong_blocker_quality))
    blockers.sort(
        key=lambda b: (b["blocker_strength"] == "strong", b["quality_score"]),
        reverse=True,
    )
    return blockers


def _blocker_dict(
    poi: _PoiZone,
    target: _LiquidityPool,
    strong_blocker_quality: float,
) -> dict[str, Any]:
    strength = _blocker_strength(poi, strong_blocker_quality)
    return {
        "poi_id": poi.poi_id,
        "poi_type": poi.poi_type,
        "direction": poi.direction,
        "zone_low": poi.zone_low,
        "zone_high": poi.zone_high,
        "zone_mid": poi.zone_mid,
        "timeframe": poi.timeframe,
        "fresh_status": poi.fresh_status,
        "quality_score": poi.quality_score,
        "blocker_strength": strength,
        "blocks_target_id": target.liquidity_id,
        "note": f"{poi.direction} {poi.poi_type} sits between entry and target liquidity",
    }


def _blocker_strength(poi: _PoiZone, strong_blocker_quality: float) -> str:
    if poi.blocking_strength in {"strong", "major"}:
        return "strong"
    if poi.quality_score >= strong_blocker_quality or poi.timeframe.lower() in _HTF_TIMEFRAMES:
        return "strong"
    if poi.quality_score >= 7.0:
        return "moderate"
    return "weak"


def _risk_to_reward(
    entry_price: float,
    stop_loss: float | None,
    target_price: float,
    path_bias: LiquidityPathBias,
    minimum_rr: float,
    warnings: list[str],
) -> dict[str, Any]:
    if stop_loss is None:
        warnings.append("stop_loss_missing_cannot_compute_rr")
        return {
            "entry_price": entry_price,
            "stop_loss": None,
            "target_price": target_price,
            "risk_points": None,
            "reward_points": None,
            "rr_to_target": None,
            "rr_valid": None,
            "minimum_rr_required": minimum_rr,
        }
    bullish = path_bias in {
        LiquidityPathBias.BULLISH,
        LiquidityPathBias.BULLISH_CANDIDATE,
        LiquidityPathBias.BULLISH_CONTINUATION,
    }
    if bullish:
        risk = entry_price - stop_loss
        reward = target_price - entry_price
    else:
        risk = stop_loss - entry_price
        reward = entry_price - target_price
    if risk <= 0 or reward <= 0:
        rr = None
        rr_valid = False
    else:
        rr = round(reward / risk, 4)
        rr_valid = rr >= minimum_rr
    return {
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "risk_points": round(risk, 5),
        "reward_points": round(reward, 5),
        "rr_to_target": rr,
        "rr_valid": rr_valid,
        "minimum_rr_required": minimum_rr,
    }


def _score_target(
    context: Mapping[str, Any],
    pool: _LiquidityPool,
    path_bias: LiquidityPathBias,
    blockers: Sequence[Mapping[str, Any]],
    rr: Mapping[str, Any],
) -> tuple[float, list[str]]:
    score = 1.4
    reasons = ["Target is directionally aligned with liquidity-to-liquidity path"]
    score += min(pool.quality_score, 10.0) * 0.18
    score += min(pool.target_priority_score, 10.0) * 0.16
    if pool.swept_status in {"unswept", "active", "fresh", "resting"}:
        score += 1.0
        reasons.append("Target liquidity is unswept or still meaningful")
    if _target_role(pool, context) == LiquidityTargetRole.FINAL_TARGET.value:
        score += 0.9
        reasons.append("External liquidity is suitable as final target")
    else:
        score += 0.75
        reasons.append("Internal liquidity is suitable as partial target")
    if _htf_aligned(context, path_bias):
        score += 0.8
        reasons.append("HTF bias or expected draw supports the path")
    if rr.get("rr_valid") is True:
        score += 1.0
        reasons.append("Reward-to-risk meets minimum requirement")
    elif rr.get("rr_to_target") is not None:
        score -= 0.5
        reasons.append("Reward-to-risk is below the preferred threshold")
    if blockers:
        score -= 0.8 * len(blockers)
        reasons.append("Opposing POI exists between entry and target")
    if any(blocker["blocker_strength"] == "strong" for blocker in blockers):
        score = min(score, 4.0)
        reasons.append("Strong opposing POI caps target score")
    if path_bias in {LiquidityPathBias.BULLISH_CANDIDATE, LiquidityPathBias.BEARISH_CANDIDATE}:
        score = min(score, 5.0)
        reasons.append("Path is candidate because structure confirmation is incomplete")
    return round(_clamp(score, 0.0, 10.0), 2), reasons


def _target_role(pool: _LiquidityPool, context: Mapping[str, Any]) -> str:
    requested = str(context.get("target_preference") or "").lower()
    if requested == "partial" and pool.internal_or_external == "internal":
        return LiquidityTargetRole.PARTIAL_TARGET.value
    if pool.internal_or_external == "internal":
        return LiquidityTargetRole.PARTIAL_TARGET.value
    if pool.internal_or_external == "external":
        return LiquidityTargetRole.FINAL_TARGET.value
    return LiquidityTargetRole.WATCHLIST_TARGET.value


def _path_valid(
    best: Mapping[str, Any],
    path_bias: LiquidityPathBias,
    minimum_score: float,
) -> bool:
    return (
        path_bias in {LiquidityPathBias.BULLISH, LiquidityPathBias.BEARISH}
        and best["target_score"] >= minimum_score
        and not best["blocked_by_strong_poi"]
    )


def _path_grade(score: float, valid: bool) -> LiquidityPathGrade:
    if not valid:
        return LiquidityPathGrade.INVALID if score < 5.0 else LiquidityPathGrade.WATCHLIST
    if score >= 8.0:
        return LiquidityPathGrade.STRONG
    return LiquidityPathGrade.VALID


def _recommendation(best: Mapping[str, Any], path_valid: bool) -> dict[str, Any]:
    if path_valid:
        return {"use_target": True, "reason": "Target path is clean enough for context"}
    if best["blocked_by_strong_poi"]:
        return {
            "use_target": False,
            "alternative_target": "closer_internal_liquidity_before_blocker",
            "reason": "Strong opposing POI blocks path to selected target",
        }
    return {
        "use_target": False,
        "reason": "Path is not strong enough for target selection without more confirmation",
    }


def _target_ladder(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ladder: list[dict[str, Any]] = []
    for number, item in enumerate(candidates[:3], start=1):
        liquidity = item["liquidity"]
        ladder.append(
            {
                "target_number": number,
                "liquidity_id": liquidity["liquidity_id"],
                "target_type": liquidity["liquidity_type"],
                "price": liquidity["zone_mid"],
                "role": item["target_role"],
                "internal_or_external": liquidity["internal_or_external"],
                "rr_to_target": item["risk_to_reward"]["rr_to_target"],
                "target_score": item["target_score"],
            }
        )
    return ladder


def _blockers_on_both_sides(
    pois: Sequence[_PoiZone],
    entry_price: float,
    blocker_quality_threshold: float,
) -> list[dict[str, Any]]:
    blockers = []
    for poi in pois:
        if poi.invalidated or poi.quality_score < blocker_quality_threshold:
            continue
        if poi.direction == "bearish" and poi.zone_low > entry_price:
            blockers.append(_poi_to_generic_blocker(poi))
        if poi.direction == "bullish" and poi.zone_high < entry_price:
            blockers.append(_poi_to_generic_blocker(poi))
    return blockers


def _poi_to_generic_blocker(poi: _PoiZone) -> dict[str, Any]:
    return {
        "poi_id": poi.poi_id,
        "poi_type": poi.poi_type,
        "direction": poi.direction,
        "zone_low": poi.zone_low,
        "zone_high": poi.zone_high,
        "timeframe": poi.timeframe,
        "quality_score": poi.quality_score,
        "blocker_strength": poi.blocking_strength or "moderate",
    }


def _normalize_liquidity_pools(items: Sequence[Mapping[str, Any] | Any]) -> list[_LiquidityPool]:
    pools: list[_LiquidityPool] = []
    for item in items or []:
        raw = _mapping(item)
        if not raw:
            continue
        price = _float(raw.get("price") or raw.get("zone_mid"), 0.0)
        zone_low = _float(raw.get("zone_low"), price)
        zone_high = _float(raw.get("zone_high"), price)
        zone_mid = _float(raw.get("zone_mid"), (zone_low + zone_high) / 2)
        direction = _liquidity_side(raw)
        if direction is None:
            continue
        pools.append(
            _LiquidityPool(
                liquidity_id=str(raw.get("liquidity_id") or raw.get("id") or f"LIQ_{len(pools)}"),
                liquidity_type=str(
                    raw.get("liquidity_type") or raw.get("level_type") or "liquidity"
                ),
                direction=direction,
                zone_low=min(zone_low, zone_high),
                zone_mid=zone_mid,
                zone_high=max(zone_low, zone_high),
                internal_or_external=str(raw.get("internal_or_external") or "external").lower(),
                swept_status=str(raw.get("swept_status") or raw.get("status") or "unswept").lower(),
                quality_score=_float(raw.get("quality_score"), 5.0),
                target_priority_score=_float(raw.get("target_priority_score"), 5.0),
                timeframe=str(raw.get("timeframe") or "unknown"),
                session_source=str(raw.get("session_source") or "unknown"),
                touched_count=_int(raw.get("touched_count"), 0),
                created_index=_optional_int(raw.get("created_index")),
                last_touched_index=_optional_int(raw.get("last_touched_index")),
                invalidated=_truthy(raw.get("invalidated_status") or raw.get("invalidated"), False),
            )
        )
    return pools


def _normalize_poi_zones(items: Sequence[Mapping[str, Any] | Any]) -> list[_PoiZone]:
    pois: list[_PoiZone] = []
    for item in items or []:
        raw = _mapping(item)
        if not raw:
            continue
        zone_low = _float(raw.get("zone_low"), _float(raw.get("price"), 0.0))
        zone_high = _float(raw.get("zone_high"), zone_low)
        direction = _direction_text(raw.get("direction")) or "neutral"
        pois.append(
            _PoiZone(
                poi_id=str(raw.get("poi_id") or raw.get("id") or f"POI_{len(pois)}"),
                poi_type=str(raw.get("poi_type") or raw.get("type") or "poi"),
                direction=direction,
                zone_low=min(zone_low, zone_high),
                zone_high=max(zone_low, zone_high),
                zone_mid=_float(raw.get("zone_mid"), (zone_low + zone_high) / 2),
                timeframe=str(raw.get("timeframe") or "unknown"),
                fresh_status=str(raw.get("fresh_status") or "unknown").lower(),
                mitigated_status=str(raw.get("mitigated_status") or "unknown").lower(),
                quality_score=_float(raw.get("quality_score"), 5.0),
                invalidated=_truthy(raw.get("invalidated_status") or raw.get("invalidated"), False),
                premium_discount_location=str(raw.get("premium_discount_location") or "unknown"),
                blocking_strength=str(raw.get("blocking_strength") or "").lower(),
            )
        )
    return pois


def _liquidity_side(raw: Mapping[str, Any]) -> LiquiditySide | None:
    direction = str(raw.get("direction") or raw.get("side") or "").lower()
    liquidity_type = str(raw.get("liquidity_type") or raw.get("level_type") or "").lower()
    if direction in {"buy_side", "buyside", "buy", "above", "high"}:
        return LiquiditySide.BUY_SIDE
    if direction in {"sell_side", "sellside", "sell", "below", "low"}:
        return LiquiditySide.SELL_SIDE
    buy_words = ("high", "pdh", "buy_side", "equal_high", "range_high")
    sell_words = ("low", "pdl", "sell_side", "equal_low", "range_low")
    if any(word in liquidity_type for word in buy_words):
        return LiquiditySide.BUY_SIDE
    if any(word in liquidity_type for word in sell_words):
        return LiquiditySide.SELL_SIDE
    return None


def _pool_to_dict(pool: _LiquidityPool) -> dict[str, Any]:
    return {
        "liquidity_id": pool.liquidity_id,
        "liquidity_type": pool.liquidity_type,
        "direction": pool.direction.value,
        "zone_low": pool.zone_low,
        "zone_mid": pool.zone_mid,
        "zone_high": pool.zone_high,
        "price": pool.zone_mid,
        "internal_or_external": pool.internal_or_external,
        "swept_status": pool.swept_status,
        "quality_score": pool.quality_score,
        "target_priority_score": pool.target_priority_score,
        "timeframe": pool.timeframe,
        "session_source": pool.session_source,
        "touched_count": pool.touched_count,
        "created_index": pool.created_index,
        "last_touched_index": pool.last_touched_index,
        "invalidated": pool.invalidated,
    }


def _htf_aligned(context: Mapping[str, Any], path_bias: LiquidityPathBias) -> bool:
    htf_bias = _direction_text(context.get("htf_bias"))
    expected_draw = str(context.get("expected_draw") or "").lower()
    if path_bias in {LiquidityPathBias.BULLISH, LiquidityPathBias.BULLISH_CANDIDATE}:
        return htf_bias == "bullish" or expected_draw == "buy_side"
    if path_bias in {LiquidityPathBias.BEARISH, LiquidityPathBias.BEARISH_CANDIDATE}:
        return htf_bias == "bearish" or expected_draw == "sell_side"
    return False


def _path_confidence(score: float, path_bias: LiquidityPathBias, valid: bool) -> float:
    candidate_biases = {
        LiquidityPathBias.BULLISH_CANDIDATE,
        LiquidityPathBias.BEARISH_CANDIDATE,
    }
    if not valid and path_bias in candidate_biases:
        return round(min(score, 5.0), 2)
    return round(score if valid else min(score, 4.0), 2)


def _path_id(path_bias: LiquidityPathBias, start: _LiquidityPool, target: Mapping[str, Any]) -> str:
    prefix = "L2L_BULL" if "bullish" in path_bias.value else "L2L_BEAR"
    return f"{prefix}_{start.liquidity_id}_TO_{target['liquidity_id']}"


def _recent_score(pool: _LiquidityPool, current_index: int | None) -> float:
    if current_index is None or pool.last_touched_index is None:
        return float(pool.last_touched_index or 0)
    distance = max(0, current_index - pool.last_touched_index)
    return max(0.0, 1000.0 - distance)


def _start_status_score(status: str) -> float:
    if "reclaimed" in status or "rejected" in status:
        return 3.0
    if "swept" in status:
        return 2.0
    if "accepted" in status:
        return 1.0
    return 0.0


def _infer_current_price(pools: Sequence[_LiquidityPool]) -> float:
    if not pools:
        return 0.0
    return sum(pool.zone_mid for pool in pools) / len(pools)


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if value is None:
        return {}
    data: dict[str, Any] = {}
    for key in dir(value):
        if key.startswith("_"):
            continue
        try:
            attr = getattr(value, key)
        except Exception:
            continue
        if not callable(attr):
            data[key] = attr
    return data


def _direction_text(value: Any) -> str | None:
    text = str(value or "").lower()
    if text in {"bullish", "buy", "long", "up", "buy_side"}:
        return "bullish"
    if text in {"bearish", "sell", "short", "down", "sell_side"}:
        return "bearish"
    return None


def _float(value: Any, default: float) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any, default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "confirmed"}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
