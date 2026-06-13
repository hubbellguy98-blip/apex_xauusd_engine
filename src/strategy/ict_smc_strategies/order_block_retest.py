"""Order Block Retest After Liquidity Sweep strategy model.

The model is treated as a full ICT/SMC sequence, not as "any opposite candle":

liquidity sweep -> reclaim/rejection -> displacement and structure break ->
last opposite candle order block -> retest -> reaction confirmation ->
stop/target/RR -> scoring.

This layer is pure Python and uses only closed candles. It returns plain
dictionaries for tests, backtests, and later orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence


class OBRetestDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"


class OBRetestStatus(str, Enum):
    VALID = "valid"
    REJECTED = "rejected"
    WAITING_FOR_RETEST = "waiting_for_retest"


class OBEntryMode(str, Enum):
    AGGRESSIVE = "aggressive"
    BALANCED = "balanced"
    CONSERVATIVE = "conservative"


class OBConfirmationMode(str, Enum):
    AGGRESSIVE = "aggressive"
    CANDLE_REACTION = "candle_reaction"
    LTF_MSS = "ltf_mss"


class OBZoneMode(str, Enum):
    FULL_RANGE = "full_range"
    BODY_RANGE = "body_range"


@dataclass(frozen=True, slots=True)
class _Candle:
    position: int
    index: int
    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    is_closed: bool = True

    @property
    def range(self) -> float:
        return max(self.high - self.low, 1e-9)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_to_range(self) -> float:
        return self.body / self.range

    @property
    def bullish_close_position(self) -> float:
        return (self.close - self.low) / self.range

    @property
    def bearish_close_position(self) -> float:
        return (self.high - self.close) / self.range


def detect_liquidity_sweep(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    liquidity_pools: Sequence[Mapping[str, Any]] | None = None,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Detect reclaimed sell-side sweeps and rejected buy-side sweeps."""

    cfg = _config(config)
    candles = _closed_candles(df)
    if not candles:
        return []
    atr = _atr(candles, int(cfg["atr_period"]))
    sweep_buffer = _configured_buffer(cfg, "sweep_buffer", atr * float(cfg["sweep_buffer_atr_multiplier"]))
    events: list[dict[str, Any]] = []
    for pool in liquidity_pools or []:
        if _pool_swept(pool):
            continue
        side = _pool_side(pool)
        if side not in {"sell_side", "buy_side"}:
            continue
        low, high = _pool_bounds(pool)
        mid = (low + high) / 2.0
        pool_id = str(_get(pool, "liquidity_id", "pool_id", "id", default=f"LIQ_{side}_{low}_{high}"))
        for candle in candles:
            if side == "sell_side" and candle.low < low - sweep_buffer and candle.close > low:
                depth = low - candle.low
                strength = 6.5 + (1.0 if candle.close > mid else 0.0) + (1.0 if candle.close > high else 0.0)
                events.append(
                    _sweep_event(
                        OBRetestDirection.BULLISH,
                        "sell_side",
                        pool_id,
                        low,
                        low,
                        high,
                        mid,
                        candle,
                        depth,
                        "reclaim_status",
                        "reclaimed_above_swept_level",
                        strength,
                        atr,
                    )
                )
            if side == "buy_side" and candle.high > high + sweep_buffer and candle.close < high:
                depth = candle.high - high
                strength = 6.5 + (1.0 if candle.close < mid else 0.0) + (1.0 if candle.close < low else 0.0)
                events.append(
                    _sweep_event(
                        OBRetestDirection.BEARISH,
                        "buy_side",
                        pool_id,
                        high,
                        low,
                        high,
                        mid,
                        candle,
                        depth,
                        "rejection_status",
                        "rejected_below_swept_level",
                        strength,
                        atr,
                    )
                )
    return sorted(events, key=lambda item: (int(item["sweep_index"]), -float(item["quality_score"])))


def detect_displacement(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    sweep_event: Mapping[str, Any],
    swings: Sequence[Mapping[str, Any]] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Detect post-sweep displacement that breaks structure by close."""

    cfg = _config(config)
    direction = _direction(sweep_event.get("direction_bias"))
    candles = _closed_candles(df)
    if direction is OBRetestDirection.NONE or not candles:
        return None
    atr = _atr(candles, int(cfg["atr_period"]))
    break_buffer = _configured_buffer(cfg, "break_buffer", atr * float(cfg["break_buffer_atr_multiplier"]))
    sweep_index = int(sweep_event["sweep_index"])
    wait = int(cfg["max_displacement_wait_candles"])
    wanted_kind = "high" if direction is OBRetestDirection.BULLISH else "low"
    levels = [
        swing
        for swing in _confirmed_swings(candles, swings)
        if swing["kind"] == wanted_kind and int(swing["index"]) <= sweep_index + wait
    ]
    if not levels:
        return None
    level = (
        max(levels, key=lambda item: float(item["price"]))
        if wanted_kind == "high"
        else min(levels, key=lambda item: float(item["price"]))
    )
    for candle in candles:
        if candle.index <= sweep_index or candle.index > sweep_index + wait:
            continue
        broke = (
            candle.close > float(level["price"]) + break_buffer
            if direction is OBRetestDirection.BULLISH
            else candle.close < float(level["price"]) - break_buffer
        )
        directional_body = candle.bullish if direction is OBRetestDirection.BULLISH else candle.bearish
        close_position = (
            candle.bullish_close_position if direction is OBRetestDirection.BULLISH else candle.bearish_close_position
        )
        range_to_atr = candle.range / max(atr, 1e-9)
        displacement_ok = (
            broke
            and directional_body
            and candle.body_to_range >= float(cfg["displacement_min_body_to_range"])
            and range_to_atr >= float(cfg["displacement_min_range_to_atr"])
            and close_position >= float(cfg["displacement_min_close_position"])
        )
        if not displacement_ok:
            continue
        strength = 6.5 + min(candle.body_to_range * 2.0, 1.6) + min(range_to_atr, 2.0) * 0.7
        return {
            "direction": direction.value,
            "structure_break_type": f"{direction.value}_mss",
            "broken_swing_id": level["swing_id"],
            "broken_level": round(float(level["price"]), 5),
            "confirmed_by_close": True,
            "structure_break_confirmed": True,
            "displacement_confirmed": True,
            "confirmation_index": candle.index,
            "confirmation_time": candle.timestamp,
            "range_to_atr_ratio": round(range_to_atr, 3),
            "body_to_range_ratio": round(candle.body_to_range, 3),
            "close_position_score": round(close_position, 3),
            "strength_score": round(_clamp(strength, 0, 10), 2),
        }
    return None


def detect_order_block_after_sweep(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    sweep_event: Mapping[str, Any] | None,
    displacement: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Select the last opposite candle before displacement as the OB."""

    if sweep_event is None or displacement is None:
        return None
    cfg = _config(config)
    direction = _direction(displacement.get("direction"))
    candles = _closed_candles(df)
    if direction is OBRetestDirection.NONE or not candles:
        return None
    sweep_index = int(sweep_event["sweep_index"])
    displacement_index = int(displacement["confirmation_index"])
    opposite = [
        candle
        for candle in candles
        if sweep_index <= candle.index < displacement_index
        and (
            (direction is OBRetestDirection.BULLISH and candle.bearish)
            or (direction is OBRetestDirection.BEARISH and candle.bullish)
        )
    ]
    if not opposite:
        return None
    source = opposite[-1]
    full_low, full_high = source.low, source.high
    body_low, body_high = min(source.open, source.close), max(source.open, source.close)
    zone_mode = OBZoneMode(str(cfg["zone_mode"]).lower())
    zone_low, zone_high = (body_low, body_high) if zone_mode is OBZoneMode.BODY_RANGE else (full_low, full_high)
    atr = _atr(candles[: source.position + 1] or candles, int(cfg["atr_period"]))
    width = zone_high - zone_low
    width_to_atr = width / max(atr, 1e-9)
    reasons: list[str] = []
    if not bool(displacement.get("displacement_confirmed", False)):
        reasons.append("weak_ob_no_displacement")
    if not bool(displacement.get("structure_break_confirmed", False)):
        reasons.append("ob_not_validated_by_structure_break")
    if width_to_atr > float(cfg["max_ob_atr_multiplier"]):
        reasons.append("ob_too_wide")
    if source.range / max(atr, 1e-9) > float(cfg["max_news_spike_atr_multiplier"]):
        reasons.append("news_spike_order_block")
    quality = 6.5 + min(float(sweep_event.get("quality_score", 0)) / 10.0, 1.0)
    quality += min(float(displacement.get("strength_score", 0)) / 10.0, 1.0)
    quality -= max(0.0, width_to_atr - 1.0) * 0.8 + len(reasons) * 1.5
    mean_threshold = (zone_low + zone_high) / 2.0
    return {
        "ob_id": f"OB_{direction.value.upper()}_{source.index}_{displacement_index}",
        "ob_type": f"{direction.value}_order_block",
        "source_candle_index": source.index,
        "source_candle_time": source.timestamp,
        "direction": direction.value,
        "zone_mode": zone_mode.value,
        "zone_low": round(zone_low, 5),
        "zone_high": round(zone_high, 5),
        "full_zone_low": round(full_low, 5),
        "full_zone_high": round(full_high, 5),
        "body_low": round(body_low, 5),
        "body_high": round(body_high, 5),
        "mean_threshold": round(mean_threshold, 5),
        "created_after_sweep": source.index >= sweep_index,
        "valid_from_index": displacement_index,
        "valid_from_time": displacement.get("confirmation_time"),
        "displacement_confirmed": bool(displacement.get("displacement_confirmed", False)),
        "structure_break_confirmed": bool(displacement.get("structure_break_confirmed", False)),
        "fresh_status": "fresh",
        "mitigated_count": 0,
        "failed_status": False,
        "width": round(width, 5),
        "width_to_atr": round(width_to_atr, 3),
        "quality_score": round(_clamp(quality, 0, 10), 2),
        "valid_status": not reasons,
        "rejection_reasons": _dedupe(reasons),
    }


def detect_ob_retest(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    order_block: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Detect first post-confirmation OB retest, expiry, or invalidation."""

    cfg = _config(config)
    candles = _closed_candles(df)
    direction = _direction(order_block.get("direction"))
    if direction is OBRetestDirection.NONE:
        return None
    start_index = int(order_block["valid_from_index"])
    expiry = int(cfg["ob_retest_expiry_candles"])
    zone_low = float(order_block["zone_low"])
    zone_high = float(order_block["zone_high"])
    mean_threshold = float(order_block["mean_threshold"])
    invalidation_low = float(order_block.get("full_zone_low", zone_low))
    invalidation_high = float(order_block.get("full_zone_high", zone_high))
    for candle in candles:
        if candle.index <= start_index:
            continue
        if candle.index > start_index + expiry:
            return {"retest_detected": False, "retest_status": "expired", "rejection_reason": "ob_retest_expired"}
        if direction is OBRetestDirection.BULLISH and candle.close < invalidation_low:
            return _retest_result(order_block, candle, False, "invalidated", "ob_invalidated", candle.low)
        if direction is OBRetestDirection.BEARISH and candle.close > invalidation_high:
            return _retest_result(order_block, candle, False, "invalidated", "ob_invalidated", candle.high)
        if not (candle.low <= zone_high and candle.high >= zone_low):
            continue
        if direction is OBRetestDirection.BULLISH:
            touched_price = min(candle.low, zone_high)
            depth = "mean_threshold_touched" if candle.low <= mean_threshold else "entered_zone"
            if candle.low <= zone_low:
                depth = "deep_mitigation"
        else:
            touched_price = max(candle.high, zone_low)
            depth = "mean_threshold_touched" if candle.high >= mean_threshold else "entered_zone"
            if candle.high >= zone_high:
                depth = "deep_mitigation"
        result = _retest_result(order_block, candle, True, depth, None, touched_price)
        result["mitigated_count"] = int(order_block.get("mitigated_count", 0)) + 1
        result["fresh_status"] = "first_retest" if result["mitigated_count"] == 1 else "mitigated"
        return result
    return None


def validate_ob_reaction(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    order_block: Mapping[str, Any],
    retest_event: Mapping[str, Any] | None,
    ltf_context: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate retest reaction with aggressive, candle, or LTF-MSS confirmation."""

    cfg = _config(config)
    if retest_event is None:
        return _reaction(False, "none", ["no_ob_retest"], 0.0)
    if str(retest_event.get("retest_status")) == "invalidated":
        return _reaction(False, "invalidated", ["ob_invalidated"], 0.0)
    direction = _direction(order_block.get("direction"))
    mode = OBConfirmationMode(str(cfg["confirmation_mode"]).lower())
    if mode is OBConfirmationMode.AGGRESSIVE:
        return _reaction(True, "aggressive_ob_touch", [], 6.2, retest_event=retest_event)
    if mode is OBConfirmationMode.LTF_MSS or str(cfg["entry_mode"]).lower() == OBEntryMode.CONSERVATIVE.value:
        return _ltf_reaction(order_block, retest_event, ltf_context or {})
    candles = _closed_candles(df)
    retest_index = int(retest_event.get("retest_index", -1))
    wait = int(cfg["reaction_wait_candles"])
    mean_threshold = float(order_block["mean_threshold"])
    zone_low = float(order_block["zone_low"])
    zone_high = float(order_block["zone_high"])
    for candle in candles:
        if candle.index < retest_index or candle.index > retest_index + wait:
            continue
        if direction is OBRetestDirection.BULLISH:
            if candle.close < float(order_block.get("full_zone_low", zone_low)):
                return _reaction(False, "invalidated", ["ob_invalidated"], 0.0, retest_event=retest_event)
            if candle.bullish and candle.close > mean_threshold:
                strength = 7.4 + (1.0 if candle.close > zone_high else 0.0) + min(candle.body_to_range, 1.0)
                return _reaction(True, "bullish_reaction_candle_from_ob", [], strength, retest_event, candle)
        if direction is OBRetestDirection.BEARISH:
            if candle.close > float(order_block.get("full_zone_high", zone_high)):
                return _reaction(False, "invalidated", ["ob_invalidated"], 0.0, retest_event=retest_event)
            if candle.bearish and candle.close < mean_threshold:
                strength = 7.4 + (1.0 if candle.close < zone_low else 0.0) + min(candle.body_to_range, 1.0)
                return _reaction(True, "bearish_reaction_candle_from_ob", [], strength, retest_event, candle)
    return _reaction(False, "reaction_missing", ["no_ob_reaction_confirmation"], 0.0, retest_event=retest_event)


def generate_ob_retest_signal(context: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Generate a complete OB retest signal or deterministic rejection."""

    cfg = _config(config)
    setup_df = context.get("m15_df", context.get("df", context.get("candles", [])))
    candles = _closed_candles(setup_df)
    if len(candles) < 3:
        return _no_trade(context, ["insufficient_closed_candles"])
    safety_reasons = _safety_filter_reasons(context, cfg)
    if safety_reasons:
        return _no_trade(context, safety_reasons)
    sweeps = detect_liquidity_sweep(setup_df, context.get("liquidity_pools", []), cfg)
    if not sweeps:
        return _no_trade(
            context,
            ["missing_required_liquidity_sweep", "weak_ob_no_displacement", "ob_not_validated_by_structure_break"],
        )
    all_reasons: list[str] = []
    waiting_payload: dict[str, Any] | None = None
    for sweep in sweeps:
        displacement = detect_displacement(setup_df, sweep, context.get("swings", []), cfg)
        if displacement is None:
            all_reasons.extend(["weak_ob_no_displacement", "ob_not_validated_by_structure_break"])
            continue
        order_block = detect_order_block_after_sweep(setup_df, sweep, displacement, cfg)
        if order_block is None:
            all_reasons.append("no_valid_order_block")
            continue
        retest = detect_ob_retest(setup_df, order_block, cfg)
        if retest is None or not bool(retest.get("retest_detected", False)):
            if retest and retest.get("rejection_reason"):
                all_reasons.append(str(retest["rejection_reason"]))
            else:
                waiting_payload = {
                    "liquidity_sweep": sweep,
                    "structure_and_displacement": displacement,
                    "order_block": order_block,
                }
            continue
        if str(retest.get("retest_status")) == "invalidated":
            all_reasons.append("ob_invalidated")
            continue
        reaction = validate_ob_reaction(setup_df, order_block, retest, context.get("ltf_context", {}), cfg)
        if not bool(reaction.get("confirmed", False)):
            all_reasons.extend(reaction.get("rejection_reasons", ["no_ob_reaction_confirmation"]))
            continue
        risk = _risk_plan(sweep, order_block, retest, reaction, context, cfg)
        candidate_reasons = _risk_rejection_reasons(risk, order_block, cfg)
        if candidate_reasons:
            all_reasons.extend(candidate_reasons)
            continue
        entry = {
            "entry_type": risk["entry_type"],
            "entry_price": risk["entry_price"],
            "entry_triggered": risk["entry_triggered"],
        }
        setup = {
            "direction": displacement["direction"],
            "liquidity_sweep": sweep,
            "structure_and_displacement": displacement,
            "order_block": {
                **order_block,
                "fresh_status": retest.get("fresh_status", "first_retest"),
                "mitigated_count": retest.get("mitigated_count", 1),
            },
            "retest": retest,
            "ltf_confirmation": reaction,
            "entry": entry,
            "risk": risk,
        }
        score = score_ob_retest_setup(setup, context, cfg)
        if not score["trade_allowed"]:
            all_reasons.extend(score.get("hard_filter_failures") or ["score_or_filter_failed"])
            continue
        symbol = str(context.get("symbol", "XAUUSD"))
        return {
            "strategy": "Order Block Retest After Liquidity Sweep",
            "symbol": symbol,
            "signal_id": f"{symbol}_OB_RETEST_{displacement['direction'].upper()}_{retest['retest_index']}",
            "signal_status": OBRetestStatus.VALID.value,
            "trade_allowed": True,
            "direction": displacement["direction"],
            "timeframe_stack": context.get("timeframe_stack", {"setup_timeframe": "15M", "entry_timeframe": "5M"}),
            **setup,
            "filters": _passed_filters(context),
            "score": score,
            "rejection_reasons": [],
        }
    if waiting_payload is not None and not all_reasons:
        return _no_trade(context, ["waiting_for_ob_retest"], OBRetestStatus.WAITING_FOR_RETEST, **waiting_payload)
    return _no_trade(context, all_reasons or ["no_valid_ob_retest_candidate"])


def score_ob_retest_setup(
    setup: Mapping[str, Any], context: Mapping[str, Any] | None = None, config: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Score the full setup from 0 to 10 and apply hard filters."""

    cfg = _config(config)
    context = context or {}
    sweep = setup.get("liquidity_sweep", {}) or {}
    displacement = setup.get("structure_and_displacement", {}) or {}
    ob = setup.get("order_block", {}) or {}
    retest = setup.get("retest", {}) or {}
    reaction = setup.get("ltf_confirmation", {}) or {}
    risk = setup.get("risk", {}) or {}
    hard_filters: list[str] = []
    hard_filters.extend(_safety_filter_reasons(context, cfg))
    if not sweep:
        hard_filters.append("missing_required_liquidity_sweep")
    if not bool(displacement.get("displacement_confirmed", False)):
        hard_filters.append("weak_ob_no_displacement")
    if not bool(displacement.get("structure_break_confirmed", False)):
        hard_filters.append("ob_not_validated_by_structure_break")
    if bool(ob.get("failed_status", False)):
        hard_filters.append("ob_failed")
    if int(ob.get("mitigated_count", 0)) > int(cfg["max_allowed_mitigations"]):
        hard_filters.append("ob_over_mitigated")
    if not bool(retest.get("retest_detected", False)):
        hard_filters.append("no_ob_retest")
    if not bool(reaction.get("confirmed", False)):
        hard_filters.append("no_ob_reaction_confirmation")
    hard_filters.extend(_risk_rejection_reasons(risk, ob, cfg))
    component_scores = {
        "liquidity_sweep": float(sweep.get("quality_score", 0.0)),
        "reclaim_rejection": 8.0 if (sweep.get("reclaim_status") or sweep.get("rejection_status")) else 0.0,
        "displacement_strength": float(displacement.get("strength_score", 0.0)),
        "structure_break": 8.5 if bool(displacement.get("structure_break_confirmed", False)) else 0.0,
        "ob_validity": float(ob.get("quality_score", 0.0)),
        "ob_freshness": 8.5 if str(ob.get("fresh_status")) in {"fresh", "first_retest"} else 5.5,
        "retest_quality": _retest_score(retest),
        "ltf_confirmation": float(reaction.get("reaction_strength_score", 0.0)),
        "target_rr": _rr_score(float(risk.get("rr", 0.0)), float(cfg["min_rr"])),
        "xauusd_safety": 9.0 if not _safety_filter_reasons(context, cfg) else 2.0,
    }
    weights = {
        "liquidity_sweep": 1.1,
        "reclaim_rejection": 0.8,
        "displacement_strength": 1.2,
        "structure_break": 1.0,
        "ob_validity": 1.2,
        "ob_freshness": 0.6,
        "retest_quality": 0.8,
        "ltf_confirmation": 1.1,
        "target_rr": 1.0,
        "xauusd_safety": 0.7,
    }
    total = sum(component_scores[key] * weights[key] for key in weights) / sum(weights.values())
    total = round(_clamp(total, 0, 10), 2)
    hard_filters = _dedupe(hard_filters)
    return {
        "total_score": total,
        "grade": _grade(total),
        "trade_allowed": total >= float(cfg["minimum_setup_score"]) and not hard_filters,
        "component_scores": {key: round(value, 2) for key, value in component_scores.items()},
        "hard_filter_failures": hard_filters,
    }


def _risk_plan(
    sweep: Mapping[str, Any],
    order_block: Mapping[str, Any],
    retest: Mapping[str, Any],
    reaction: Mapping[str, Any],
    context: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    candles = _closed_candles(context.get("m15_df", context.get("df", context.get("candles", []))))
    atr = _atr(candles, int(cfg["atr_period"]))
    direction = _direction(order_block.get("direction"))
    spread = float(_get(context.get("spread_status", {}) or {}, "spread_points", "spread", default=0.0) or 0.0)
    atr_buffer = atr * float(cfg["stop_atr_buffer_multiplier"])
    if str(cfg["entry_mode"]).lower() == OBEntryMode.AGGRESSIVE.value:
        entry_price = float(order_block["mean_threshold"])
        entry_type = "ob_mean_threshold_entry"
    else:
        entry_price = float(reaction.get("confirmation_price") or retest["touched_price"])
        entry_type = "confirmed_reaction_entry"
    if direction is OBRetestDirection.BULLISH:
        invalidation = min(float(order_block.get("full_zone_low", order_block["zone_low"])), float(sweep["sweep_low"]))
        stop = invalidation - atr_buffer - spread
    else:
        invalidation = max(
            float(order_block.get("full_zone_high", order_block["zone_high"])), float(sweep["sweep_high"])
        )
        stop = invalidation + atr_buffer + spread
    target, reference = _select_target(entry_price, context.get("liquidity_pools", []), context, direction)
    rr_value = _rr(entry_price, stop, target, direction)
    return {
        "entry_type": entry_type,
        "entry_price": round(entry_price, 5),
        "entry_triggered": True,
        "stop_loss": round(stop, 5),
        "stop_reference": "beyond_sweep_extreme_with_ob_invalidation_atr_and_spread_buffer",
        "risk_distance": round(abs(entry_price - stop), 5),
        "target": round(target, 5) if target is not None else None,
        "target_reference": reference,
        "reward_distance": round(abs(float(target) - entry_price), 5) if target is not None else 0.0,
        "rr": round(rr_value, 2),
        "min_rr_required": float(cfg["min_rr"]),
        "stop_to_atr": round(abs(entry_price - stop) / max(atr, 1e-9), 3),
    }


def _risk_rejection_reasons(
    risk: Mapping[str, Any], order_block: Mapping[str, Any], cfg: Mapping[str, Any]
) -> list[str]:
    reasons: list[str] = []
    reasons.extend(str(reason) for reason in order_block.get("rejection_reasons", []))
    if risk.get("target") is None:
        reasons.append("no_valid_target")
    if float(risk.get("rr", 0.0)) < float(cfg["min_rr"]):
        reasons.append("rr_below_minimum")
    if float(risk.get("stop_to_atr", 0.0)) > float(cfg["max_stop_atr_multiplier"]):
        reasons.append("stop_too_large")
    return _dedupe(reasons)


def _select_target(
    entry: float, pools: Sequence[Mapping[str, Any]], context: Mapping[str, Any], direction: OBRetestDirection
) -> tuple[float | None, str]:
    target_context = context.get("target_liquidity")
    if isinstance(target_context, Mapping):
        price = _float(_get(target_context, "price", "target_price", "zone_mid", default=None))
        if price is not None and not _pool_swept(target_context):
            if direction is OBRetestDirection.BULLISH and price > entry:
                return price, str(_get(target_context, "label", "id", default="context_buy_side_liquidity"))
            if direction is OBRetestDirection.BEARISH and price < entry:
                return price, str(_get(target_context, "label", "id", default="context_sell_side_liquidity"))
    wanted_side = "buy_side" if direction is OBRetestDirection.BULLISH else "sell_side"
    targets: list[tuple[float, str]] = []
    for pool in pools:
        if _pool_swept(pool) or _pool_side(pool) != wanted_side:
            continue
        low, high = _pool_bounds(pool)
        price = high if direction is OBRetestDirection.BULLISH else low
        if direction is OBRetestDirection.BULLISH and price > entry:
            targets.append((price, str(_get(pool, "liquidity_id", "id", default="buy_side_liquidity"))))
        if direction is OBRetestDirection.BEARISH and price < entry:
            targets.append((price, str(_get(pool, "liquidity_id", "id", default="sell_side_liquidity"))))
    if not targets:
        return None, "none"
    return (
        min(targets, key=lambda item: item[0])
        if direction is OBRetestDirection.BULLISH
        else max(targets, key=lambda item: item[0])
    )


def _ltf_reaction(ob: Mapping[str, Any], retest_event: Mapping[str, Any], ltf: Mapping[str, Any]) -> dict[str, Any]:
    direction = _direction(ob.get("direction"))
    if direction is OBRetestDirection.BULLISH:
        ok = bool(_get(ltf, "sell_side_sweep_inside_ob", default=False)) and bool(
            _get(ltf, "bullish_mss_confirmed", default=False)
        )
        if ok:
            strength = 8.2 + (0.6 if bool(_get(ltf, "bullish_displacement_confirmed", default=False)) else 0.0)
            return _reaction(True, "ltf_bullish_mss_inside_ob", [], strength, retest_event=retest_event)
    if direction is OBRetestDirection.BEARISH:
        ok = bool(_get(ltf, "buy_side_sweep_inside_ob", default=False)) and bool(
            _get(ltf, "bearish_mss_confirmed", default=False)
        )
        if ok:
            strength = 8.2 + (0.6 if bool(_get(ltf, "bearish_displacement_confirmed", default=False)) else 0.0)
            return _reaction(True, "ltf_bearish_mss_inside_ob", [], strength, retest_event=retest_event)
    return _reaction(False, "ltf_mss_missing", ["no_ob_reaction_confirmation"], 0.0, retest_event=retest_event)


def _sweep_event(
    direction: OBRetestDirection,
    side: str,
    pool_id: str,
    swept_level: float,
    zone_low: float,
    zone_high: float,
    zone_mid: float,
    candle: _Candle,
    depth: float,
    status_key: str,
    status_value: str,
    strength: float,
    atr: float,
) -> dict[str, Any]:
    return {
        "direction_bias": direction.value,
        "swept_side": side,
        "swept_liquidity_id": pool_id,
        "swept_level": round(swept_level, 5),
        "liquidity_zone_low": round(zone_low, 5),
        "liquidity_zone_high": round(zone_high, 5),
        "liquidity_zone_mid": round(zone_mid, 5),
        "sweep_index": candle.index,
        "sweep_time": candle.timestamp,
        "sweep_low": round(candle.low, 5),
        "sweep_high": round(candle.high, 5),
        "sweep_extreme": round(candle.low if direction is OBRetestDirection.BULLISH else candle.high, 5),
        "sweep_depth": round(depth, 5),
        status_key: status_value,
        "quality_score": round(_clamp(strength + min(depth / max(atr, 1e-9), 1.2), 0, 10), 2),
    }


def _retest_result(
    ob: Mapping[str, Any], candle: _Candle, detected: bool, status: str, reason: str | None, touched_price: float
) -> dict[str, Any]:
    return {
        "ob_id": ob.get("ob_id"),
        "retest_detected": detected,
        "retest_status": status,
        "retest_index": candle.index,
        "retest_time": candle.timestamp,
        "retest_depth": status,
        "touched_price": round(touched_price, 5),
        "rejection_reason": reason,
    }


def _reaction(
    confirmed: bool,
    confirmation_type: str,
    reasons: Sequence[str],
    strength: float,
    retest_event: Mapping[str, Any] | None = None,
    candle: _Candle | None = None,
) -> dict[str, Any]:
    return {
        "confirmed": confirmed,
        "confirmation_type": confirmation_type,
        "confirmation_index": candle.index if candle else (retest_event or {}).get("retest_index"),
        "confirmation_time": candle.timestamp if candle else (retest_event or {}).get("retest_time"),
        "confirmation_price": candle.close if candle else None,
        "reaction_strength_score": round(_clamp(strength, 0, 10), 2),
        "rejection_reasons": _dedupe(list(reasons)),
    }


def _retest_score(retest: Mapping[str, Any]) -> float:
    if not bool(retest.get("retest_detected", False)):
        return 0.0
    depth = str(retest.get("retest_depth", "entered_zone"))
    if depth == "mean_threshold_touched":
        return 8.5
    if depth == "deep_mitigation":
        return 7.5
    return 7.0


def _rr_score(rr: float, min_rr: float) -> float:
    if rr < min_rr:
        return max(0.0, rr / max(min_rr, 1e-9) * 6.0)
    return _clamp(7.0 + min(rr - min_rr, 3.0), 0, 10)


def _safety_filter_reasons(context: Mapping[str, Any], cfg: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    news = context.get("news_status", {}) or {}
    spread = context.get("spread_status", {}) or {}
    if bool(_get(news, "restricted", "news_restricted", default=False)):
        reasons.append("news_restricted")
    if bool(_get(news, "post_news_stabilized", default=True)) is False:
        reasons.append("post_news_structure_unstable")
    spread_value = _float(_get(spread, "spread_points", "spread", default=None))
    spread_status = str(_get(spread, "status", default="normal")).lower()
    if bool(_get(spread, "spread_safe", default=True)) is False or spread_status in {"high", "wide", "unsafe"}:
        reasons.append("spread_too_high")
    elif spread_value is not None and spread_value > float(cfg["max_spread_points"]):
        reasons.append("spread_too_high")
    return _dedupe(reasons)


def _no_trade(
    context: Mapping[str, Any],
    reasons: Sequence[str],
    status: OBRetestStatus = OBRetestStatus.REJECTED,
    **payload: Any,
) -> dict[str, Any]:
    return {
        "strategy": "Order Block Retest After Liquidity Sweep",
        "symbol": str(context.get("symbol", "XAUUSD")),
        "signal_status": status.value,
        "trade_allowed": False,
        "direction": None,
        "rejection_reason": reasons[0] if reasons else None,
        "rejection_reasons": _dedupe(list(reasons)),
        **payload,
    }


def _passed_filters(context: Mapping[str, Any]) -> dict[str, str]:
    return {
        "news_filter": "passed",
        "spread_filter": "passed",
        "ob_width_filter": "passed",
        "freshness_filter": "passed",
        "htf_blocker_filter": "passed",
        "htf_bias": str(context.get("htf_bias", "neutral")),
    }


def _config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    data = dict(config or {})
    return {
        "atr_period": int(data.get("atr_period", 14)),
        "sweep_buffer": data.get("sweep_buffer"),
        "sweep_buffer_atr_multiplier": float(data.get("sweep_buffer_atr_multiplier", 0.02)),
        "break_buffer": data.get("break_buffer"),
        "break_buffer_atr_multiplier": float(data.get("break_buffer_atr_multiplier", 0.01)),
        "max_displacement_wait_candles": int(data.get("max_displacement_wait_candles", 8)),
        "displacement_min_body_to_range": float(data.get("displacement_min_body_to_range", 0.55)),
        "displacement_min_range_to_atr": float(data.get("displacement_min_range_to_atr", 0.8)),
        "displacement_min_close_position": float(data.get("displacement_min_close_position", 0.65)),
        "zone_mode": str(data.get("zone_mode", OBZoneMode.FULL_RANGE.value)).lower(),
        "max_ob_atr_multiplier": float(data.get("max_ob_atr_multiplier", 2.8)),
        "max_news_spike_atr_multiplier": float(data.get("max_news_spike_atr_multiplier", 4.0)),
        "ob_retest_expiry_candles": int(data.get("ob_retest_expiry_candles", 14)),
        "reaction_wait_candles": int(data.get("reaction_wait_candles", 3)),
        "confirmation_mode": str(data.get("confirmation_mode", OBConfirmationMode.CANDLE_REACTION.value)).lower(),
        "entry_mode": str(data.get("entry_mode", OBEntryMode.BALANCED.value)).lower(),
        "stop_atr_buffer_multiplier": float(data.get("stop_atr_buffer_multiplier", 0.02)),
        "max_stop_atr_multiplier": float(data.get("max_stop_atr_multiplier", 4.5)),
        "max_allowed_mitigations": int(data.get("max_allowed_mitigations", 1)),
        "max_spread_points": float(data.get("max_spread_points", 1.0)),
        "min_rr": float(data.get("min_rr", 2.0)),
        "minimum_setup_score": float(data.get("minimum_setup_score", 7.5)),
    }


def _confirmed_swings(candles: Sequence[_Candle], swings: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if swings:
        normalized = []
        for swing in swings:
            kind = str(_get(swing, "kind", "type", "swing_type", default="")).lower()
            if "high" in kind:
                kind = "high"
            elif "low" in kind:
                kind = "low"
            else:
                continue
            if bool(_get(swing, "confirmed", default=True)) is False:
                continue
            normalized.append(
                {
                    "swing_id": str(_get(swing, "swing_id", "id", default=f"SWING_{kind}_{_get(swing, 'index')}")),
                    "kind": kind,
                    "index": int(_get(swing, "index", "candle_index", default=0)),
                    "price": float(_get(swing, "price", "level", default=0.0)),
                }
            )
        return normalized
    derived: list[dict[str, Any]] = []
    for pos in range(1, len(candles) - 1):
        previous, current, following = candles[pos - 1], candles[pos], candles[pos + 1]
        if current.high > previous.high and current.high > following.high:
            derived.append(
                {
                    "swing_id": f"SWING_HIGH_{current.index}",
                    "kind": "high",
                    "index": current.index,
                    "price": current.high,
                }
            )
        if current.low < previous.low and current.low < following.low:
            derived.append(
                {"swing_id": f"SWING_LOW_{current.index}", "kind": "low", "index": current.index, "price": current.low}
            )
    return derived


def _pool_bounds(pool: Mapping[str, Any]) -> tuple[float, float]:
    price = _float(_get(pool, "price", default=None))
    low = _float(_get(pool, "zone_low", "low", default=price))
    high = _float(_get(pool, "zone_high", "high", default=price))
    if low is None or high is None:
        raise ValueError("liquidity pool requires price or zone_low/zone_high")
    return min(low, high), max(low, high)


def _pool_side(pool: Mapping[str, Any]) -> str:
    text = str(_get(pool, "direction", "side", "liquidity_side", default="")).lower().replace("-", "_")
    if text in {"sell", "sell_side", "ssl", "sellside"}:
        return "sell_side"
    if text in {"buy", "buy_side", "bsl", "buyside"}:
        return "buy_side"
    return text


def _pool_swept(pool: Mapping[str, Any]) -> bool:
    return str(_get(pool, "swept_status", "status", default="active")).lower() in {
        "swept",
        "already_swept",
        "fully_swept",
        "cleared",
        "inactive",
    }


def _closed_candles(rows: Sequence[Mapping[str, Any] | Any] | Any) -> list[_Candle]:
    candles: list[_Candle] = []
    for position, row in enumerate(_records(rows)):
        is_closed = bool(_get(row, "is_closed", "closed", default=True))
        if not is_closed:
            continue
        candles.append(
            _Candle(
                position=len(candles),
                index=int(_get(row, "index", default=position)),
                timestamp=_get(row, "timestamp", "time", default=position),
                open=float(_get(row, "open", default=0.0)),
                high=float(_get(row, "high", default=0.0)),
                low=float(_get(row, "low", default=0.0)),
                close=float(_get(row, "close", default=0.0)),
                volume=float(_get(row, "volume", default=0.0)),
                is_closed=is_closed,
            )
        )
    return sorted(candles, key=lambda candle: candle.index)


def _records(rows: Sequence[Mapping[str, Any] | Any] | Any) -> list[Any]:
    if hasattr(rows, "to_dict"):
        return list(rows.to_dict("records"))  # type: ignore[call-arg, union-attr]
    return list(rows or [])


def _atr(candles: Sequence[_Candle], period: int) -> float:
    if not candles:
        return 0.0
    ranges: list[float] = []
    previous_close = candles[0].close
    for candle in candles:
        ranges.append(
            max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close))
        )
        previous_close = candle.close
    selected = ranges[-period:] if len(ranges) >= period else ranges
    return mean(selected) if selected else 0.0


def _configured_buffer(cfg: Mapping[str, Any], key: str, default: float) -> float:
    value = cfg.get(key)
    return default if value in {None, ""} else float(value)


def _direction(value: Any) -> OBRetestDirection:
    text = str(value or "").lower()
    if text in {"bullish", "buy", "long"}:
        return OBRetestDirection.BULLISH
    if text in {"bearish", "sell", "short"}:
        return OBRetestDirection.BEARISH
    return OBRetestDirection.NONE


def _rr(entry: float, stop: float, target: float | None, direction: OBRetestDirection) -> float:
    if target is None:
        return 0.0
    if direction is OBRetestDirection.BULLISH:
        return (target - entry) / max(entry - stop, 1e-9)
    return (entry - target) / max(stop - entry, 1e-9)


def _grade(score: float) -> str:
    if score >= 9.0:
        return "A+"
    if score >= 8.0:
        return "A"
    if score >= 7.0:
        return "B"
    if score >= 6.0:
        return "C"
    if score >= 5.0:
        return "D"
    return "F"


def _get(row: Mapping[str, Any] | Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if isinstance(row, Mapping) and key in row and row[key] is not None:
            return row[key]
        if not isinstance(row, Mapping) and hasattr(row, key):
            value = getattr(row, key)
            if value is not None:
                return value
    return default


def _float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
