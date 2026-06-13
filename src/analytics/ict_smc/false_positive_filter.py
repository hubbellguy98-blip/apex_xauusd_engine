"""False-positive filters for already-detected ICT/SMC setups.

The detector modules can find many chart patterns. This layer decides whether
those detected patterns are tradable, context-only, or false positives. It does
not create setups and it does not submit orders.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence


class FalsePositiveStatus(str, Enum):
    VALID = "valid"
    CONTEXT_ONLY = "context_only"
    REJECTED = "rejected"


class FalsePositiveCategory(str, Enum):
    DATA = "data_failure"
    FVG = "fvg_failure"
    ORDER_BLOCK = "poi_failure"
    LIQUIDITY = "liquidity_failure"
    STRUCTURE = "structure_failure"
    HTF = "htf_conflict"
    RISK = "risk_failure"
    MARKET = "market_condition_failure"
    NEWS = "news_failure"
    DUPLICATE = "duplicate_signal"
    TARGET = "target_failure"


class FilterMode(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    PERMISSIVE = "permissive"


def filter_false_smc_signals(
    setups: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any,
    context: Mapping[str, Any] | Any,
) -> dict[str, Any]:
    """Filter already-detected ICT/SMC setups before entry/risk/execution."""

    ctx = _mapping(context)
    config = _filter_config(ctx)
    records = _records(setups)
    valid_setups: list[dict[str, Any]] = []
    context_only_setups: list[dict[str, Any]] = []
    rejected_setups: list[dict[str, Any]] = []
    rejection_reasons: dict[str, int] = {}

    for setup in records:
        evaluation = _evaluate_setup(setup, ctx, config)
        for reason in evaluation["rejection_reasons"]:
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
        if evaluation["status"] == FalsePositiveStatus.REJECTED.value:
            rejected_setups.append(evaluation)
        elif evaluation["status"] == FalsePositiveStatus.CONTEXT_ONLY.value:
            context_only_setups.append(evaluation)
        else:
            valid_setups.append(evaluation)

    valid_setups, duplicate_rejections = _reject_overlapping_valid_setups(valid_setups)
    rejected_setups.extend(duplicate_rejections)
    for rejected in duplicate_rejections:
        for reason in rejected["rejection_reasons"]:
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    valid_setups.sort(key=lambda item: (item["filtered_score"], item.get("rr") or 0.0), reverse=True)
    highest_quality = valid_setups[0] if valid_setups else None

    return {
        "function": "filter_false_smc_signals",
        "symbol": str(_get(ctx, "symbol", default="XAUUSD")),
        "timeframe": str(_get(ctx, "timeframe", default="multi")),
        "filter_time": _filter_time(ctx),
        "valid_setups": valid_setups,
        "context_only_setups": context_only_setups,
        "rejected_setups": rejected_setups,
        "rejection_reasons": rejection_reasons,
        "warnings": _summary_warnings(valid_setups, context_only_setups),
        "highest_quality_setup": highest_quality,
        "filter_summary": {
            "total_setups_checked": len(records),
            "valid_count": len(valid_setups),
            "context_only_count": len(context_only_setups),
            "rejected_count": len(rejected_setups),
            "highest_quality_setup_id": highest_quality["setup_id"] if highest_quality else None,
        },
    }


def _evaluate_setup(setup: Mapping[str, Any], context: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    setup_id = str(_get(setup, "setup_id", "signal_id", "id", default="unknown_setup"))
    setup_type = str(_get(setup, "setup_type", "type", default="unknown_setup_type"))
    direction = _direction(_get(setup, "direction", "bias", "side", default=None))
    score = _float(_get(setup, "setup_score", "score", "quality_score", default=0.0), 0.0) or 0.0
    rr = _rr(setup)
    hard_reasons: list[str] = []
    context_reasons: list[str] = []
    warnings: list[str] = []
    passed_filters: list[str] = []
    soft_penalty = 0.0

    _data_filter(setup, direction, rr, hard_reasons, passed_filters)
    soft_penalty += _execution_context_filter(setup, context, config, hard_reasons, warnings, passed_filters)
    soft_penalty += _fvg_filter(setup, context, config, hard_reasons, warnings, passed_filters)
    soft_penalty += _order_block_filter(setup, config, hard_reasons, warnings, passed_filters)
    soft_penalty += _liquidity_filter(setup, config, hard_reasons, context_reasons, warnings, passed_filters)
    soft_penalty += _structure_filter(setup, config, hard_reasons, context_reasons, passed_filters)
    soft_penalty += _htf_filter(setup, context, config, direction, score, rr, hard_reasons, warnings, passed_filters)
    soft_penalty += _market_condition_filter(setup, context, config, score, hard_reasons, warnings, passed_filters)
    soft_penalty += _range_location_filter(setup, context, hard_reasons, warnings, passed_filters)
    _target_filter(setup, hard_reasons, passed_filters)
    _rr_filter(setup, config, rr, hard_reasons, passed_filters)
    _staleness_filter(setup, config, hard_reasons, passed_filters)
    soft_penalty += _frequency_filter(setup, context, config, hard_reasons, warnings, passed_filters)

    filtered_score = max(0.0, score - soft_penalty)
    if not hard_reasons and not context_reasons and filtered_score < float(config["minimum_tradable_score"]):
        context_reasons.append("soft_penalty_score_below_tradable_threshold")

    if hard_reasons:
        status = FalsePositiveStatus.REJECTED
        category = _rejection_category(hard_reasons)
    elif context_reasons:
        status = FalsePositiveStatus.CONTEXT_ONLY
        category = "context_only"
    else:
        status = FalsePositiveStatus.VALID
        category = None

    reasons = _dedupe(hard_reasons + context_reasons)
    return {
        "setup_id": setup_id,
        "setup_type": setup_type,
        "direction": direction,
        "status": status.value,
        "setup_score": round(score, 2),
        "filtered_score": round(filtered_score, 2),
        "rr": _round(rr),
        "rejection_category": category,
        "rejection_reasons": reasons,
        "failed_filters": reasons,
        "warnings": _dedupe(warnings),
        "passed_filters": _dedupe(passed_filters),
        "original_setup": setup,
    }


def _data_filter(
    setup: Mapping[str, Any],
    direction: str,
    rr: float | None,
    hard: list[str],
    passed: list[str],
) -> None:
    if _bool(_get(setup, "uses_closed_candles", "confirmed_closed_candle", default=True)) is False:
        hard.append("uses_unclosed_candle")
    else:
        passed.append("closed_candle_only")
    if direction not in {"bullish", "bearish"}:
        hard.append("invalid_direction")
    if _float(_get(setup, "entry", "entry_price", default=None)) is None:
        hard.append("missing_entry")
    if _float(_get(setup, "stop", "stop_loss", default=None)) is None:
        hard.append("missing_stop")
    if _float(_get(setup, "target", "take_profit", "final_target", default=None)) is None:
        hard.append("missing_target")
    if rr is None:
        hard.append("missing_rr")


def _execution_context_filter(
    setup: Mapping[str, Any],
    context: Mapping[str, Any],
    config: Mapping[str, Any],
    hard: list[str],
    warnings: list[str],
    passed: list[str],
) -> float:
    penalty = 0.0
    news_status = _merge_context(setup, context, "news_status")
    spread_status = _merge_context(setup, context, "spread_status")
    news_blocked = False
    if _bool(_get(news_status, "restricted", "blackout_active", "news_restricted", default=False)):
        hard.append("news_restricted")
        news_blocked = True
    if _bool(_get(news_status, "first_news_spike", "first_spike_signal", default=False)):
        hard.append("first_news_spike_signal")
        news_blocked = True
    if _bool(_get(news_status, "post_news_structure_unstable", "structure_unstable", default=False)):
        hard.append("post_news_structure_unstable")
        news_blocked = True
    if not news_blocked:
        passed.append("news_safe")

    current_spread = _float(_get(spread_status, "current_spread", "spread", default=0.0), 0.0) or 0.0
    max_spread = _float(_get(spread_status, "max_allowed_spread", "max_spread", default=config["max_spread"]), config["max_spread"])
    if max_spread and current_spread > max_spread:
        hard.append("spread_too_high")
    else:
        passed.append("spread_safe")
    slippage = _float(_get(spread_status, "estimated_slippage", "slippage", default=0.0), 0.0) or 0.0
    max_slippage = _float(_get(spread_status, "max_allowed_slippage", default=config["max_slippage"]), config["max_slippage"])
    if max_slippage and slippage > max_slippage:
        hard.append("slippage_risk_too_high")
    rollover = _bool(_get(context, "rollover_execution_risk", default=False))
    if rollover:
        hard.append("rollover_execution_risk")
    if current_spread > 0 and max_spread and current_spread > max_spread * 0.75:
        warnings.append("spread_near_maximum_reduce_confidence")
        penalty += 0.5
    return penalty


def _fvg_filter(
    setup: Mapping[str, Any],
    context: Mapping[str, Any],
    config: Mapping[str, Any],
    hard: list[str],
    warnings: list[str],
    passed: list[str],
) -> float:
    fvg = _mapping(_get(setup, "fvg", default={}))
    if not fvg:
        return 0.0
    penalty = 0.0
    if _bool(_get(fvg, "active_status", "is_active", default=True)) is False:
        hard.append("fvg_inactive")
    if (_float(_get(fvg, "filled_percent", default=0.0), 0.0) or 0.0) >= 100.0:
        hard.append("fvg_already_filled")
    elif (_float(_get(fvg, "filled_percent", default=0.0), 0.0) or 0.0) >= 50.0:
        warnings.append("fvg_partially_filled")
        penalty += 0.5
    if _bool(_get(fvg, "created_by_displacement", "displacement_created", default=True)) is False:
        hard.append("random_fvg_no_displacement")
    else:
        passed.append("displacement_confirmed")
    if _bool(_get(setup, "requires_structure_confirmation", default=False)) and _bool(
        _get(fvg, "created_after_mss_bos", "after_structure_shift", default=False)
    ) is False:
        hard.append("random_fvg_no_structure_confirmation")
    fvg_size = _float(_get(fvg, "size", "gap_size", default=0.0), 0.0) or 0.0
    if fvg_size and fvg_size < float(config["minimum_fvg_size"]):
        hard.append("fvg_too_small_noise")
    atr = _float(_get(context, "atr", "average_true_range", default=0.0), 0.0) or 0.0
    if atr and fvg_size > atr * float(config["max_fvg_size_atr_multiplier"]) and _news_related(setup, context):
        hard.append("fvg_too_large_news_spike")
    if not _has_target_liquidity(setup):
        hard.append("fvg_no_target_liquidity")
    if not any(reason.startswith("fvg_") or reason.startswith("random_fvg") for reason in hard):
        passed.append("fvg_valid")
    return penalty


def _order_block_filter(
    setup: Mapping[str, Any],
    config: Mapping[str, Any],
    hard: list[str],
    warnings: list[str],
    passed: list[str],
) -> float:
    ob = _mapping(_get(setup, "order_block", "ob", default={}))
    if not ob:
        return 0.0
    penalty = 0.0
    if _bool(_get(ob, "created_by_displacement", default=True)) is False:
        hard.append("weak_ob_no_displacement")
    if _bool(_get(ob, "validated_by_mss_bos", "validated_by_structure_break", default=True)) is False:
        hard.append("weak_ob_no_structure_break")
    if _bool(_get(ob, "active_status", "is_active", default=True)) is False or _bool(_get(ob, "failed_status", default=False)):
        hard.append("ob_already_failed")
    if (_float(_get(ob, "mitigated_count", default=0.0), 0.0) or 0.0) > float(config["max_allowed_mitigations"]):
        hard.append("ob_over_mitigated")
    quality = _float(_get(ob, "quality_score", default=10.0), 10.0) or 10.0
    if quality < float(config["minimum_ob_quality"]):
        hard.append("ob_quality_too_low")
    elif quality < float(config["minimum_ob_quality"]) + 1.0:
        warnings.append("order_block_quality_near_minimum")
        penalty += 0.5
    if _bool(_get(ob, "invalidated_by_close", "closed_through_invalidation", default=False)):
        hard.append("ob_invalidated_by_close")
    if _bool(_get(setup, "stop_inside_poi", "stop_inside_ob", default=False)):
        hard.append("ob_stop_inside_zone")
    if not any(reason.startswith("ob_") or reason.startswith("weak_ob") for reason in hard):
        passed.append("order_block_valid")
    return penalty


def _liquidity_filter(
    setup: Mapping[str, Any],
    config: Mapping[str, Any],
    hard: list[str],
    context_only: list[str],
    warnings: list[str],
    passed: list[str],
) -> float:
    sweep = _mapping(_get(setup, "liquidity_sweep", "sweep", default={}))
    if not _bool(_get(setup, "requires_liquidity_sweep", default=False)):
        if not sweep:
            warnings.append("liquidity_sweep_not_required_but_absent")
            return 0.5
        passed.append("liquidity_context_present")
        return 0.0
    if not sweep or _bool(_get(sweep, "exists", "sweep_confirmed", default=False)) is False:
        hard.append("missing_required_liquidity_sweep")
        return 0.0
    reclaim = str(_get(sweep, "reclaim_status", "reaction_status", default="")).lower()
    if reclaim in {"", "none", "failed", "no_reclaim", "not_reclaimed"}:
        hard.append("sweep_no_reclaim_or_rejection")
    if str(_get(sweep, "swept_liquidity_status", "swept_status", default="fresh")).lower() in {"already_swept", "fully_swept"}:
        hard.append("swept_liquidity_already_swept")
    if _bool(_get(sweep, "connected_to_setup", default=True)) is False:
        hard.append("sweep_not_connected_to_setup")
    if (_float(_get(sweep, "depth", "sweep_depth", default=1.0), 1.0) or 0.0) < float(config["minimum_sweep_depth"]):
        hard.append("sweep_too_small")
    quality = _float(_get(sweep, "quality_score", default=10.0), 10.0) or 10.0
    if quality < float(config["minimum_sweep_quality"]):
        context_only.append("weak_sweep_context_only")
    else:
        passed.append("liquidity_sweep_confirmed")
    return 0.0


def _structure_filter(
    setup: Mapping[str, Any],
    config: Mapping[str, Any],
    hard: list[str],
    context_only: list[str],
    passed: list[str],
) -> float:
    structure = _mapping(_get(setup, "mss_bos", "structure", default={}))
    mode = FilterMode(str(config["mode"]))
    if _bool(_get(setup, "is_reversal_model", default=False)):
        if _bool(_get(structure, "mss_confirmed", "mss", default=False)) is False:
            if mode is FilterMode.CONSERVATIVE:
                hard.append("no_mss_for_reversal")
            else:
                context_only.append("no_mss_for_reversal")
        else:
            passed.append("mss_confirmed")
    if _bool(_get(setup, "is_continuation_model", default=False)):
        if _bool(_get(structure, "bos_confirmed", "bos", default=False)) is False:
            hard.append("no_bos_for_continuation")
        else:
            passed.append("bos_confirmed")
    if str(_get(structure, "confirmation_type", default="close")).lower() in {"wick_only", "wick"}:
        hard.append("wick_only_structure_break")
    if _bool(_get(structure, "weak_swing_break", default=False)):
        hard.append("weak_swing_structure_break")
    if _bool(_get(structure, "break_against_setup", default=False)):
        hard.append("structure_break_against_setup")
    return 0.0


def _htf_filter(
    setup: Mapping[str, Any],
    context: Mapping[str, Any],
    config: Mapping[str, Any],
    direction: str,
    score: float,
    rr: float | None,
    hard: list[str],
    warnings: list[str],
    passed: list[str],
) -> float:
    penalty = 0.0
    bias = _mapping(_get(context, "htf_bias", default={}))
    bias_direction = _direction(_get(bias, "direction", "bias", default=None))
    bias_strength = _float(_get(bias, "strength", "confidence", default=0.0), 0.0) or 0.0
    if bias_direction in {"bullish", "bearish"} and direction in {"bullish", "bearish"} and bias_direction != direction:
        warnings.append("htf_bias_conflict")
        penalty += 1.0
        if bias_strength >= float(config["strong_htf_bias_threshold"]) and score < float(config["strong_countertrend_score"]):
            hard.append("htf_bias_conflict")
    elif bias_direction == direction:
        passed.append("htf_bias_aligned")

    blocker = _htf_poi_blocker(setup, context, config, direction)
    if blocker is not None:
        warnings.append("htf_poi_blocks_target")
        if not _closer_target_meets_rr(setup, blocker, rr, config):
            hard.extend(["htf_poi_blocks_target", "no_target_meets_min_rr"])
    else:
        passed.append("target_path_unblocked")
    return penalty


def _market_condition_filter(
    setup: Mapping[str, Any],
    context: Mapping[str, Any],
    config: Mapping[str, Any],
    score: float,
    hard: list[str],
    warnings: list[str],
    passed: list[str],
) -> float:
    market = _mapping(_get(context, "market_condition", default={}))
    state = str(_get(market, "state", "condition", "structure_state", default="")).lower()
    penalty = 0.0
    if state in {"choppy", "ranging", "range", "sideways"}:
        warnings.append("choppy_market")
        penalty += 1.0
        if score < float(config["high_quality_threshold"]):
            hard.append("choppy_market")
    alternating = _float(_get(market, "alternating_structure_breaks", default=0.0), 0.0) or 0.0
    if alternating >= float(config["max_alternating_structure_breaks"]):
        hard.append("alternating_structure_noise")
    overlap = _float(_get(market, "overlap_ratio", default=0.0), 0.0) or 0.0
    if overlap > float(config["max_overlap_ratio"]):
        hard.append("overlapping_candle_noise")
    displacement = _mapping(_get(setup, "displacement", default={}))
    displacement_strength = _float(_get(displacement, "strength_score", "score", default=10.0), 10.0) or 10.0
    if state in {"choppy", "ranging", "range", "sideways"} and displacement_strength < float(config["minimum_displacement_in_chop"]):
        hard.append("displacement_too_weak_in_chop")
    if state not in {"choppy", "ranging", "range", "sideways"}:
        passed.append("market_condition_safe")
    return penalty


def _range_location_filter(
    setup: Mapping[str, Any],
    context: Mapping[str, Any],
    hard: list[str],
    warnings: list[str],
    passed: list[str],
) -> float:
    location = _range_location(setup, context)
    sweep = _mapping(_get(setup, "liquidity_sweep", default={}))
    if location in {"equilibrium", "middle", "midrange"}:
        if not _bool(_get(setup, "has_htf_poi_confluence", default=False)) and not _bool(_get(sweep, "exists", default=False)):
            hard.append("price_in_middle_of_range")
        else:
            warnings.append("price_near_equilibrium_reduce_confidence")
    elif location:
        passed.append("premium_discount_edge_present")
    entry_location = str(_get(_mapping(_get(setup, "premium_discount", default={})), "entry_location", default="")).lower()
    direction = _direction(_get(setup, "direction", default=None))
    if direction == "bullish" and entry_location == "premium" and _bool(_get(setup, "target_is_close", default=False)):
        hard.append("bullish_entry_in_premium_with_poor_target")
    if direction == "bearish" and entry_location == "discount" and _bool(_get(setup, "target_is_close", default=False)):
        hard.append("bearish_entry_in_discount_with_poor_target")
    return 0.0


def _target_filter(setup: Mapping[str, Any], hard: list[str], passed: list[str]) -> None:
    target = _mapping(_get(setup, "target_liquidity", "target", default={}))
    if str(_get(target, "swept_status", default="fresh")).lower() in {"fully_swept", "already_swept"}:
        hard.append("target_liquidity_already_swept")
    if _bool(_get(setup, "target_reached_before_entry", default=False)):
        hard.append("target_reached_before_entry")
    if target and not any(reason.startswith("target_") for reason in hard):
        passed.append("target_unswept")


def _rr_filter(
    setup: Mapping[str, Any],
    config: Mapping[str, Any],
    rr: float | None,
    hard: list[str],
    passed: list[str],
) -> None:
    if rr is None:
        return
    if rr <= 0:
        hard.append("invalid_risk_reward")
    elif rr < float(config["min_rr"]):
        hard.append("rr_below_minimum")
    else:
        passed.append("rr_valid")
    if _bool(_get(setup, "stop_inside_poi", default=False)):
        hard.append("stop_inside_poi")


def _staleness_filter(
    setup: Mapping[str, Any],
    config: Mapping[str, Any],
    hard: list[str],
    passed: list[str],
) -> None:
    age = _float(_get(setup, "age_candles", default=0.0), 0.0) or 0.0
    if age > float(config["max_setup_age"]):
        hard.append("setup_stale")
    elif not _bool(_get(setup, "session_window_expired", default=False)):
        passed.append("setup_fresh")
    if _bool(_get(setup, "session_window_expired", default=False)):
        hard.append("session_window_expired")
    if _bool(_get(setup, "opposite_structure_after_setup", default=False)):
        hard.append("opposite_structure_after_setup")


def _frequency_filter(
    setup: Mapping[str, Any],
    context: Mapping[str, Any],
    config: Mapping[str, Any],
    hard: list[str],
    warnings: list[str],
    passed: list[str],
) -> float:
    recent = _records(_get(context, "recent_signals", default=[]))
    max_recent = int(config["max_recent_signals"])
    if max_recent > 0 and len(recent) > max_recent:
        hard.append("too_many_signals_recently")
    setup_poi = str(_get(setup, "poi_id", default=_get(_mapping(_get(setup, "poi", default={})), "poi_id", default="")))
    setup_sweep = str(_get(setup, "sweep_id", default=_get(_mapping(_get(setup, "liquidity_sweep", default={})), "sweep_id", "liquidity_id", default="")))
    for item in recent:
        if setup_poi and setup_poi == str(_get(item, "poi_id", default="")):
            hard.append("duplicate_signal_same_poi")
            break
        if setup_sweep and setup_sweep == str(_get(item, "sweep_id", "liquidity_id", default="")):
            hard.append("same_sweep_event_duplicate")
            break
    if _bool(_get(context, "signal_cooldown_active", default=False)):
        hard.append("signal_cooldown_active")
    if not hard:
        passed.append("frequency_safe")
    if len(recent) > max_recent * 0.75 if max_recent else False:
        warnings.append("recent_signal_frequency_elevated")
        return 0.5
    return 0.0


def _reject_overlapping_valid_setups(valid_setups: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for setup in sorted(valid_setups, key=lambda item: (item["filtered_score"], item.get("rr") or 0.0), reverse=True):
        key = _overlap_key(setup)
        if key not in kept:
            kept[key] = setup
            continue
        lower = dict(setup)
        lower["status"] = FalsePositiveStatus.REJECTED.value
        lower["rejection_category"] = FalsePositiveCategory.DUPLICATE.value
        lower["rejection_reasons"] = _dedupe(lower["rejection_reasons"] + ["overlapping_setup"])
        lower["failed_filters"] = lower["rejection_reasons"]
        rejected.append(lower)
    return list(kept.values()), rejected


def _overlap_key(setup: Mapping[str, Any]) -> str:
    raw = _mapping(_get(setup, "original_setup", default={}))
    poi = _get(raw, "poi_id", default=_get(_mapping(_get(raw, "poi", default={})), "poi_id", default=None))
    sweep = _get(raw, "sweep_id", default=_get(_mapping(_get(raw, "liquidity_sweep", default={})), "sweep_id", "liquidity_id", default=None))
    if poi:
        return f"poi:{poi}"
    if sweep:
        return f"sweep:{sweep}"
    return f"setup:{setup['setup_id']}"


def _htf_poi_blocker(
    setup: Mapping[str, Any],
    context: Mapping[str, Any],
    config: Mapping[str, Any],
    direction: str,
) -> Mapping[str, Any] | None:
    entry = _float(_get(setup, "entry", "entry_price", default=None))
    target = _float(_get(setup, "target", "take_profit", "final_target", default=None))
    if entry is None or target is None:
        return None
    threshold = float(config["htf_blocker_quality_threshold"])
    htf_timeframes = set(config["htf_timeframes"])
    for poi in _records(_get(context, "poi_zones", default=[])):
        poi_direction = _direction(_get(poi, "direction", default=None))
        timeframe = str(_get(poi, "timeframe", default="")).lower()
        quality = _float(_get(poi, "quality_score", default=0.0), 0.0) or 0.0
        invalidated = _bool(_get(poi, "invalidated", "invalidated_status", default=False))
        if invalidated or quality < threshold or timeframe not in htf_timeframes:
            continue
        zone_low = _float(_get(poi, "zone_low", "low", default=None))
        zone_high = _float(_get(poi, "zone_high", "high", default=None))
        if zone_low is None or zone_high is None:
            continue
        if direction == "bullish" and poi_direction == "bearish" and entry < zone_low < target:
            return poi
        if direction == "bearish" and poi_direction == "bullish" and target < zone_high < entry:
            return poi
    return None


def _closer_target_meets_rr(
    setup: Mapping[str, Any],
    blocker: Mapping[str, Any],
    rr: float | None,
    config: Mapping[str, Any],
) -> bool:
    closer_rr = _float(_get(setup, "closer_target_rr", default=None))
    if closer_rr is not None:
        return closer_rr >= float(config["min_rr"])
    return False


def _rr(setup: Mapping[str, Any]) -> float | None:
    rr = _float(_get(setup, "rr", "risk_reward", default=None))
    if rr is not None:
        return rr
    entry = _float(_get(setup, "entry", "entry_price", default=None))
    stop = _float(_get(setup, "stop", "stop_loss", default=None))
    target = _float(_get(setup, "target", "take_profit", "final_target", default=None))
    direction = _direction(_get(setup, "direction", default=None))
    if entry is None or stop is None or target is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    reward = target - entry if direction == "bullish" else entry - target
    return reward / risk


def _filter_config(context: Mapping[str, Any]) -> dict[str, Any]:
    raw = _mapping(_get(context, "filter_config", default={}))
    return {
        "mode": str(_get(raw, "mode", default=FilterMode.CONSERVATIVE.value)).lower(),
        "min_rr": _float(_get(raw, "min_rr", "minimum_rr", default=1.5), 1.5) or 1.5,
        "minimum_tradable_score": _float(_get(raw, "minimum_tradable_score", default=6.0), 6.0) or 6.0,
        "minimum_fvg_size": _float(_get(raw, "minimum_fvg_size", default=0.05), 0.05) or 0.05,
        "max_fvg_size_atr_multiplier": _float(_get(raw, "max_fvg_size_atr_multiplier", default=3.0), 3.0) or 3.0,
        "minimum_ob_quality": _float(_get(raw, "minimum_ob_quality", default=6.0), 6.0) or 6.0,
        "max_allowed_mitigations": _float(_get(raw, "max_allowed_mitigations", default=2.0), 2.0) or 2.0,
        "minimum_sweep_depth": _float(_get(raw, "minimum_sweep_depth", default=0.02), 0.02) or 0.02,
        "minimum_sweep_quality": _float(_get(raw, "minimum_sweep_quality", default=5.0), 5.0) or 5.0,
        "strong_htf_bias_threshold": _float(_get(raw, "strong_htf_bias_threshold", default=7.0), 7.0) or 7.0,
        "strong_countertrend_score": _float(_get(raw, "strong_countertrend_score", default=8.5), 8.5) or 8.5,
        "htf_blocker_quality_threshold": _float(_get(raw, "htf_blocker_quality_threshold", default=7.5), 7.5) or 7.5,
        "htf_timeframes": tuple(str(tf).lower() for tf in _get(raw, "htf_timeframes", default=("1h", "4h", "daily", "d1"))),
        "high_quality_threshold": _float(_get(raw, "high_quality_threshold", default=8.0), 8.0) or 8.0,
        "minimum_displacement_in_chop": _float(_get(raw, "minimum_displacement_in_chop", default=7.0), 7.0) or 7.0,
        "max_alternating_structure_breaks": _float(_get(raw, "max_alternating_structure_breaks", default=3.0), 3.0) or 3.0,
        "max_overlap_ratio": _float(_get(raw, "max_overlap_ratio", default=0.60), 0.60) or 0.60,
        "max_setup_age": _float(_get(raw, "max_setup_age", default=12.0), 12.0) or 12.0,
        "max_recent_signals": int(_float(_get(raw, "max_recent_signals", default=8), 8) or 8),
        "max_spread": _float(_get(raw, "max_spread", "max_allowed_spread", default=0.8), 0.8) or 0.8,
        "max_slippage": _float(_get(raw, "max_slippage", "max_allowed_slippage", default=0.5), 0.5) or 0.5,
    }


def _merge_context(setup: Mapping[str, Any], context: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    setup_value = _mapping(_get(setup, key, default={}))
    context_value = _mapping(_get(context, key, default={}))
    return {**context_value, **setup_value}


def _has_target_liquidity(setup: Mapping[str, Any]) -> bool:
    target = _mapping(_get(setup, "target_liquidity", default={}))
    if target:
        return str(_get(target, "swept_status", default="fresh")).lower() not in {"fully_swept", "already_swept"}
    return _bool(_get(setup, "has_target_liquidity", default=False))


def _news_related(setup: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    return bool(
        _bool(_get(setup, "news_related", default=False))
        or _bool(_get(_mapping(_get(setup, "news_status", default={})), "first_news_spike", default=False))
        or _bool(_get(_mapping(_get(context, "news_status", default={})), "restricted", default=False))
    )


def _range_location(setup: Mapping[str, Any], context: Mapping[str, Any]) -> str:
    setup_pd = _mapping(_get(setup, "premium_discount", default={}))
    ctx_pd = _mapping(_get(context, "premium_discount", default={}))
    dealing_range = _mapping(_get(context, "dealing_range", default={}))
    return str(
        _get(
            setup_pd,
            "current_price_location",
            "entry_location",
            default=_get(ctx_pd, "current_price_location", default=_get(dealing_range, "current_price_location", default="")),
        )
    ).lower()


def _rejection_category(reasons: Sequence[str]) -> str:
    categories = {
        FalsePositiveCategory.DATA.value: {"uses_unclosed_candle", "invalid_direction", "missing_entry", "missing_stop", "missing_target", "missing_rr"},
        FalsePositiveCategory.FVG.value: {"random_fvg_no_displacement", "random_fvg_no_structure_confirmation", "fvg_inactive", "fvg_already_filled", "fvg_too_small_noise", "fvg_too_large_news_spike", "fvg_no_target_liquidity"},
        FalsePositiveCategory.ORDER_BLOCK.value: {"weak_ob_no_displacement", "weak_ob_no_structure_break", "ob_already_failed", "ob_over_mitigated", "ob_quality_too_low", "ob_invalidated_by_close", "ob_stop_inside_zone"},
        FalsePositiveCategory.LIQUIDITY.value: {"missing_required_liquidity_sweep", "sweep_too_small", "sweep_no_reclaim_or_rejection", "swept_liquidity_already_swept", "sweep_not_connected_to_setup"},
        FalsePositiveCategory.STRUCTURE.value: {"no_mss_for_reversal", "no_bos_for_continuation", "wick_only_structure_break", "weak_swing_structure_break", "structure_break_against_setup"},
        FalsePositiveCategory.HTF.value: {"htf_bias_conflict", "htf_poi_blocks_target", "daily_poi_blocks_trade_path", "h1_draw_opposes_setup"},
        FalsePositiveCategory.RISK.value: {"rr_below_minimum", "invalid_risk_reward", "stop_inside_poi", "stop_too_wide_for_target", "no_target_meets_min_rr"},
        FalsePositiveCategory.MARKET.value: {"choppy_market", "no_clear_direction", "alternating_structure_noise", "overlapping_candle_noise", "price_in_middle_of_range"},
        FalsePositiveCategory.NEWS.value: {"news_restricted", "first_news_spike_signal", "post_news_structure_unstable", "spread_too_high", "slippage_risk_too_high", "rollover_execution_risk"},
        FalsePositiveCategory.DUPLICATE.value: {"duplicate_signal_same_poi", "same_sweep_event_duplicate", "too_many_signals_recently", "signal_cooldown_active", "overlapping_setup"},
        FalsePositiveCategory.TARGET.value: {"target_liquidity_already_swept", "target_reached_before_entry"},
    }
    reason_set = set(reasons)
    for category, known in categories.items():
        if reason_set & known:
            return category
    return "unknown_failure"


def _summary_warnings(valid: Sequence[Mapping[str, Any]], context_only: Sequence[Mapping[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if not valid and context_only:
        warnings.append("only_context_setups_available_no_tradeable_signal")
    if not valid and not context_only:
        warnings.append("all_detected_setups_filtered_out")
    return warnings


def _filter_time(context: Mapping[str, Any]) -> str:
    raw = _get(context, "current_time", "timestamp", default=None)
    if isinstance(raw, datetime):
        value = raw
    elif isinstance(raw, str) and raw:
        return raw
    else:
        value = datetime.now(timezone.utc)
    return value.isoformat()


def _direction(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"long", "buy", "bull", "bullish", "buy_side"}:
        return "bullish"
    if raw in {"short", "sell", "bear", "bearish", "sell_side"}:
        return "bearish"
    return "none"


def _records(values: Any) -> list[Mapping[str, Any]]:
    if values is None:
        return []
    if isinstance(values, Mapping):
        return [values]
    try:
        return [_mapping(item) for item in values]
    except TypeError:
        return [_mapping(values)]


def _mapping(value: Mapping[str, Any] | Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "__dict__"):
        return vars(value)
    return {}


def _get(mapping: Mapping[str, Any] | Any, *keys: str, default: Any = None) -> Any:
    data = _mapping(mapping)
    for key in keys:
        if key in data:
            return data[key]
    return default


def _float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "active", "confirmed", "valid"}
    return bool(value)


def _round(value: Any, digits: int = 4) -> float | None:
    numeric = _float(value)
    if numeric is None:
        return None
    return round(numeric, digits)


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
