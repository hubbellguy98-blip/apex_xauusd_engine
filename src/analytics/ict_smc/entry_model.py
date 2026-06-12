"""Objective ICT/SMC entry model after setup confirmation.

This module is the final execution-decision layer for already-confirmed ICT/SMC
setups. It does not discover liquidity sweeps, MSS, displacement, FVGs, or order
blocks from scratch. It decides whether a confirmed setup has a valid entry
zone, entry type, stop, target, reward-to-risk, and execution permission.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence


class EntryDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"


class EntryMode(str, Enum):
    AGGRESSIVE = "aggressive"
    BALANCED = "balanced"
    CONSERVATIVE = "conservative"


class EntryType(str, Enum):
    FVG_LIMIT_MIDPOINT = "fvg_limit_midpoint"
    FVG_CONFIRMATION = "fvg_confirmation_entry"
    OB_LIMIT_MEAN_THRESHOLD = "ob_limit_mean_threshold"
    OB_CONFIRMATION = "ob_confirmation_entry"
    LTF_CONFIRMATION_FVG = "ltf_confirmation_fvg_entry"
    RETEST_REACTION = "retest_reaction_entry"


class EntryStatus(str, Enum):
    VALID = "valid_entry_signal"
    SETUP_NOT_CONFIRMED = "setup_not_confirmed"
    INVALID_DIRECTION = "invalid_direction"
    INSUFFICIENT_CONTEXT = "insufficient_setup_context"
    NEWS_RESTRICTED = "news_restricted"
    SPREAD_TOO_HIGH = "spread_too_high"
    SESSION_BLOCKED = "session_filter_blocked"
    NO_VALID_ENTRY_ZONE = "no_valid_entry_zone"
    WAITING_FOR_RETEST = "waiting_for_retest"
    WAITING_FOR_CANDLE_CONFIRMATION = "waiting_for_candle_confirmation"
    WAITING_FOR_LTF_CONFIRMATION = "waiting_for_ltf_confirmation"
    LIMIT_NOT_ALLOWED = "limit_order_not_allowed"
    MARKET_NOT_ALLOWED = "market_order_not_allowed"
    QUALITY_TOO_LOW = "entry_quality_too_low"
    NO_VALID_TARGET = "no_valid_target"
    INVALID_STOP = "invalid_stop_loss"
    STOP_TOO_WIDE = "stop_too_wide"
    POOR_RR = "poor_rr"
    CONFIDENCE_TOO_LOW = "confidence_too_low"


@dataclass(frozen=True, slots=True)
class _Candle:
    index: int
    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True

    @property
    def range(self) -> float:
        return max(0.0, self.high - self.low)

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    @property
    def close_position(self) -> float:
        if self.range <= 0:
            return 0.5
        return (self.close - self.low) / self.range


@dataclass(frozen=True, slots=True)
class _EntryZone:
    zone_id: str
    zone_type: str
    direction: EntryDirection
    zone_low: float
    zone_high: float
    zone_mid: float
    quality_score: float
    fresh_status: str
    retest_status: str
    invalidated: bool
    created_after_mss: bool
    created_by_displacement: bool
    premium_discount_aligned: bool
    source: Mapping[str, Any]


def generate_entry_signal(
    setup: Mapping[str, Any],
    df: Sequence[Mapping[str, Any] | Any] | Any,
    risk_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate an entry decision after an ICT/SMC setup is confirmed."""

    config = _risk_config(risk_config or {})
    warnings = [
        "Entry model requires a confirmed setup first.",
        "Do not enter from raw liquidity sweep, raw FVG, or raw OB alone.",
        "Only confirmed closed candles are used for entry confirmation.",
    ]
    candles = [c for c in _normalize_candles(df) if c.is_closed]
    direction = _direction(setup.get("direction"))
    target = _target_price(setup, direction)

    if not bool(setup.get("confirmed", False)):
        return _blocked(setup, direction, EntryStatus.SETUP_NOT_CONFIRMED, warnings, target=target)
    if direction is EntryDirection.NONE:
        return _blocked(setup, direction, EntryStatus.INVALID_DIRECTION, warnings, target=target)
    if not _has_execution_context(setup):
        return _blocked(setup, direction, EntryStatus.INSUFFICIENT_CONTEXT, warnings, target=target)

    safety_status = _safety_gate(setup, config)
    if safety_status is not None:
        return _blocked(
            setup,
            direction,
            safety_status,
            warnings,
            target=target,
            confidence_score=2.5,
        )

    zones = _candidate_zones(setup, direction, config)
    if not zones:
        return _blocked(
            setup,
            direction,
            EntryStatus.NO_VALID_ENTRY_ZONE,
            warnings,
            target=target,
            confidence_score=4.0,
        )

    last_candle = candles[-1] if candles else None
    selected_zone = _select_zone(zones, setup, last_candle, target, direction)
    mode = EntryMode(str(config["entry_mode"]).lower())
    entry_plan = _entry_plan(setup, selected_zone, candles, config, mode, direction)
    if not entry_plan["valid"]:
        return _blocked(
            setup,
            direction,
            entry_plan["status"],
            warnings + entry_plan["warnings"],
            target=target,
            selected_zone=selected_zone,
            confidence_score=entry_plan["confidence_hint"],
        )

    entry_price = float(entry_plan["entry_price"])
    if target is None or not _target_on_correct_side(entry_price, target, direction):
        return _blocked(
            setup,
            direction,
            EntryStatus.NO_VALID_TARGET,
            warnings + entry_plan["warnings"],
            selected_zone=selected_zone,
            confidence_score=4.5,
        )

    atr = _average_true_range(candles, int(config["atr_period"]))
    stop_buffer = atr * float(config["stop_buffer_atr_multiplier"])
    stop_plan = _stop_plan(setup, selected_zone, candles, entry_price, direction, stop_buffer)
    if not stop_plan["valid"]:
        return _blocked(
            setup,
            direction,
            EntryStatus.INVALID_STOP,
            warnings + entry_plan["warnings"],
            target=target,
            selected_zone=selected_zone,
            entry_type=entry_plan["entry_type"],
            entry_price=entry_price,
            confidence_score=4.0,
        )

    risk_points, reward_points, rr = _risk_reward(
        entry_price,
        stop_plan["stop_loss"],
        target,
        direction,
    )
    max_stop = atr * float(config["max_stop_atr_multiplier"])
    if max_stop > 0 and risk_points > max_stop:
        return _blocked(
            setup,
            direction,
            EntryStatus.STOP_TOO_WIDE,
            warnings + entry_plan["warnings"],
            target=target,
            selected_zone=selected_zone,
            entry_type=entry_plan["entry_type"],
            entry_price=entry_price,
            stop_loss=stop_plan["stop_loss"],
            rr=rr,
            confidence_score=4.0,
            risk_points=risk_points,
            reward_points=reward_points,
        )
    if rr < float(config["min_rr"]):
        return _blocked(
            setup,
            direction,
            EntryStatus.POOR_RR,
            warnings + entry_plan["warnings"] + ["Do not execute unless RR improves."],
            target=target,
            selected_zone=selected_zone,
            entry_type=entry_plan["entry_type"],
            entry_price=entry_price,
            stop_loss=stop_plan["stop_loss"],
            rr=rr,
            confidence_score=5.0,
            risk_points=risk_points,
            reward_points=reward_points,
        )

    confidence = _confidence_score(setup, selected_zone, entry_plan, rr, config)
    if confidence < float(config["minimum_entry_score"]):
        return _blocked(
            setup,
            direction,
            EntryStatus.CONFIDENCE_TOO_LOW,
            warnings + entry_plan["warnings"],
            target=target,
            selected_zone=selected_zone,
            entry_type=entry_plan["entry_type"],
            entry_price=entry_price,
            stop_loss=stop_plan["stop_loss"],
            rr=rr,
            confidence_score=confidence,
            risk_points=risk_points,
            reward_points=reward_points,
        )

    return {
        "function": "generate_entry_signal",
        "concept_name": "ICT/SMC Entry Model",
        "setup_id": setup.get("setup_id"),
        "entry_signal": True,
        "position_allowed": True,
        "direction": direction.value,
        "entry_type": entry_plan["entry_type"],
        "order_type": entry_plan["order_type"],
        "entry_price": _round(entry_price),
        "stop_loss": _round(stop_plan["stop_loss"]),
        "target": _round(target),
        "rr": round(rr, 2),
        "confidence_score": confidence,
        "status": EntryStatus.VALID.value,
        "selected_zone": _zone_to_dict(selected_zone),
        "risk_plan": {
            "risk_points": _round(risk_points),
            "reward_points": _round(reward_points),
            "minimum_rr_required": float(config["min_rr"]),
            "preferred_rr": float(config["preferred_rr"]),
            "stop_reference": stop_plan["stop_reference"],
            "target_reference": _target_reference(setup),
        },
        "decision": {
            "entry_model_valid": True,
            "execution_allowed": True,
            "rejection_reason": None,
        },
        "reasons": _dedupe(
            [
                "Setup is confirmed before entry evaluation.",
                f"Selected {selected_zone.zone_type} as the highest-ranked entry zone.",
                entry_plan["reason"],
                "Stop-loss, target, and reward-to-risk are valid.",
            ]
            + entry_plan["reasons"]
        ),
        "warnings": _dedupe(warnings + entry_plan["warnings"]),
    }


def _risk_config(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "entry_mode": str(config.get("entry_mode", EntryMode.CONSERVATIVE.value)).lower(),
        "allowed_entry_types": set(config.get("allowed_entry_types", [])),
        "min_rr": float(config.get("min_rr", 1.5)),
        "preferred_rr": float(config.get("preferred_rr", 2.0)),
        "minimum_entry_score": float(config.get("minimum_entry_score", 6.5)),
        "minimum_zone_quality": float(config.get("minimum_zone_quality", 5.0)),
        "aggressive_min_setup_score": float(config.get("aggressive_min_setup_score", 8.0)),
        "aggressive_min_zone_score": float(config.get("aggressive_min_zone_score", 7.0)),
        "stop_buffer_atr_multiplier": float(config.get("stop_buffer_atr_multiplier", 0.05)),
        "max_stop_atr_multiplier": float(config.get("max_stop_atr_multiplier", 3.5)),
        "atr_period": int(config.get("atr_period", 14)),
        "use_ltf_confirmation": bool(config.get("use_ltf_confirmation", False)),
        "allow_market_order": bool(config.get("allow_market_order", True)),
        "allow_limit_order": bool(config.get("allow_limit_order", True)),
        "cancel_if_news_restricted": bool(config.get("cancel_if_news_restricted", True)),
        "cancel_if_zone_invalidated": bool(config.get("cancel_if_zone_invalidated", True)),
    }


def _entry_plan(
    setup: Mapping[str, Any],
    zone: _EntryZone,
    candles: Sequence[_Candle],
    config: Mapping[str, Any],
    mode: EntryMode,
    direction: EntryDirection,
) -> dict[str, Any]:
    if config["use_ltf_confirmation"] and not _ltf_confirmed(setup, direction):
        return _entry_block(EntryStatus.WAITING_FOR_LTF_CONFIRMATION, 5.0)
    if mode is EntryMode.AGGRESSIVE:
        return _aggressive_entry_plan(setup, zone, config)
    if mode is EntryMode.CONSERVATIVE:
        return _conservative_entry_plan(zone, candles, config, direction)
    return _balanced_entry_plan(setup, zone, candles, config, direction)


def _aggressive_entry_plan(
    setup: Mapping[str, Any],
    zone: _EntryZone,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    if not config["allow_limit_order"]:
        return _entry_block(EntryStatus.LIMIT_NOT_ALLOWED, 4.0)
    if float(setup.get("setup_score", 0.0)) < float(config["aggressive_min_setup_score"]):
        return _entry_block(EntryStatus.QUALITY_TOO_LOW, 5.0)
    if zone.quality_score < float(config["aggressive_min_zone_score"]):
        return _entry_block(EntryStatus.QUALITY_TOO_LOW, 5.0)
    entry_type = (
        EntryType.FVG_LIMIT_MIDPOINT.value
        if "fvg" in zone.zone_type
        else EntryType.OB_LIMIT_MEAN_THRESHOLD.value
    )
    if not _entry_type_allowed(entry_type, config):
        return _entry_block(EntryStatus.LIMIT_NOT_ALLOWED, 4.0)
    return {
        "valid": True,
        "status": EntryStatus.VALID,
        "entry_type": entry_type,
        "order_type": "limit_order",
        "entry_price": zone.zone_mid,
        "reason": "Aggressive limit entry selected at zone midpoint or mean threshold.",
        "reasons": ["High setup and zone quality allow aggressive limit entry."],
        "warnings": ["Limit entry is aggressive because it does not wait for candle rejection."],
        "confidence_hint": 7.0,
    }


def _conservative_entry_plan(
    zone: _EntryZone,
    candles: Sequence[_Candle],
    config: Mapping[str, Any],
    direction: EntryDirection,
) -> dict[str, Any]:
    if not config["allow_market_order"]:
        return _entry_block(EntryStatus.MARKET_NOT_ALLOWED, 4.0)
    if not candles or not _zone_retested(zone, candles):
        return _entry_block(EntryStatus.WAITING_FOR_RETEST, 6.0)
    confirmation = _candle_confirmation(zone, candles[-1], direction)
    if not confirmation["confirmed"]:
        return _entry_block(EntryStatus.WAITING_FOR_CANDLE_CONFIRMATION, 6.2)
    entry_type = (
        EntryType.FVG_CONFIRMATION.value
        if "fvg" in zone.zone_type
        else EntryType.OB_CONFIRMATION.value
    )
    if not _entry_type_allowed(entry_type, config):
        return _entry_block(EntryStatus.MARKET_NOT_ALLOWED, 4.0)
    return {
        "valid": True,
        "status": EntryStatus.VALID,
        "entry_type": entry_type,
        "order_type": "market_order_after_closed_candle_confirmation",
        "entry_price": candles[-1].close,
        "reason": "Conservative entry uses closed-candle reaction from the zone.",
        "reasons": confirmation["reasons"],
        "warnings": [],
        "confidence_hint": 7.0,
    }


def _balanced_entry_plan(
    setup: Mapping[str, Any],
    zone: _EntryZone,
    candles: Sequence[_Candle],
    config: Mapping[str, Any],
    direction: EntryDirection,
) -> dict[str, Any]:
    if candles and _zone_retested(zone, candles):
        confirmation = _candle_confirmation(zone, candles[-1], direction)
        if confirmation["confirmed"]:
            return {
                "valid": True,
                "status": EntryStatus.VALID,
                "entry_type": EntryType.RETEST_REACTION.value,
                "order_type": "market_order_after_partial_reaction",
                "entry_price": candles[-1].close,
                "reason": "Balanced entry uses retest plus partial reaction.",
                "reasons": confirmation["reasons"],
                "warnings": [],
                "confidence_hint": 7.0,
            }
    if float(setup.get("setup_score", 0.0)) >= float(config["aggressive_min_setup_score"]):
        return _aggressive_entry_plan(setup, zone, config)
    return _entry_block(EntryStatus.WAITING_FOR_CANDLE_CONFIRMATION, 6.0)


def _entry_block(status: EntryStatus, confidence_hint: float) -> dict[str, Any]:
    return {
        "valid": False,
        "status": status,
        "warnings": [],
        "reasons": [],
        "confidence_hint": confidence_hint,
    }


def _candidate_zones(
    setup: Mapping[str, Any],
    direction: EntryDirection,
    config: Mapping[str, Any],
) -> list[_EntryZone]:
    zones: list[_EntryZone] = []
    for raw in list(setup.get("fvg_zones", []) or []):
        zone = _normalize_zone(raw, "fvg", direction)
        if zone:
            zones.append(zone)
    for raw in list(setup.get("order_blocks", []) or []):
        zone = _normalize_zone(raw, "order_block", direction)
        if zone:
            zones.append(zone)
    minimum_quality = float(config["minimum_zone_quality"])
    return [
        zone
        for zone in zones
        if zone.direction is direction
        and not zone.invalidated
        and zone.quality_score >= minimum_quality
        and zone.fresh_status not in {"fully_filled", "consumed", "stale"}
    ]


def _normalize_zone(
    raw: Mapping[str, Any],
    source_type: str,
    direction: EntryDirection,
) -> _EntryZone | None:
    zone_type = str(
        _get(raw, "zone_type", "fvg_type", "order_block_type", "type", default=source_type)
    ).lower()
    zone_direction = _direction(_get(raw, "direction", default=direction.value))
    low = _float(_get(raw, "zone_low", "low", default=None))
    high = _float(_get(raw, "zone_high", "high", default=None))
    if low is None or high is None:
        return None
    zone_low, zone_high = sorted([low, high])
    midpoint = _float(_get(raw, "zone_mid", "midpoint", "mean_threshold", default=None))
    zone_mid = midpoint if midpoint is not None else (zone_low + zone_high) / 2
    invalidated = bool(_get(raw, "invalidated", default=False)) or str(
        _get(raw, "active_status", "status", default="active")
    ).lower() in {"invalid", "invalidated", "inactive", "broken"}
    if "fvg" in zone_type:
        normalized_type = f"{zone_direction.value}_fvg"
    elif "order" in zone_type or "ob" in zone_type:
        normalized_type = f"{zone_direction.value}_order_block"
    else:
        normalized_type = f"{zone_direction.value}_{source_type}"
    return _EntryZone(
        zone_id=str(_get(raw, "zone_id", "fvg_id", "order_block_id", default=normalized_type)),
        zone_type=normalized_type,
        direction=zone_direction,
        zone_low=zone_low,
        zone_high=zone_high,
        zone_mid=zone_mid,
        quality_score=float(_get(raw, "quality_score", "zone_quality_score", default=5.0)),
        fresh_status=str(_get(raw, "fresh_status", "freshness", default="fresh")).lower(),
        retest_status=str(_get(raw, "retest_status", default="not_retested")).lower(),
        invalidated=invalidated,
        created_after_mss=bool(_get(raw, "created_after_mss", default=False)),
        created_by_displacement=bool(_get(raw, "created_by_displacement", default=False)),
        premium_discount_aligned=bool(_get(raw, "premium_discount_aligned", default=True)),
        source=raw,
    )


def _select_zone(
    zones: Sequence[_EntryZone],
    setup: Mapping[str, Any],
    last_candle: _Candle | None,
    target: float | None,
    direction: EntryDirection,
) -> _EntryZone:
    current_price = last_candle.close if last_candle else _float(setup.get("current_price"), 0.0)
    return sorted(
        zones,
        key=lambda zone: _zone_rank(zone, current_price, target, direction),
        reverse=True,
    )[0]


def _zone_rank(
    zone: _EntryZone,
    current_price: float,
    target: float | None,
    direction: EntryDirection,
) -> tuple[float, float, float]:
    quality = zone.quality_score
    quality += 1.5 if zone.retest_status in {"reacted", "confirmed_reaction", "respected"} else 0.0
    quality += 1.0 if zone.fresh_status in {"fresh", "unmitigated"} else 0.0
    quality += 0.8 if zone.created_after_mss else 0.0
    quality += 0.8 if zone.created_by_displacement else 0.0
    quality += 0.6 if zone.premium_discount_aligned else -0.4
    rr_proxy = 0.0
    if target is not None:
        stop_edge = zone.zone_low if direction is EntryDirection.BULLISH else zone.zone_high
        risk_proxy = abs(zone.zone_mid - stop_edge)
        reward_proxy = abs(target - zone.zone_mid)
        rr_proxy = reward_proxy / risk_proxy if risk_proxy > 0 else 0.0
    distance_penalty = -abs(current_price - zone.zone_mid) * 0.01
    return (quality, rr_proxy, distance_penalty)


def _safety_gate(setup: Mapping[str, Any], config: Mapping[str, Any]) -> EntryStatus | None:
    news = setup.get("news_filter_status", {}) or {}
    spread = setup.get("spread_status", {}) or {}
    session = setup.get("killzone_status", setup.get("session_status", {})) or {}
    if config["cancel_if_news_restricted"] and bool(_get(news, "restricted", default=False)):
        return EntryStatus.NEWS_RESTRICTED
    spread_text = str(_get(spread, "spread_status", "status", default="normal")).lower()
    if spread_text in {"wide", "unsafe", "too_high", "abnormal"}:
        return EntryStatus.SPREAD_TOO_HIGH
    if bool(_get(spread, "spread_safe", default=True)) is False:
        return EntryStatus.SPREAD_TOO_HIGH
    session_text = str(_get(session, "status", "session_status", default="allowed")).lower()
    if session_text in {"blocked", "closed", "outside_allowed_session"}:
        return EntryStatus.SESSION_BLOCKED
    return None


def _has_execution_context(setup: Mapping[str, Any]) -> bool:
    has_structure = any(
        bool(setup.get(key))
        for key in ["mss_event", "bos_event", "market_structure_shift", "structure_confirmation"]
    )
    has_displacement = bool(setup.get("displacement")) or bool(setup.get("displacement_confirmed"))
    has_zone = bool(setup.get("fvg_zones") or setup.get("order_blocks"))
    has_target = _target_price(setup, _direction(setup.get("direction"))) is not None
    return has_structure and has_displacement and has_zone and has_target


def _zone_retested(zone: _EntryZone, candles: Sequence[_Candle]) -> bool:
    retested = {"touched", "partially_filled", "half_filled", "deep_filled", "reacted"}
    if zone.retest_status in retested.union({"confirmed_reaction", "respected"}):
        return True
    return any(_candle_touches_zone(candle, zone) for candle in candles[-5:])


def _candle_touches_zone(candle: _Candle, zone: _EntryZone) -> bool:
    return candle.low <= zone.zone_high and candle.high >= zone.zone_low


def _candle_confirmation(
    zone: _EntryZone,
    candle: _Candle,
    direction: EntryDirection,
) -> dict[str, Any]:
    if not _candle_touches_zone(candle, zone):
        return {"confirmed": False, "reasons": []}
    if direction is EntryDirection.BULLISH:
        lower_wick = max(0.0, min(candle.open, candle.close) - candle.low)
        confirmed = (
            candle.bullish
            and candle.close > zone.zone_mid
            and candle.close_position >= 0.60
            and candle.close >= zone.zone_low
        ) or candle.close > zone.zone_high
        if confirmed:
            reasons = ["Closed bullish reaction candle confirmed from entry zone."]
            if lower_wick >= candle.range * 0.25:
                reasons.append("Lower rejection wick supports bullish reaction.")
            return {"confirmed": True, "reasons": reasons}
    if direction is EntryDirection.BEARISH:
        upper_wick = max(0.0, candle.high - max(candle.open, candle.close))
        bearish_close_position = 1.0 - candle.close_position
        confirmed = (
            candle.bearish
            and candle.close < zone.zone_mid
            and bearish_close_position >= 0.60
            and candle.close <= zone.zone_high
        ) or candle.close < zone.zone_low
        if confirmed:
            reasons = ["Closed bearish rejection candle confirmed from entry zone."]
            if upper_wick >= candle.range * 0.25:
                reasons.append("Upper rejection wick supports bearish reaction.")
            return {"confirmed": True, "reasons": reasons}
    return {"confirmed": False, "reasons": []}


def _ltf_confirmed(setup: Mapping[str, Any], direction: EntryDirection) -> bool:
    ltf = setup.get("ltf_confirmation", {}) or {}
    if not ltf:
        return False
    ltf_direction = _direction(_get(ltf, "direction", default=direction.value))
    return (
        bool(_get(ltf, "confirmed", "mss_confirmed", default=False))
        and ltf_direction is direction
    )


def _stop_plan(
    setup: Mapping[str, Any],
    zone: _EntryZone,
    candles: Sequence[_Candle],
    entry_price: float,
    direction: EntryDirection,
    buffer: float,
) -> dict[str, Any]:
    recent = candles[-8:] if candles else []
    invalidation = _float(setup.get("invalidation_level"))
    sweep_extreme = _float(_get(setup, "sweep_extreme", "sweep_low", "sweep_high", default=None))
    if direction is EntryDirection.BULLISH:
        candidates = [zone.zone_low]
        candidates.extend([value for value in [invalidation, sweep_extreme] if value is not None])
        candidates.extend([candle.low for candle in recent])
        stop = min(candidates) - buffer
        return {
            "valid": stop < entry_price,
            "stop_loss": stop,
            "stop_reference": "below_sweep_or_zone_low_with_ATR_buffer",
        }
    candidates = [zone.zone_high]
    candidates.extend([value for value in [invalidation, sweep_extreme] if value is not None])
    candidates.extend([candle.high for candle in recent])
    stop = max(candidates) + buffer
    return {
        "valid": stop > entry_price,
        "stop_loss": stop,
        "stop_reference": "above_sweep_or_zone_high_with_ATR_buffer",
    }


def _target_price(setup: Mapping[str, Any], direction: EntryDirection) -> float | None:
    target = setup.get("target_liquidity", {}) or {}
    if isinstance(target, Mapping):
        price = _float(_get(target, "target_price", "price", "zone_mid", default=None))
        status = str(_get(target, "swept_status", "status", default="active")).lower()
        if status in {"swept", "fully_swept", "cleared", "invalidated"}:
            return None
        return price
    return _float(target)


def _target_reference(setup: Mapping[str, Any]) -> str:
    target = setup.get("target_liquidity", {}) or {}
    if isinstance(target, Mapping):
        return str(_get(target, "target_reference", "liquidity_id", default="target_liquidity"))
    return "target_liquidity"


def _target_on_correct_side(entry_price: float, target: float, direction: EntryDirection) -> bool:
    if direction is EntryDirection.BULLISH:
        return target > entry_price
    if direction is EntryDirection.BEARISH:
        return target < entry_price
    return False


def _risk_reward(
    entry: float,
    stop: float,
    target: float,
    direction: EntryDirection,
) -> tuple[float, float, float]:
    if direction is EntryDirection.BULLISH:
        risk = max(0.0, entry - stop)
        reward = max(0.0, target - entry)
    else:
        risk = max(0.0, stop - entry)
        reward = max(0.0, entry - target)
    rr = reward / risk if risk > 0 else 0.0
    return risk, reward, rr


def _confidence_score(
    setup: Mapping[str, Any],
    zone: _EntryZone,
    entry_plan: Mapping[str, Any],
    rr: float,
    config: Mapping[str, Any],
) -> float:
    score = 1.0
    score += min(float(setup.get("setup_score", 0.0)), 10.0) * 0.28
    score += min(zone.quality_score, 10.0) * 0.22
    score += 0.8 if zone.fresh_status in {"fresh", "unmitigated"} else 0.2
    score += 0.9 if zone.created_after_mss else 0.2
    score += 0.9 if zone.created_by_displacement else 0.2
    score += 0.8 if zone.premium_discount_aligned else -0.4
    score += 1.0 if "confirmation" in str(entry_plan["entry_type"]) else 0.4
    score += 1.0 if rr >= float(config["preferred_rr"]) else 0.45
    if setup.get("ltf_confirmation"):
        score += 0.8
    return round(max(0.0, min(10.0, score)), 2)


def _blocked(
    setup: Mapping[str, Any],
    direction: EntryDirection,
    status: EntryStatus,
    warnings: Sequence[str],
    *,
    target: float | None = None,
    selected_zone: _EntryZone | None = None,
    entry_type: str | None = None,
    entry_price: float | None = None,
    stop_loss: float | None = None,
    rr: float | None = None,
    confidence_score: float = 3.0,
    risk_points: float | None = None,
    reward_points: float | None = None,
) -> dict[str, Any]:
    return {
        "function": "generate_entry_signal",
        "concept_name": "ICT/SMC Entry Model",
        "setup_id": setup.get("setup_id"),
        "entry_signal": False,
        "position_allowed": False,
        "direction": direction.value if direction is not EntryDirection.NONE else None,
        "entry_type": entry_type,
        "order_type": None,
        "entry_price": _round(entry_price) if entry_price is not None else None,
        "stop_loss": _round(stop_loss) if stop_loss is not None else None,
        "target": _round(target) if target is not None else None,
        "rr": round(rr, 2) if rr is not None else None,
        "confidence_score": round(confidence_score, 2),
        "status": status.value,
        "rejection_reason": status.value,
        "selected_zone": _zone_to_dict(selected_zone) if selected_zone else None,
        "risk_plan": {
            "risk_points": _round(risk_points) if risk_points is not None else None,
            "reward_points": _round(reward_points) if reward_points is not None else None,
            "target_reference": _target_reference(setup),
        },
        "decision": {
            "entry_model_valid": status
            not in {
                EntryStatus.SETUP_NOT_CONFIRMED,
                EntryStatus.INVALID_DIRECTION,
                EntryStatus.INSUFFICIENT_CONTEXT,
            },
            "execution_allowed": False,
            "rejection_reason": status.value,
        },
        "reason": status.value,
        "reasons": [status.value],
        "warnings": _dedupe(list(warnings)),
    }


def _normalize_candles(rows: Sequence[Mapping[str, Any] | Any] | Any) -> list[_Candle]:
    candles: list[_Candle] = []
    for position, row in enumerate(_records(rows)):
        candles.append(
            _Candle(
                index=int(_get(row, "index", default=position)),
                timestamp=_get(row, "timestamp", "time", default=position),
                open=float(_get(row, "open", default=0.0)),
                high=float(_get(row, "high", default=0.0)),
                low=float(_get(row, "low", default=0.0)),
                close=float(_get(row, "close", default=0.0)),
                volume=float(_get(row, "volume", default=0.0)),
                is_closed=bool(_get(row, "is_closed", "closed", default=True)),
            )
        )
    return sorted(candles, key=lambda candle: (candle.timestamp, candle.index))


def _average_true_range(candles: Sequence[_Candle], period: int) -> float:
    if len(candles) < 2:
        return 0.0
    ranges: list[float] = []
    for previous, current in zip(candles, candles[1:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    selected = ranges[-period:] if len(ranges) >= period else ranges
    return mean(selected) if selected else 0.0


def _direction(value: Any) -> EntryDirection:
    text = str(value or "").lower()
    if text in {"bullish", "buy", "long"}:
        return EntryDirection.BULLISH
    if text in {"bearish", "sell", "short"}:
        return EntryDirection.BEARISH
    return EntryDirection.NONE


def _entry_type_allowed(entry_type: str, config: Mapping[str, Any]) -> bool:
    allowed = config.get("allowed_entry_types", set())
    if not allowed or entry_type in allowed:
        return True
    aliases = {
        EntryType.FVG_LIMIT_MIDPOINT.value: "fvg_limit",
        EntryType.FVG_CONFIRMATION.value: "fvg_confirmation",
        EntryType.OB_LIMIT_MEAN_THRESHOLD.value: "ob_limit",
        EntryType.OB_CONFIRMATION.value: "ob_confirmation",
        EntryType.RETEST_REACTION.value: "market_confirmation",
        EntryType.LTF_CONFIRMATION_FVG.value: "ltf_confirmation",
    }
    return aliases.get(entry_type) in allowed


def _zone_to_dict(zone: _EntryZone | None) -> dict[str, Any] | None:
    if zone is None:
        return None
    return {
        "zone_id": zone.zone_id,
        "zone_type": zone.zone_type,
        "zone_low": _round(zone.zone_low),
        "zone_high": _round(zone.zone_high),
        "zone_mid": _round(zone.zone_mid),
        "quality_score": round(zone.quality_score, 2),
        "fresh_status": zone.fresh_status,
        "retest_status": zone.retest_status,
        "created_after_mss": zone.created_after_mss,
        "created_by_displacement": zone.created_by_displacement,
        "premium_discount_aligned": zone.premium_discount_aligned,
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
