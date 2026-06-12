"""Logical ICT/SMC stop-loss placement.

This module turns trader-defined invalidation rules into deterministic stop
placement. It does not size the position and it does not submit orders. The
function calculates where the setup idea becomes invalid, applies ATR/spread
buffers, and reports whether the resulting risk distance is usable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence


class StopDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"


class StopMode(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    ENTRY_ZONE_BASED = "entry_zone_based"
    SWEEP_BASED = "sweep_based"
    STRUCTURE_BASED = "structure_based"


class StopStatus(str, Enum):
    VALID = "valid_stop_loss"
    INVALID_DIRECTION = "invalid_direction"
    MISSING_ENTRY_PRICE = "missing_entry_price"
    INSUFFICIENT_CONTEXT = "insufficient_closed_candle_context"
    NO_VALID_INVALIDATION = "no_valid_invalidation_level"
    STOP_INSIDE_BULLISH_POI = "stop_inside_bullish_poi"
    STOP_INSIDE_BEARISH_POI = "stop_inside_bearish_poi"
    INVALID_BULLISH_STOP = "invalid_bullish_stop_above_entry"
    INVALID_BEARISH_STOP = "invalid_bearish_stop_below_entry"
    INVALID_RISK_DISTANCE = "invalid_risk_distance"
    STOP_TOO_TIGHT_FOR_SPREAD = "stop_too_tight_for_spread"
    STOP_TOO_WIDE = "stop_too_wide"
    STOP_TOO_WIDE_FOR_RR = "stop_too_wide_for_rr"


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


@dataclass(frozen=True, slots=True)
class _Zone:
    zone_id: str
    zone_type: str
    zone_low: float
    zone_high: float
    source: str


@dataclass(frozen=True, slots=True)
class _InvalidationCandidate:
    level: float
    reason: str
    source_type: str
    priority: int


def calculate_smc_stop_loss(
    setup: Mapping[str, Any],
    df: Sequence[Mapping[str, Any] | Any] | Any,
    atr: float | None,
    spread_buffer: float | None,
) -> dict[str, Any]:
    """Calculate a structural stop-loss for an ICT/SMC setup.

    The stop is based on the chosen setup direction, selected entry context,
    invalidation references, ATR buffer, spread buffer, and POI boundaries.
    """

    risk_config = _risk_config(setup.get("risk_config", {}) or {})
    warnings = [
        "Stop-loss must protect setup invalidation, not force artificial RR.",
        "Stop placement and position sizing are separate steps.",
        "Only confirmed closed candles are used for swing/ATR context.",
    ]
    candles = [candle for candle in _normalize_candles(df) if candle.is_closed]
    direction = _direction(_get(setup, "direction", default=None))
    entry_price = _float(_get(setup, "entry_price", "entry", "entry_level", default=None))
    stop_mode = _stop_mode(_get(setup, "stop_mode", default=risk_config["stop_mode"]))

    if direction is StopDirection.NONE:
        return _blocked(setup, direction, StopStatus.INVALID_DIRECTION, warnings, stop_mode)
    if entry_price is None:
        return _blocked(setup, direction, StopStatus.MISSING_ENTRY_PRICE, warnings, stop_mode)
    if not candles:
        return _blocked(
            setup,
            direction,
            StopStatus.INSUFFICIENT_CONTEXT,
            warnings,
            stop_mode,
            entry_price=entry_price,
        )

    effective_atr = _float(atr, None)
    if effective_atr is None or effective_atr <= 0:
        effective_atr = _average_true_range(candles, int(risk_config["atr_period"]))
    atr_buffer = (
        effective_atr * float(risk_config["stop_buffer_atr_multiplier"])
        if effective_atr > 0
        else float(risk_config["default_stop_buffer"])
    )
    spread = max(0.0, _float(spread_buffer, 0.0) or 0.0)
    total_buffer = max(0.0, atr_buffer + spread)

    zones = _zones(setup)
    proposed_stop = _float(_get(setup, "proposed_stop_loss", default=None))
    if proposed_stop is not None:
        poi_status = _poi_stop_status(proposed_stop, zones, direction)
        if poi_status is not None:
            suggestion = _corrected_stop_suggestion(setup, zones, direction, total_buffer, entry_price)
            return _blocked(
                setup,
                direction,
                poi_status,
                warnings + ["Existing proposed stop is inside the active POI."],
                stop_mode,
                entry_price=entry_price,
                stop_loss=proposed_stop,
                risk_distance=_risk_distance(entry_price, proposed_stop, direction),
                corrected_stop_suggestion=suggestion,
            )

    candidates = _candidate_invalidations(setup, candles, zones, direction, entry_price)
    if not candidates:
        return _blocked(
            setup,
            direction,
            StopStatus.NO_VALID_INVALIDATION,
            warnings,
            stop_mode,
            entry_price=entry_price,
        )

    selected = _select_candidate(candidates, stop_mode, direction, setup, effective_atr, entry_price)
    stop_loss = _apply_buffer(selected.level, total_buffer, direction)
    poi_status = _poi_stop_status(stop_loss, zones, direction)
    risk_distance = _risk_distance(entry_price, stop_loss, direction)

    status = StopStatus.VALID
    if poi_status is not None:
        status = poi_status
    elif direction is StopDirection.BULLISH and stop_loss >= entry_price:
        status = StopStatus.INVALID_BULLISH_STOP
    elif direction is StopDirection.BEARISH and stop_loss <= entry_price:
        status = StopStatus.INVALID_BEARISH_STOP
    elif risk_distance <= 0:
        status = StopStatus.INVALID_RISK_DISTANCE

    post_warnings = _post_calc_warnings(
        setup,
        direction,
        entry_price,
        stop_loss,
        risk_distance,
        effective_atr,
        spread,
        risk_config,
    )
    warnings = _dedupe(warnings + post_warnings)

    if status is StopStatus.VALID:
        if StopStatus.STOP_TOO_TIGHT_FOR_SPREAD.value in post_warnings:
            status = StopStatus.STOP_TOO_TIGHT_FOR_SPREAD
        elif StopStatus.STOP_TOO_WIDE_FOR_RR.value in post_warnings:
            status = StopStatus.STOP_TOO_WIDE_FOR_RR
        elif StopStatus.STOP_TOO_WIDE.value in post_warnings:
            status = StopStatus.STOP_TOO_WIDE

    rr = _rr_if_target_available(setup, direction, entry_price, stop_loss, risk_distance)
    stop_valid = status is StopStatus.VALID

    return {
        "function": "calculate_smc_stop_loss",
        "concept_name": "ICT/SMC Stop-Loss Logic",
        "setup_id": setup.get("setup_id"),
        "direction": direction.value,
        "entry_price": _round(entry_price),
        "stop_loss": _round(stop_loss),
        "invalidation_reason": selected.reason if stop_valid else status.value,
        "risk_distance": _round(risk_distance),
        "selected_invalidation_level": _round(selected.level),
        "selected_invalidation_source": selected.source_type,
        "stop_mode": stop_mode.value,
        "stop_valid": stop_valid,
        "execution_allowed": stop_valid,
        "rr_if_target_available": round(rr, 2) if rr is not None else None,
        "buffer_details": {
            "atr": _round(effective_atr),
            "atr_buffer": _round(atr_buffer),
            "spread_buffer": _round(spread),
            "total_buffer": _round(total_buffer),
        },
        "candidate_levels": _candidate_dicts(candidates),
        "decision": {
            "status": status.value,
            "requires_better_entry": status
            in {StopStatus.STOP_TOO_WIDE, StopStatus.STOP_TOO_WIDE_FOR_RR},
            "reason": selected.reason if stop_valid else status.value,
        },
        "warnings": warnings,
    }


def _risk_config(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "stop_mode": str(config.get("stop_mode", StopMode.BALANCED.value)).lower(),
        "stop_buffer_atr_multiplier": float(config.get("stop_buffer_atr_multiplier", 0.10)),
        "default_stop_buffer": float(config.get("default_stop_buffer", 0.25)),
        "atr_period": int(config.get("atr_period", 14)),
        "max_stop_atr_multiplier": float(config.get("max_stop_atr_multiplier", 3.0)),
        "minimum_spread_multiple": float(config.get("minimum_spread_multiple", 2.0)),
        "min_rr": float(config.get("min_rr", 1.5)),
        "balanced_sweep_include_atr_multiple": float(
            config.get("balanced_sweep_include_atr_multiple", 1.5)
        ),
    }


def _candidate_invalidations(
    setup: Mapping[str, Any],
    candles: Sequence[_Candle],
    zones: Sequence[_Zone],
    direction: StopDirection,
    entry_price: float,
) -> list[_InvalidationCandidate]:
    candidates: list[_InvalidationCandidate] = []
    if direction is StopDirection.BULLISH:
        _add_candidate(candidates, setup, ["sweep_low", "manipulation_low", "raid_low"], "below_liquidity_sweep_low", "sweep", 100)
        _add_candidate(candidates, setup, ["ltf_sweep_low"], "below_ltf_sweep_low", "ltf_sweep", 95)
        _add_candidate(candidates, setup, ["recent_swing_low"], "below_recent_swing_low", "structure", 80)
        _add_candidate(candidates, setup, ["invalidation_level"], "setup_defined_invalidation", "custom", 75)
        for zone in zones:
            candidates.append(
                _InvalidationCandidate(
                    level=zone.zone_low,
                    reason=_bullish_zone_reason(zone),
                    source_type=zone.source,
                    priority=_zone_priority(zone),
                )
            )
        for candle in candles[-5:]:
            candidates.append(
                _InvalidationCandidate(candle.low, "below_recent_closed_candle_low", "recent_low", 45)
            )
        return [candidate for candidate in candidates if candidate.level < entry_price]

    _add_candidate(candidates, setup, ["sweep_high", "manipulation_high", "raid_high"], "above_liquidity_sweep_high", "sweep", 100)
    _add_candidate(candidates, setup, ["ltf_sweep_high"], "above_ltf_sweep_high", "ltf_sweep", 95)
    _add_candidate(candidates, setup, ["recent_swing_high"], "above_recent_swing_high", "structure", 80)
    _add_candidate(candidates, setup, ["invalidation_level"], "setup_defined_invalidation", "custom", 75)
    for zone in zones:
        candidates.append(
            _InvalidationCandidate(
                level=zone.zone_high,
                reason=_bearish_zone_reason(zone),
                source_type=zone.source,
                priority=_zone_priority(zone),
            )
        )
    for candle in candles[-5:]:
        candidates.append(
            _InvalidationCandidate(candle.high, "above_recent_closed_candle_high", "recent_high", 45)
        )
    return [candidate for candidate in candidates if candidate.level > entry_price]


def _add_candidate(
    candidates: list[_InvalidationCandidate],
    setup: Mapping[str, Any],
    keys: Sequence[str],
    reason: str,
    source_type: str,
    priority: int,
) -> None:
    value = _nested_float(setup, keys)
    if value is not None:
        candidates.append(_InvalidationCandidate(value, reason, source_type, priority))


def _nested_float(setup: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        value = _float(_get(setup, key, default=None))
        if value is not None:
            return value
    ltf = setup.get("ltf_confirmation", {}) or {}
    for key in keys:
        value = _float(_get(ltf, key, default=None))
        if value is not None:
            return value
    return None


def _select_candidate(
    candidates: Sequence[_InvalidationCandidate],
    stop_mode: StopMode,
    direction: StopDirection,
    setup: Mapping[str, Any],
    atr: float,
    entry_price: float,
) -> _InvalidationCandidate:
    if stop_mode is StopMode.CONSERVATIVE:
        return min(candidates, key=lambda c: c.level) if direction is StopDirection.BULLISH else max(candidates, key=lambda c: c.level)
    if stop_mode is StopMode.SWEEP_BASED:
        return _prefer(candidates, {"sweep"}) or _extreme(candidates, direction)
    if stop_mode is StopMode.ENTRY_ZONE_BASED:
        return _prefer(candidates, {"entry_zone", "fvg", "order_block", "poi"}) or _nearest(candidates, direction, entry_price)
    if stop_mode is StopMode.STRUCTURE_BASED:
        return _prefer(candidates, {"structure", "custom", "recent_low", "recent_high"}) or _extreme(candidates, direction)
    if stop_mode is StopMode.AGGRESSIVE:
        return _prefer(candidates, {"ltf_sweep", "fvg", "order_block", "entry_zone", "poi"}) or _nearest(candidates, direction, entry_price)
    return _balanced_candidate(candidates, direction, setup, atr, entry_price)


def _balanced_candidate(
    candidates: Sequence[_InvalidationCandidate],
    direction: StopDirection,
    setup: Mapping[str, Any],
    atr: float,
    entry_price: float,
) -> _InvalidationCandidate:
    nearby_sources = {"entry_zone", "fvg", "order_block", "poi", "structure", "custom", "ltf_sweep"}
    nearby = [candidate for candidate in candidates if candidate.source_type in nearby_sources]
    sweep = _prefer(candidates, {"sweep"})
    risk_config = _risk_config(setup.get("risk_config", {}) or {})
    include_distance = atr * float(risk_config["balanced_sweep_include_atr_multiple"])
    if sweep and include_distance > 0 and abs(entry_price - sweep.level) <= include_distance:
        nearby.append(sweep)
    if not nearby:
        return _nearest(candidates, direction, entry_price)
    return min(nearby, key=lambda c: c.level) if direction is StopDirection.BULLISH else max(nearby, key=lambda c: c.level)


def _prefer(
    candidates: Sequence[_InvalidationCandidate],
    source_types: set[str],
) -> _InvalidationCandidate | None:
    preferred = [candidate for candidate in candidates if candidate.source_type in source_types]
    if not preferred:
        return None
    return sorted(preferred, key=lambda candidate: (-candidate.priority, abs(candidate.level)))[0]


def _extreme(
    candidates: Sequence[_InvalidationCandidate],
    direction: StopDirection,
) -> _InvalidationCandidate:
    return min(candidates, key=lambda c: c.level) if direction is StopDirection.BULLISH else max(candidates, key=lambda c: c.level)


def _nearest(
    candidates: Sequence[_InvalidationCandidate],
    direction: StopDirection,
    entry_price: float,
) -> _InvalidationCandidate:
    return sorted(candidates, key=lambda candidate: abs(entry_price - candidate.level))[0]


def _apply_buffer(level: float, total_buffer: float, direction: StopDirection) -> float:
    if direction is StopDirection.BULLISH:
        return level - total_buffer
    return level + total_buffer


def _post_calc_warnings(
    setup: Mapping[str, Any],
    direction: StopDirection,
    entry_price: float,
    stop_loss: float,
    risk_distance: float,
    atr: float,
    spread_buffer: float,
    risk_config: Mapping[str, Any],
) -> list[str]:
    warnings: list[str] = []
    minimum_spread_distance = spread_buffer * float(risk_config["minimum_spread_multiple"])
    if minimum_spread_distance > 0 and risk_distance < minimum_spread_distance:
        warnings.append(StopStatus.STOP_TOO_TIGHT_FOR_SPREAD.value)
    max_stop_distance = atr * float(risk_config["max_stop_atr_multiplier"]) if atr > 0 else 0.0
    if max_stop_distance > 0 and risk_distance > max_stop_distance:
        warnings.append(StopStatus.STOP_TOO_WIDE.value)
    rr = _rr_if_target_available(setup, direction, entry_price, stop_loss, risk_distance)
    if rr is not None and rr < float(risk_config["min_rr"]):
        warnings.append(StopStatus.STOP_TOO_WIDE_FOR_RR.value)
    return warnings


def _rr_if_target_available(
    setup: Mapping[str, Any],
    direction: StopDirection,
    entry_price: float,
    stop_loss: float,
    risk_distance: float,
) -> float | None:
    target = _target_price(setup)
    if target is None or risk_distance <= 0:
        return None
    reward = target - entry_price if direction is StopDirection.BULLISH else entry_price - target
    if reward <= 0:
        return 0.0
    return reward / risk_distance


def _target_price(setup: Mapping[str, Any]) -> float | None:
    target = setup.get("target", setup.get("take_profit", setup.get("target_liquidity")))
    if isinstance(target, Mapping):
        return _float(_get(target, "target_price", "price", "zone_mid", default=None))
    return _float(target)


def _poi_stop_status(
    stop_loss: float,
    zones: Sequence[_Zone],
    direction: StopDirection,
) -> StopStatus | None:
    if direction is StopDirection.BULLISH:
        if any(zone.zone_low <= stop_loss <= zone.zone_high for zone in zones):
            return StopStatus.STOP_INSIDE_BULLISH_POI
    elif any(zone.zone_low <= stop_loss <= zone.zone_high for zone in zones):
        return StopStatus.STOP_INSIDE_BEARISH_POI
    return None


def _corrected_stop_suggestion(
    setup: Mapping[str, Any],
    zones: Sequence[_Zone],
    direction: StopDirection,
    total_buffer: float,
    entry_price: float,
) -> dict[str, Any] | None:
    candidates = _candidate_invalidations(setup, [], zones, direction, entry_price)
    if not candidates:
        return None
    selected = _extreme(candidates, direction)
    return {
        "selected_invalidation_level": _round(selected.level),
        "suggested_stop_loss": _round(_apply_buffer(selected.level, total_buffer, direction)),
        "reason": selected.reason,
    }


def _zones(setup: Mapping[str, Any]) -> list[_Zone]:
    zones: list[_Zone] = []
    for key, source in [
        ("entry_zone", "entry_zone"),
        ("selected_zone", "entry_zone"),
        ("order_block", "order_block"),
        ("fvg_zone", "fvg"),
        ("poi_zone", "poi"),
    ]:
        zone = _zone(setup.get(key), source)
        if zone:
            zones.append(zone)
    for raw in setup.get("order_blocks", []) or []:
        zone = _zone(raw, "order_block")
        if zone:
            zones.append(zone)
    for raw in setup.get("fvg_zones", []) or []:
        zone = _zone(raw, "fvg")
        if zone:
            zones.append(zone)
    for raw in setup.get("poi_zones", []) or []:
        zone = _zone(raw, "poi")
        if zone:
            zones.append(zone)
    return _dedupe_zones(zones)


def _zone(raw: Mapping[str, Any] | Any, source: str) -> _Zone | None:
    if not raw:
        return None
    low = _float(_get(raw, "zone_low", "low", default=None))
    high = _float(_get(raw, "zone_high", "high", default=None))
    if low is None or high is None:
        return None
    zone_low, zone_high = sorted([low, high])
    return _Zone(
        zone_id=str(_get(raw, "zone_id", "order_block_id", "fvg_id", default=source)),
        zone_type=str(_get(raw, "zone_type", "type", default=source)).lower(),
        zone_low=zone_low,
        zone_high=zone_high,
        source=source,
    )


def _dedupe_zones(zones: Sequence[_Zone]) -> list[_Zone]:
    seen: set[tuple[str, float, float]] = set()
    result: list[_Zone] = []
    for zone in zones:
        key = (zone.source, zone.zone_low, zone.zone_high)
        if key not in seen:
            seen.add(key)
            result.append(zone)
    return result


def _bullish_zone_reason(zone: _Zone) -> str:
    if zone.source == "order_block":
        return "below_bullish_order_block"
    if zone.source == "fvg":
        return "below_bullish_fvg_invalidation"
    if zone.source == "poi":
        return "below_bullish_poi"
    return "below_entry_zone_low"


def _bearish_zone_reason(zone: _Zone) -> str:
    if zone.source == "order_block":
        return "above_bearish_order_block"
    if zone.source == "fvg":
        return "above_bearish_fvg_invalidation"
    if zone.source == "poi":
        return "above_bearish_poi"
    return "above_entry_zone_high"


def _zone_priority(zone: _Zone) -> int:
    if zone.source == "entry_zone":
        return 90
    if zone.source in {"order_block", "fvg", "poi"}:
        return 85
    return 70


def _risk_distance(entry_price: float, stop_loss: float, direction: StopDirection) -> float:
    if direction is StopDirection.BULLISH:
        return entry_price - stop_loss
    if direction is StopDirection.BEARISH:
        return stop_loss - entry_price
    return 0.0


def _blocked(
    setup: Mapping[str, Any],
    direction: StopDirection,
    status: StopStatus,
    warnings: Sequence[str],
    stop_mode: StopMode,
    *,
    entry_price: float | None = None,
    stop_loss: float | None = None,
    risk_distance: float | None = None,
    corrected_stop_suggestion: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "function": "calculate_smc_stop_loss",
        "concept_name": "ICT/SMC Stop-Loss Logic",
        "setup_id": setup.get("setup_id"),
        "direction": direction.value if direction is not StopDirection.NONE else None,
        "entry_price": _round(entry_price),
        "stop_loss": _round(stop_loss),
        "invalidation_reason": status.value,
        "risk_distance": _round(risk_distance),
        "selected_invalidation_level": None,
        "stop_mode": stop_mode.value,
        "stop_valid": False,
        "execution_allowed": False,
        "rr_if_target_available": None,
        "corrected_stop_suggestion": corrected_stop_suggestion,
        "decision": {
            "status": status.value,
            "requires_better_entry": status
            in {StopStatus.STOP_TOO_WIDE, StopStatus.STOP_TOO_WIDE_FOR_RR},
            "reason": status.value,
        },
        "warnings": _dedupe(list(warnings)),
    }


def _candidate_dicts(candidates: Sequence[_InvalidationCandidate]) -> list[dict[str, Any]]:
    return [
        {
            "level": _round(candidate.level),
            "reason": candidate.reason,
            "source_type": candidate.source_type,
            "priority": candidate.priority,
        }
        for candidate in candidates
    ]


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


def _direction(value: Any) -> StopDirection:
    text = str(value or "").lower()
    if text in {"bullish", "buy", "long"}:
        return StopDirection.BULLISH
    if text in {"bearish", "sell", "short"}:
        return StopDirection.BEARISH
    return StopDirection.NONE


def _stop_mode(value: Any) -> StopMode:
    text = str(value or StopMode.BALANCED.value).lower()
    for mode in StopMode:
        if text == mode.value:
            return mode
    return StopMode.BALANCED


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
