"""Weighted ICT/SMC setup scoring engine.

This module scores already-detected ICT/SMC setups. It does not discover
liquidity sweeps, MSS/BOS, FVGs, order blocks, stops, or targets from raw
market data. The output is a deterministic confluence audit that can rank
setups and explain why execution should be allowed or blocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class SetupScoreDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"


class SetupScoringMode(str, Enum):
    NORMAL = "normal"
    CONSERVATIVE = "conservative"
    AGGRESSIVE = "aggressive"


class SetupGrade(str, Enum):
    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class SetupScoreStatus(str, Enum):
    TRADE_ALLOWED = "trade_allowed"
    TRADE_BLOCKED = "trade_blocked"
    INVALID_CONTEXT = "invalid_context"


@dataclass(frozen=True, slots=True)
class _ComponentScore:
    name: str
    raw_score: float
    reason: str


def score_smc_setup(setup_context: Mapping[str, Any]) -> dict[str, Any]:
    """Score an already-detected ICT/SMC setup from 0 to 10."""

    config = _scoring_config(setup_context.get("scoring_config", {}) or {})
    direction = _direction(setup_context.get("direction"))
    warnings = [
        "Scoring evaluates an already-detected setup; it does not create trades.",
        "Only confirmed closed-candle context should be supplied to this layer.",
        "A high score improves confluence but does not guarantee profit.",
    ]
    hard_filters: list[str] = []
    caps: list[dict[str, Any]] = []

    if direction is SetupScoreDirection.NONE:
        return _invalid_result(setup_context, config, "invalid_direction", warnings)
    if not bool(setup_context.get("confirmed", False)):
        hard_filters.append("setup_not_confirmed")
        _cap(caps, 5.0, "Setup is not confirmed by closed-candle context.")

    components = [
        _score_htf_bias(setup_context, direction, caps),
        _score_premium_discount(setup_context, direction, caps),
        _score_liquidity_sweep(setup_context, direction, caps, hard_filters, config),
        _score_structure_confirmation(setup_context, direction, caps, hard_filters, config),
        _score_displacement(setup_context, direction, caps, hard_filters, config),
        _score_fvg_ob_quality(setup_context, direction, caps, hard_filters, config),
        _score_poi_freshness(setup_context, caps, hard_filters),
        _score_session_timing(setup_context, caps, hard_filters, config),
        _score_news_filter(setup_context, caps, hard_filters, config),
        _score_risk_reward(setup_context, caps, hard_filters, config),
        _score_target_clarity(setup_context, direction, caps, hard_filters),
    ]
    if config["use_volume"]:
        components.append(_score_volume_confirmation(setup_context, warnings))

    weights = _weights(config["use_volume"])
    total_weight = sum(weights[component.name] for component in components)
    if total_weight <= 0:
        return _invalid_result(setup_context, config, "no_enabled_components", warnings)

    uncapped_score = sum(
        component.raw_score * weights[component.name] for component in components
    ) / total_weight
    total_score = _apply_caps(uncapped_score, caps)
    total_score = round(_clamp(total_score), 2)
    uncapped_score = round(_clamp(uncapped_score), 2)
    threshold = float(config["trade_threshold"])
    trade_allowed = total_score >= threshold and not hard_filters
    status = SetupScoreStatus.TRADE_ALLOWED if trade_allowed else SetupScoreStatus.TRADE_BLOCKED

    return {
        "function": "score_smc_setup",
        "concept_name": "ICT/SMC Setup Scoring Engine",
        "setup_id": setup_context.get("setup_id"),
        "symbol": setup_context.get("symbol"),
        "timeframe": setup_context.get("timeframe"),
        "direction": direction.value,
        "setup_type": setup_context.get("setup_type"),
        "total_score": total_score,
        "uncapped_score": uncapped_score,
        "grade": _grade(total_score).value,
        "trade_allowed": trade_allowed,
        "trade_threshold": threshold,
        "scoring_mode": config["mode"].value,
        "status": status.value,
        "component_scores": _component_output(components, weights),
        "hard_filter_failures": _dedupe(hard_filters),
        "caps_applied": caps,
        "warnings": _dedupe(warnings),
        "reasons": _decision_reasons(components, hard_filters, caps, total_score),
        "decision_reason": _decision_reason(
            trade_allowed,
            hard_filters,
            total_score,
            threshold,
        ),
    }


def _scoring_config(config: Mapping[str, Any]) -> dict[str, Any]:
    mode = _mode(config.get("mode", SetupScoringMode.NORMAL.value))
    threshold = float(config.get("trade_threshold", 7.0))
    if mode is SetupScoringMode.CONSERVATIVE:
        threshold = max(threshold, 7.5)
    return {
        "mode": mode,
        "trade_threshold": threshold,
        "use_volume": bool(config.get("use_volume", False)),
        "min_rr": float(config.get("min_rr", 1.5)),
        "require_sweep": bool(config.get("require_sweep", True)),
        "require_structure": bool(config.get("require_structure", True)),
        "require_displacement": bool(config.get("require_displacement", False)),
        "require_entry_zone": bool(config.get("require_entry_zone", True)),
        "strict_session": bool(config.get("strict_session", False)),
    }


def _weights(use_volume: bool) -> dict[str, float]:
    if use_volume:
        return {
            "htf_bias_alignment": 9,
            "premium_discount": 7,
            "liquidity_sweep_quality": 11,
            "mss_bos_confirmation": 14,
            "displacement_strength": 10,
            "fvg_ob_quality": 9,
            "poi_freshness": 6,
            "session_timing": 5,
            "news_filter": 8,
            "risk_reward": 8,
            "target_clarity": 6,
            "volume_confirmation": 7,
        }
    return {
        "htf_bias_alignment": 10,
        "premium_discount": 8,
        "liquidity_sweep_quality": 12,
        "mss_bos_confirmation": 15,
        "displacement_strength": 10,
        "fvg_ob_quality": 10,
        "poi_freshness": 7,
        "session_timing": 6,
        "news_filter": 8,
        "risk_reward": 8,
        "target_clarity": 6,
    }


def _score_htf_bias(
    setup: Mapping[str, Any],
    direction: SetupScoreDirection,
    caps: list[dict[str, Any]],
) -> _ComponentScore:
    htf = _text(_get(setup, "htf_bias", "htf_structure", default="unknown"))
    draw = _text(_get(setup, "htf_draw_on_liquidity", "draw_on_liquidity", default="neutral"))
    blocker = setup.get("htf_poi_context", {}) or {}
    strong_blocker = bool(_get(blocker, "blocks_path", "strong_blocker", default=False))
    aligned = (direction is SetupScoreDirection.BULLISH and htf == "bullish") or (
        direction is SetupScoreDirection.BEARISH and htf == "bearish"
    )
    opposed = (direction is SetupScoreDirection.BULLISH and htf == "bearish") or (
        direction is SetupScoreDirection.BEARISH and htf == "bullish"
    )

    if aligned and _draw_aligns(draw, direction):
        return _component("htf_bias_alignment", 10.0, "HTF bias and draw support setup.")
    if aligned:
        return _component("htf_bias_alignment", 8.0, "HTF bias aligns but draw is neutral.")
    if htf in {"neutral", "ranging", "range", "unknown"}:
        return _component("htf_bias_alignment", 6.0, "HTF context is neutral or ranging.")
    if opposed and strong_blocker:
        _cap(caps, 5.0, "Opposing HTF POI blocks the setup path.")
        return _component("htf_bias_alignment", 0.0, "HTF bias and POI oppose setup.")
    if opposed and _exists(setup.get("liquidity_sweep")) and _has_structure_confirmation(setup):
        return _component(
            "htf_bias_alignment",
            4.0,
            "Setup is counter-HTF but local context exists.",
        )
    if opposed:
        _cap(caps, 5.0, "HTF bias opposes setup without sweep and MSS/BOS support.")
        return _component("htf_bias_alignment", 2.0, "HTF bias conflicts with setup direction.")
    return _component("htf_bias_alignment", 5.0, "HTF bias data is incomplete.")


def _score_premium_discount(
    setup: Mapping[str, Any],
    direction: SetupScoreDirection,
    caps: list[dict[str, Any]],
) -> _ComponentScore:
    raw = setup.get("premium_discount", setup.get("entry_location", {}))
    location = _text(_get(raw, "location", "zone", "entry_location", default=raw))
    target_too_close = bool(_get(raw, "target_too_close", default=False))
    bullish_scores = {
        "deep_discount": 10.0,
        "discount_extreme": 10.0,
        "discount": 8.0,
        "equilibrium": 6.0,
        "near_equilibrium": 6.0,
        "midpoint": 6.0,
        "premium": 4.0,
        "deep_premium": 2.0,
    }
    bearish_scores = {
        "deep_premium": 10.0,
        "premium_extreme": 10.0,
        "premium": 8.0,
        "equilibrium": 6.0,
        "near_equilibrium": 6.0,
        "midpoint": 6.0,
        "discount": 4.0,
        "deep_discount": 2.0,
    }
    scores = bullish_scores if direction is SetupScoreDirection.BULLISH else bearish_scores
    score = scores.get(location, 5.0)
    bad_bull = direction is SetupScoreDirection.BULLISH and location == "deep_premium"
    bad_bear = direction is SetupScoreDirection.BEARISH and location == "deep_discount"
    if target_too_close and (bad_bull or bad_bear):
        _cap(caps, 6.0, "Setup is in poor dealing-range location with limited target.")
    return _component("premium_discount", score, "Premium/discount location was scored.")


def _score_liquidity_sweep(
    setup: Mapping[str, Any],
    direction: SetupScoreDirection,
    caps: list[dict[str, Any]],
    hard_filters: list[str],
    config: Mapping[str, Any],
) -> _ComponentScore:
    sweep = setup.get("liquidity_sweep", {}) or {}
    if not _exists(sweep):
        if config["require_sweep"]:
            _cap(caps, 5.0, "Setup type requires a liquidity sweep but none exists.")
            hard_filters.append("liquidity_sweep_missing")
        return _component("liquidity_sweep_quality", 0.0, "No confirmed liquidity sweep.")

    score = _float(_get(sweep, "quality_score", "swept_liquidity_quality", default=None))
    if score is None:
        level = _text(_get(sweep, "swept_level_type", "level_type", default=""))
        strong_levels = {
            "pdh",
            "pdl",
            "asian_high",
            "asian_low",
            "london_high",
            "london_low",
            "equal_highs",
            "equal_lows",
            "htf_swing_high",
            "htf_swing_low",
        }
        score = 6.0
        score += 1.5 if level in strong_levels else 0.0
        sweep_side = _text(_get(sweep, "swept_side", "side", default=""))
        score += 1.0 if _sweep_side_aligns(sweep_side, direction) else 0.0
        score += 1.5 if _reclaimed(sweep) else 0.0
    if not _reclaimed(sweep):
        _cap(caps, 5.5, "Liquidity sweep exists but has no reclaim or rejection.")
        score = min(score, 5.5)
    return _component(
        "liquidity_sweep_quality",
        score,
        "Liquidity sweep is scored from level quality, alignment, and reclaim.",
    )


def _score_structure_confirmation(
    setup: Mapping[str, Any],
    direction: SetupScoreDirection,
    caps: list[dict[str, Any]],
    hard_filters: list[str],
    config: Mapping[str, Any],
) -> _ComponentScore:
    structure = _structure_context(setup)
    setup_type = _text(setup.get("setup_type"))
    break_type = _text(_get(structure, "break_type", "type", "structure_type", default=""))
    confirmed = bool(
        _get(structure, "confirmed", "mss_confirmed", "bos_confirmed", default=False)
    )
    close_confirmed = bool(
        _get(structure, "close_confirmed", "candle_close_confirmed", default=confirmed)
    )
    struct_direction = _direction(_get(structure, "direction", default=direction.value))

    if confirmed and close_confirmed and struct_direction is direction:
        if break_type in {"mss", "bos", "market_structure_shift", "break_of_structure"}:
            return _component("mss_bos_confirmation", 10.0, "MSS/BOS is candle-close confirmed.")
        return _component("mss_bos_confirmation", 8.0, "Structure confirms setup direction.")
    if break_type in {"choch", "internal_choch", "internal_shift"}:
        return _component("mss_bos_confirmation", 6.0, "Only CHoCH/internal shift is present.")
    if bool(_get(structure, "wick_break", "wick_only", default=False)):
        _cap(caps, 6.0, "Structure break is wick-only, not candle-close confirmed.")
        return _component("mss_bos_confirmation", 4.0, "Structure break is wick-only.")

    needs_structure = config["require_structure"] or any(
        term in setup_type for term in ["reversal", "raid", "sweep", "hunt", "continuation"]
    )
    if config["mode"] is SetupScoringMode.CONSERVATIVE and needs_structure:
        hard_filters.append("no_mss_or_bos_confirmation")
    _cap(caps, 5.5, "No MSS/BOS candle-close confirmation.")
    return _component("mss_bos_confirmation", 0.0, "No MSS/BOS confirmation.")


def _score_displacement(
    setup: Mapping[str, Any],
    direction: SetupScoreDirection,
    caps: list[dict[str, Any]],
    hard_filters: list[str],
    config: Mapping[str, Any],
) -> _ComponentScore:
    displacement = setup.get("displacement", {}) or {}
    if not _exists(displacement) and not bool(setup.get("displacement_confirmed", False)):
        _cap(caps, 6.0, "No directional displacement after structure confirmation.")
        if config["require_displacement"]:
            hard_filters.append("displacement_missing")
        return _component("displacement_strength", 0.0, "No displacement evidence.")

    if bool(_get(displacement, "news_spike_only", default=False)):
        _cap(caps, 5.0, "Displacement appears to be first news spike only.")

    body = _float(_get(displacement, "body_to_range_ratio", default=0.0), 0.0) or 0.0
    atr = _float(_get(displacement, "range_to_atr_ratio", default=0.0), 0.0) or 0.0
    close_position = _float(_get(displacement, "close_position", default=None))
    fvg_created = bool(_get(displacement, "fvg_created", "created_fvg", default=False))
    disp_direction = _direction(_get(displacement, "direction", default=direction.value))

    score = 2.0
    score += 2.5 if disp_direction is direction else -1.5
    score += 2.0 if body >= 0.55 else 0.8 if body >= 0.40 else 0.0
    score += 2.0 if atr >= 1.5 else 1.2 if atr >= 1.0 else 0.0
    if close_position is not None:
        close_extreme = (
            close_position
            if direction is SetupScoreDirection.BULLISH
            else 1.0 - close_position
        )
        score += (
            1.5
            if close_extreme >= 0.70
            else 0.5
            if close_extreme >= 0.55
            else 0.0
        )
    score += 1.0 if fvg_created else 0.0
    return _component(
        "displacement_strength",
        score,
        "Displacement scored from body, ATR, close, and FVG creation.",
    )


def _score_fvg_ob_quality(
    setup: Mapping[str, Any],
    direction: SetupScoreDirection,
    caps: list[dict[str, Any]],
    hard_filters: list[str],
    config: Mapping[str, Any],
) -> _ComponentScore:
    zone = _first_mapping(setup, "entry_zone", "selected_zone", "fvg_zone", "order_block")
    if not zone:
        zone = _first_sequence_mapping(setup, "fvg_zones", "order_blocks")
    if not zone or _invalidated(zone):
        _cap(caps, 6.0, "No valid FVG/OB entry zone exists.")
        if config["require_entry_zone"]:
            hard_filters.append("no_valid_entry_zone")
        return _component("fvg_ob_quality", 0.0, "No valid FVG/OB entry zone.")

    quality = _float(_get(zone, "quality_score", "zone_quality_score", default=None))
    if quality is None:
        zone_type = _text(_get(zone, "zone_type", "type", default=""))
        quality = 5.0
        has_overlap = "fvg" in zone_type and ("ob" in zone_type or "order" in zone_type)
        has_entry_zone = "fvg" in zone_type or "ob" in zone_type or "order" in zone_type
        quality += 2.0 if has_overlap else 0.0
        quality += 1.2 if has_entry_zone else 0.0
        created_after_structure = bool(
            _get(zone, "created_after_mss", "created_after_bos", default=False)
        )
        quality += 1.0 if created_after_structure else 0.0
        quality += 1.0 if bool(_get(zone, "created_by_displacement", default=False)) else 0.0
        quality += (
            0.8
            if _direction(_get(zone, "direction", default=direction.value)) is direction
            else -1.0
        )
    return _component(
        "fvg_ob_quality",
        quality,
        "FVG/OB quality scored from zone validity and structural connection.",
    )


def _score_poi_freshness(
    setup: Mapping[str, Any],
    caps: list[dict[str, Any]],
    hard_filters: list[str],
) -> _ComponentScore:
    poi = _first_mapping(setup, "poi", "entry_zone", "selected_zone", "fvg_zone", "order_block")
    if poi and _invalidated(poi):
        hard_filters.append("poi_invalidated")
        _cap(caps, 4.0, "POI or entry zone has been invalidated.")
        return _component("poi_freshness", 0.0, "POI is invalidated.")

    freshness = _text(
        _get(poi or {}, "fresh_status", "freshness", "mitigation_status", default="unknown")
    )
    touches = int(_float(_get(poi or {}, "touch_count", "retests", default=0), 0) or 0)
    if freshness in {"fresh", "unmitigated", "first_touch"}:
        score = 10.0 if touches == 0 else 8.0
    elif freshness in {"lightly_mitigated", "touched", "reacted"}:
        score = 8.0
    elif freshness in {"partially_mitigated", "partial"}:
        score = 6.0
    elif freshness in {"deeply_mitigated", "deep"}:
        score = 4.0
    elif freshness in {"multiple_retests", "stale", "overused"} or touches >= 3:
        score = 2.0
    else:
        score = 5.0
    return _component(
        "poi_freshness",
        score,
        "POI freshness is scored from mitigation and retest count.",
    )


def _score_session_timing(
    setup: Mapping[str, Any],
    caps: list[dict[str, Any]],
    hard_filters: list[str],
    config: Mapping[str, Any],
) -> _ComponentScore:
    session = _first_mapping(setup, "session_context", "killzone_status", "session_status") or {}
    status = _text(_get(session, "status", "session_status", default=""))
    name = _text(_get(session, "session_name", "session", default=""))
    ideal = bool(_get(session, "ideal_killzone", "inside_ideal_killzone", default=False))
    time_specific = bool(_get(session, "time_window_specific", default=False))
    outside = bool(_get(session, "outside_required_window", default=False))

    if status in {"closed", "weekend", "market_closed"}:
        return _component("session_timing", 0.0, "Market/session is closed.")
    if ideal or status in {"ideal", "killzone", "inside_killzone"}:
        return _component("session_timing", 10.0, "Setup formed inside ideal killzone.")
    if name in {"london", "new_york", "ny", "london_killzone", "newyork_killzone"}:
        return _component("session_timing", 8.0, "Setup formed during active London/NY session.")
    if status in {"active", "normal", "liquid"}:
        return _component("session_timing", 6.0, "Setup formed during normal liquid session.")
    if outside and time_specific:
        _cap(caps, 5.0, "Time-window-specific model is outside its required window.")
        if config["strict_session"]:
            hard_filters.append("outside_required_time_window")
        return _component("session_timing", 3.0, "Setup is outside its required time window.")
    if status in {"rollover", "poor_liquidity"}:
        return _component("session_timing", 2.0, "Setup formed in poor liquidity timing.")
    return _component("session_timing", 5.0, "Session timing is not explicit.")


def _score_news_filter(
    setup: Mapping[str, Any],
    caps: list[dict[str, Any]],
    hard_filters: list[str],
    config: Mapping[str, Any],
) -> _ComponentScore:
    news = _first_mapping(setup, "news_filter", "news_filter_status") or {}
    spread = _first_mapping(setup, "spread_status", "execution_safety") or {}
    restricted = bool(_get(news, "restricted", "news_restricted", default=False))
    spread_text = _text(_get(spread, "spread_status", "status", default="normal"))
    spread_safe = bool(_get(spread, "spread_safe", default=True))
    first_spike = bool(_get(news, "first_spike_only", "news_spike_only", default=False))
    stabilized = bool(_get(news, "post_news_stabilized", "structure_stabilized", default=False))

    if restricted:
        hard_filters.append("news_restricted")
        _cap(caps, 3.0, "Inside high-impact news blackout.")
        return _component("news_filter", 0.0, "High-impact news restriction is active.")
    if spread_text in {"unsafe", "wide", "too_high", "abnormal"} or not spread_safe:
        hard_filters.append("spread_unsafe")
        _cap(caps, 4.0, "Spread or execution conditions are unsafe.")
        return _component("news_filter", 2.0, "Spread/execution safety is unsafe.")
    if first_spike and config["mode"] is SetupScoringMode.CONSERVATIVE:
        hard_filters.append("first_news_spike")
        _cap(caps, 4.0, "Setup depends on first news spike without stabilization.")
        return _component("news_filter", 2.0, "First news spike is not accepted.")
    if stabilized:
        return _component("news_filter", 6.0, "Post-news setup has stabilization evidence.")
    if bool(_get(news, "medium_news_nearby", "minor_news_nearby", default=False)):
        return _component("news_filter", 8.0, "Minor/medium news nearby but no unsafe spread.")
    return _component("news_filter", 10.0, "No news restriction and execution safety is normal.")


def _score_risk_reward(
    setup: Mapping[str, Any],
    caps: list[dict[str, Any]],
    hard_filters: list[str],
    config: Mapping[str, Any],
) -> _ComponentScore:
    risk = setup.get("risk_plan", {}) or {}
    rr = _float(_get(risk, "rr", "reward_to_risk", default=setup.get("rr")))
    stop_valid = bool(_get(risk, "stop_valid", default=True))
    if not stop_valid:
        hard_filters.append("invalid_stop")
        _cap(caps, 4.0, "Stop-loss plan is invalid.")
        return _component("risk_reward", 0.0, "Stop-loss plan is invalid.")
    if rr is None or rr <= 0:
        hard_filters.append("invalid_rr")
        _cap(caps, 5.0, "Reward-to-risk is missing or invalid.")
        return _component("risk_reward", 0.0, "RR is missing or invalid.")
    if rr >= 3.0:
        score = 10.0
    elif rr >= 2.0:
        score = 8.0
    elif rr >= 1.5:
        score = 6.0
    elif rr >= 1.0:
        score = 4.0
    else:
        score = 2.0
    if rr < float(config["min_rr"]):
        hard_filters.append("rr_below_minimum")
        _cap(caps, 5.0, "RR is below minimum requirement.")
    return _component("risk_reward", score, f"RR is {rr:.2f}.")


def _score_target_clarity(
    setup: Mapping[str, Any],
    direction: SetupScoreDirection,
    caps: list[dict[str, Any]],
    hard_filters: list[str],
) -> _ComponentScore:
    target = _first_mapping(setup, "target_liquidity", "target_plan", "target_selection")
    if not target or not bool(_get(target, "valid_trade_target_exists", default=True)):
        hard_filters.append("no_valid_target")
        _cap(caps, 4.5, "No valid liquidity target exists.")
        return _component("target_clarity", 0.0, "No valid target liquidity.")
    blocked = bool(_get(target, "blocked", "target_blocked", default=False)) or bool(
        _get(target, "blocked_targets", default=[])
    )
    alternate = bool(_get(target, "alternate_target_exists", default=False))
    if blocked and not alternate:
        hard_filters.append("target_blocked_by_htf_poi")
        _cap(caps, 5.0, "Final target is blocked by opposing HTF POI.")
        return _component("target_clarity", 2.0, "Target path is blocked by HTF POI.")

    quality = _float(_get(target, "target_quality_score", "quality_score", default=None))
    if quality is None:
        side = _text(_get(target, "direction", "side", default=""))
        quality = 5.0
        quality += 1.5 if _target_side_aligns(side, direction) else 0.0
        status = _text(_get(target, "swept_status", "status", default="unswept"))
        quality += 1.0 if status in {"unswept", "fresh", "active"} else 0.0
        role = _text(_get(target, "internal_or_external", "liquidity_role", default=""))
        quality += 0.8 if role == "external" else 0.0
        quality += 0.7 if bool(_get(target, "draw_on_liquidity_aligned", default=False)) else 0.0
    if blocked:
        quality = min(quality, 5.0)
    return _component(
        "target_clarity",
        quality,
        "Target clarity scored from liquidity quality and blockers.",
    )


def _score_volume_confirmation(
    setup: Mapping[str, Any],
    warnings: list[str],
) -> _ComponentScore:
    volume = setup.get("volume_confirmation", {}) or {}
    if not volume:
        warnings.append("volume_missing_neutral_score")
        return _component(
            "volume_confirmation",
            5.0,
            "Volume missing; neutral optional score used.",
        )
    score = _float(_get(volume, "volume_score", "score", "confirmation_score", default=5.0), 5.0)
    return _component("volume_confirmation", score or 5.0, "Optional volume confirmation score.")


def _component(name: str, score: float, reason: str) -> _ComponentScore:
    return _ComponentScore(name=name, raw_score=_clamp(score), reason=reason)


def _component_output(
    components: list[_ComponentScore],
    weights: Mapping[str, float],
) -> dict[str, dict[str, Any]]:
    return {
        component.name: {
            "raw_score": round(component.raw_score, 2),
            "weight": weights[component.name],
            "weighted_score": round(component.raw_score * weights[component.name], 2),
            "reason": component.reason,
        }
        for component in components
    }


def _invalid_result(
    setup: Mapping[str, Any],
    config: Mapping[str, Any],
    reason: str,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "function": "score_smc_setup",
        "concept_name": "ICT/SMC Setup Scoring Engine",
        "setup_id": setup.get("setup_id"),
        "symbol": setup.get("symbol"),
        "timeframe": setup.get("timeframe"),
        "direction": None,
        "setup_type": setup.get("setup_type"),
        "total_score": 0.0,
        "uncapped_score": 0.0,
        "grade": SetupGrade.F.value,
        "trade_allowed": False,
        "trade_threshold": float(config["trade_threshold"]),
        "scoring_mode": config["mode"].value,
        "status": SetupScoreStatus.INVALID_CONTEXT.value,
        "component_scores": {},
        "hard_filter_failures": [reason],
        "caps_applied": [],
        "warnings": _dedupe(warnings),
        "reasons": [reason],
        "decision_reason": reason,
    }


def _decision_reasons(
    components: list[_ComponentScore],
    hard_filters: list[str],
    caps: list[dict[str, Any]],
    total_score: float,
) -> list[str]:
    reasons = [f"Final confluence score is {total_score:.2f}."]
    weakest = sorted(components, key=lambda component: component.raw_score)[:3]
    reasons.extend([f"{component.name}: {component.reason}" for component in weakest])
    reasons.extend([f"hard_filter: {item}" for item in _dedupe(hard_filters)])
    reasons.extend([f"cap: {cap['reason']}" for cap in caps])
    return _dedupe(reasons)


def _decision_reason(
    trade_allowed: bool,
    hard_filters: list[str],
    total_score: float,
    threshold: float,
) -> str:
    if trade_allowed:
        return "Setup score meets threshold and no hard filters failed."
    if hard_filters:
        return "Trade blocked by hard filter: " + ", ".join(_dedupe(hard_filters))
    return f"Trade blocked because score {total_score:.2f} is below threshold {threshold:.2f}."


def _apply_caps(score: float, caps: list[dict[str, Any]]) -> float:
    capped = score
    for cap in caps:
        capped = min(capped, float(cap["cap"]))
    return capped


def _cap(caps: list[dict[str, Any]], value: float, reason: str) -> None:
    cap = {"cap": float(value), "reason": reason}
    if cap not in caps:
        caps.append(cap)


def _grade(score: float) -> SetupGrade:
    if score >= 9.0:
        return SetupGrade.A_PLUS
    if score >= 8.0:
        return SetupGrade.A
    if score >= 7.0:
        return SetupGrade.B
    if score >= 6.0:
        return SetupGrade.C
    if score >= 5.0:
        return SetupGrade.D
    return SetupGrade.F


def _structure_context(setup: Mapping[str, Any]) -> Mapping[str, Any]:
    return _first_mapping(
        setup,
        "structure_confirmation",
        "mss_event",
        "bos_event",
        "market_structure_shift",
    ) or {}


def _has_structure_confirmation(setup: Mapping[str, Any]) -> bool:
    structure = _structure_context(setup)
    return bool(_get(structure, "confirmed", "mss_confirmed", "bos_confirmed", default=False))


def _first_mapping(setup: Mapping[str, Any], *keys: str) -> Mapping[str, Any] | None:
    for key in keys:
        value = setup.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def _first_sequence_mapping(setup: Mapping[str, Any], *keys: str) -> Mapping[str, Any] | None:
    for key in keys:
        values = setup.get(key)
        if values and isinstance(values, list) and isinstance(values[0], Mapping):
            return values[0]
    return None


def _exists(raw: Any) -> bool:
    if isinstance(raw, Mapping):
        return bool(_get(raw, "exists", "confirmed", default=bool(raw)))
    return bool(raw)


def _reclaimed(sweep: Mapping[str, Any]) -> bool:
    reclaim = _text(_get(sweep, "reclaim_status", "rejection_status", default=""))
    if reclaim in {"reclaimed", "rejected", "strong_reclaim", "closed_back_inside"}:
        return True
    return bool(_get(sweep, "reclaimed", "rejection_confirmed", default=False))


def _sweep_side_aligns(side: str, direction: SetupScoreDirection) -> bool:
    if direction is SetupScoreDirection.BULLISH:
        return side in {"sell_side", "sellside", "ssl", "low", "below"}
    return side in {"buy_side", "buyside", "bsl", "high", "above"}


def _draw_aligns(draw: str, direction: SetupScoreDirection) -> bool:
    if direction is SetupScoreDirection.BULLISH:
        return draw in {"buy_side", "buyside", "bsl", "above", "high"}
    return draw in {"sell_side", "sellside", "ssl", "below", "low"}


def _target_side_aligns(side: str, direction: SetupScoreDirection) -> bool:
    return _draw_aligns(side, direction)


def _invalidated(raw: Mapping[str, Any]) -> bool:
    status = _text(_get(raw, "status", "active_status", "fresh_status", default="active"))
    return bool(_get(raw, "invalidated", "invalidated_status", default=False)) or status in {
        "invalidated",
        "invalid",
        "broken",
        "consumed",
    }


def _mode(value: Any) -> SetupScoringMode:
    text = _text(value)
    for mode in SetupScoringMode:
        if text == mode.value:
            return mode
    return SetupScoringMode.NORMAL


def _direction(value: Any) -> SetupScoreDirection:
    text = _text(value)
    if text in {"bullish", "buy", "long"}:
        return SetupScoreDirection.BULLISH
    if text in {"bearish", "sell", "short"}:
        return SetupScoreDirection.BEARISH
    return SetupScoreDirection.NONE


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


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, float(value)))


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
