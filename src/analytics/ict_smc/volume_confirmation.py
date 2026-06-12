"""Optional volume confirmation layer for ICT/SMC events.

Volume is treated as a secondary confidence layer. For XAUUSD and forex this
usually means broker tick volume, so the score confirms activity context rather
than proving centralized institutional volume.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence


class VolumeConfirmationStatus(str, Enum):
    CONTRADICTORY = "weak_or_contradictory_volume"
    WEAK = "weak_volume_confirmation"
    NEUTRAL = "neutral_volume_confirmation"
    STRONG = "strong_volume_confirmation"
    EXCELLENT = "excellent_volume_confirmation"
    LOW_VOLUME_PULLBACK = "healthy_low_volume_pullback"
    NEWS_SPIKE_WARNING = "news_spike_warning"


class VolumeEventType(str, Enum):
    LIQUIDITY_SWEEP = "liquidity_sweep"
    DISPLACEMENT = "displacement"
    FVG_RETRACEMENT = "fvg_retracement"
    ORDER_BLOCK_RETEST = "order_block_retest"
    ABSORPTION = "absorption"
    REJECTION = "rejection"
    BREAKOUT_CONTINUATION = "breakout_continuation"
    MSS_CONFIRMATION = "mss_confirmation"
    BOS_CONFIRMATION = "bos_confirmation"


class VolumeDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


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
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_to_range_ratio(self) -> float:
        if self.range == 0:
            return 0.0
        return self.body / self.range

    @property
    def upper_wick_ratio(self) -> float:
        if self.range == 0:
            return 0.0
        return max(0.0, self.high - max(self.open, self.close)) / self.range

    @property
    def lower_wick_ratio(self) -> float:
        if self.range == 0:
            return 0.0
        return max(0.0, min(self.open, self.close) - self.low) / self.range

    @property
    def bullish_close_position(self) -> float:
        if self.range == 0:
            return 0.0
        return (self.close - self.low) / self.range

    @property
    def bearish_close_position(self) -> float:
        if self.range == 0:
            return 0.0
        return (self.high - self.close) / self.range


def score_volume_confirmation(
    df: Sequence[Mapping[str, Any]],
    event: Mapping[str, Any],
    *,
    default_lookback: int = 20,
) -> dict[str, Any]:
    """Score whether volume supports an existing ICT/SMC event.

    Volume does not create a trade signal. It only confirms, weakens, or warns
    about an event that was already detected by price-action logic.
    """

    candles = _to_candles(df)
    event_type = str(_get(event, "event_type", default="")).lower()
    direction = str(_get(event, "direction", default="neutral")).lower()
    indices = [int(item) for item in _get(event, "candle_indices", default=[]) or []]

    if not candles:
        return _empty_result(event, "No confirmed closed candles were available.")
    if not event_type or not indices:
        return _empty_result(event, "No event candles provided.")

    by_index = {candle.index: candle for candle in candles}
    event_candles = [by_index[index] for index in indices if index in by_index]
    if not event_candles:
        return _empty_result(event, "Event candle indices were not found.")

    event_start = min(candle.index for candle in event_candles)
    lookback = int(_get(event, "reference_volume_lookback", default=default_lookback))
    reference = [candle for candle in candles if candle.index < event_start][-lookback:]
    warnings = [
        "Volume is optional SMC/ICT confluence, not a standalone entry signal.",
        "For XAUUSD/forex, broker volume is usually tick-volume activity.",
    ]
    if len(reference) < max(5, min(lookback, 10)):
        warnings.append("insufficient_volume_history")

    volume_ma = mean([candle.volume for candle in reference]) if reference else 0.0
    if volume_ma <= 0:
        return _empty_result(
            event,
            "Volume baseline is unavailable or zero.",
            warnings=warnings,
        )

    reference_ranges = [candle.range for candle in reference if candle.range > 0]
    reference_range_ma = mean(reference_ranges) if reference_ranges else 0.0
    metrics = _base_metrics(event_candles, volume_ma, reference, reference_range_ma)
    event_type_enum = _event_type(event_type)
    direction_enum = _direction(direction)

    if event_type_enum == VolumeEventType.LIQUIDITY_SWEEP:
        score, pattern, reasons = _score_liquidity_sweep(
            event_candles,
            event,
            direction_enum,
            metrics,
        )
    elif event_type_enum == VolumeEventType.DISPLACEMENT:
        score, pattern, reasons = _score_displacement(
            event_candles,
            event,
            direction_enum,
            metrics,
        )
    elif event_type_enum in {
        VolumeEventType.FVG_RETRACEMENT,
        VolumeEventType.ORDER_BLOCK_RETEST,
    }:
        score, pattern, reasons = _score_retracement(
            candles,
            event_candles,
            event,
            direction_enum,
            metrics,
        )
    elif event_type_enum in {VolumeEventType.ABSORPTION, VolumeEventType.REJECTION}:
        score, pattern, reasons = _score_absorption_or_rejection(
            event_candles,
            event,
            direction_enum,
            metrics,
        )
    elif event_type_enum == VolumeEventType.BREAKOUT_CONTINUATION:
        score, pattern, reasons = _score_breakout_continuation(
            event_candles,
            event,
            direction_enum,
            metrics,
        )
    else:
        score, pattern, reasons = _score_generic_confirmation(
            event_candles,
            direction_enum,
            metrics,
        )

    if bool(_get(event, "news_flag", default=False)):
        score = min(score, 4.0)
        pattern["news_spike_warning"] = True
        reasons.append("Volume spike is marked as news-sensitive.")
        warnings.append("do_not_treat_news_spike_as_normal_smc_confirmation")

    score = round(max(0.0, min(10.0, score)), 2)
    status = _confirmation_status(score, pattern)
    interpretation = _interpretation(event_type_enum, direction_enum, status, reasons)

    return {
        "function": "score_volume_confirmation",
        "event_id": _get(event, "event_id", default=None),
        "event_type": event_type_enum.value,
        "direction": direction_enum.value,
        "candle_indices": indices,
        "volume_score": score,
        "confirmation_status": status.value,
        "interpretation": interpretation,
        "metrics": metrics,
        "volume_pattern": pattern,
        "warnings": warnings,
        "reasons": reasons,
        "entry_allowed_from_volume_alone": False,
    }


def _score_liquidity_sweep(
    candles: Sequence[_Candle],
    event: Mapping[str, Any],
    direction: VolumeDirection,
    metrics: Mapping[str, float],
) -> tuple[float, dict[str, Any], list[str]]:
    candle = candles[-1]
    level = _optional_float(event, "level_price")
    bullish = direction == VolumeDirection.BULLISH
    relative = metrics["event_relative_volume"]
    score = _relative_volume_points(relative, strong=1.5)
    reasons: list[str] = []

    high_volume = relative >= 1.2
    if high_volume:
        reasons.append("Sweep candle volume expanded above recent average.")

    if bullish:
        reclaim = level is not None and candle.low < level and candle.close > level
        wick_score = _scale(candle.lower_wick_ratio, 0.2, 0.55, 0.0, 1.5)
        directional_close = _scale(candle.bullish_close_position, 0.45, 0.75, 0.0, 1.5)
    else:
        reclaim = level is not None and candle.high > level and candle.close < level
        wick_score = _scale(candle.upper_wick_ratio, 0.2, 0.55, 0.0, 1.5)
        directional_close = _scale(candle.bearish_close_position, 0.45, 0.75, 0.0, 1.5)

    score += wick_score + directional_close
    if reclaim:
        score += 2.0
        reasons.append("Price reclaimed/rejected the swept liquidity level.")
    else:
        reasons.append("No clean reclaim/rejection was confirmed.")

    follow_through = _follow_through_volume(event, metrics)
    if follow_through:
        score += 1.5
        reasons.append("Follow-through volume supported the reversal side.")

    if high_volume and not reclaim:
        score = min(score, 4.0)
        reasons.append("High volume accepted beyond the level instead of reversing.")

    pattern = {
        "high_volume_sweep": high_volume,
        "absorption_detected": reclaim and high_volume,
        "reclaim_supported": reclaim,
        "follow_through_volume_confirmed": follow_through,
        "volume_contradiction": high_volume and not reclaim,
    }
    return score, pattern, reasons


def _score_displacement(
    candles: Sequence[_Candle],
    event: Mapping[str, Any],
    direction: VolumeDirection,
    metrics: Mapping[str, float],
) -> tuple[float, dict[str, Any], list[str]]:
    relative = metrics["event_relative_volume"]
    dominance = _directional_volume_dominance(candles, direction)
    close_quality = _average_directional_close(candles, direction)
    score = _relative_volume_points(relative, strong=1.4)
    score += _scale(dominance, 0.45, 0.75, 0.0, 2.0)
    score += _scale(close_quality, 0.45, 0.75, 0.0, 1.5)
    score += _scale(metrics["range_expansion_ratio"], 1.0, 1.8, 0.0, 1.5)

    structure_break = bool(_get(event, "structure_break_confirmed", default=False))
    fvg_created = bool(_get(event, "fvg_created", default=False))
    if structure_break:
        score += 1.5
    else:
        score = min(score, 5.0)
    if fvg_created:
        score += 1.0
    else:
        score = min(score, 6.0)

    if relative < 0.8 and metrics["average_body_to_range_ratio"] < 0.45:
        score = min(score, 4.0)

    pattern = {
        "volume_expansion": relative >= 1.2,
        "directional_volume_support": dominance >= 0.6,
        "structure_break_supported": structure_break,
        "fvg_creation_supported": fvg_created,
        "range_expansion_with_volume": metrics["range_expansion_ratio"] >= 1.2,
    }
    reasons = [
        "Displacement volume expanded relative to baseline."
        if relative >= 1.2
        else "Displacement volume did not expand strongly.",
        "Directional candle volume dominated the event."
        if dominance >= 0.6
        else "Directional volume dominance was limited.",
    ]
    return score, pattern, reasons


def _score_retracement(
    all_candles: Sequence[_Candle],
    event_candles: Sequence[_Candle],
    event: Mapping[str, Any],
    direction: VolumeDirection,
    metrics: Mapping[str, float],
) -> tuple[float, dict[str, Any], list[str]]:
    displacement_avg = _indexed_average_volume(
        all_candles,
        _get(event, "displacement_indices", default=[]),
    )
    if displacement_avg <= 0:
        displacement_avg = float(_get(event, "displacement_avg_volume", default=0.0))
    pullback_avg = metrics["event_avg_volume"]
    ratio = pullback_avg / displacement_avg if displacement_avg > 0 else 1.0
    zone_respected = not bool(_get(event, "zone_invalidated", default=False))
    reaction_avg = _indexed_average_volume(
        all_candles,
        _get(event, "reaction_indices", default=[]),
    )
    reaction_ratio = reaction_avg / pullback_avg if pullback_avg > 0 else 0.0
    close_quality = _average_directional_close(event_candles, direction)

    score = _inverse_scale(ratio, 0.45, 1.0, 2.0, 0.0)
    score += _inverse_scale(metrics["event_avg_range"], 0.4, 1.1, 1.5, 0.0)
    score += 2.0 if zone_respected else 0.0
    score += _scale(reaction_ratio, 1.0, 1.6, 0.0, 2.0)
    score += _scale(close_quality, 0.45, 0.75, 0.0, 1.5)
    score += 1.0 if bool(_get(event, "continuation_confirmed", default=False)) else 0.0

    if not zone_respected:
        score = min(score, 4.0)
    if reaction_avg <= 0:
        score = min(score, 5.0)
    if ratio >= 1.0:
        score = min(score, 6.0)

    pattern = {
        "low_volume_pullback": ratio <= 0.7,
        "zone_respected": zone_respected,
        "reaction_volume_increased": reaction_ratio >= 1.2,
        "pullback_to_displacement_volume_ratio": round(ratio, 4),
    }
    reasons = [
        "Pullback volume was corrective versus displacement."
        if ratio <= 0.7
        else "Pullback volume was not clearly corrective.",
        "The FVG/OB zone held."
        if zone_respected
        else "The FVG/OB zone was invalidated.",
    ]
    return score, pattern, reasons


def _score_absorption_or_rejection(
    candles: Sequence[_Candle],
    event: Mapping[str, Any],
    direction: VolumeDirection,
    metrics: Mapping[str, float],
) -> tuple[float, dict[str, Any], list[str]]:
    candle = candles[-1]
    level = _optional_float(event, "level_price")
    bullish = direction == VolumeDirection.BULLISH
    relative = metrics["event_relative_volume"]
    score = _relative_volume_points(relative, strong=1.5)

    if bullish:
        reclaim = level is not None and candle.low < level and candle.close > level
        wick_score = _scale(candle.lower_wick_ratio, 0.2, 0.55, 0.0, 1.5)
        close_score = _scale(candle.bullish_close_position, 0.45, 0.75, 0.0, 1.5)
    else:
        reclaim = level is not None and candle.high > level and candle.close < level
        wick_score = _scale(candle.upper_wick_ratio, 0.2, 0.55, 0.0, 1.5)
        close_score = _scale(candle.bearish_close_position, 0.45, 0.75, 0.0, 1.5)

    score += wick_score + close_score
    score += 2.0 if reclaim else 0.0
    follow_up = bool(_get(event, "mss_confirmed", "displacement_confirmed", default=False))
    score += 2.0 if follow_up else 0.0
    if not reclaim:
        score = min(score, 4.0)
    if not follow_up:
        score = min(score, 5.0)

    pattern = {
        "high_volume_at_liquidity": relative >= 1.5,
        "failure_to_continue": reclaim,
        "wick_rejection": wick_score >= 0.75,
        "follow_up_structure": follow_up,
    }
    reasons = [
        "High activity occurred at liquidity."
        if relative >= 1.5
        else "Activity at liquidity was not high enough.",
        "Price failed to continue beyond the level."
        if reclaim
        else "Price did not cleanly reject the level.",
    ]
    return score, pattern, reasons


def _score_breakout_continuation(
    candles: Sequence[_Candle],
    event: Mapping[str, Any],
    direction: VolumeDirection,
    metrics: Mapping[str, float],
) -> tuple[float, dict[str, Any], list[str]]:
    candle = candles[-1]
    level = _optional_float(event, "level_price")
    bullish = direction == VolumeDirection.BULLISH
    close_beyond = (
        level is not None
        and ((bullish and candle.close > level) or (not bullish and candle.close < level))
    )
    score = _relative_volume_points(metrics["event_relative_volume"], strong=1.4)
    score += 2.0 if close_beyond else 0.0
    score += 1.5 if bool(_get(event, "retest_held", default=False)) else 0.0
    score += 1.5 if bool(_get(event, "continuation_confirmed", default=False)) else 0.0
    if not close_beyond:
        score = min(score, 4.0)
    pattern = {
        "volume_expansion": metrics["event_relative_volume"] >= 1.2,
        "acceptance_beyond_level": close_beyond,
        "retest_held": bool(_get(event, "retest_held", default=False)),
        "continuation_confirmed": bool(_get(event, "continuation_confirmed", default=False)),
    }
    return score, pattern, ["Volume scored as breakout continuation context."]


def _score_generic_confirmation(
    candles: Sequence[_Candle],
    direction: VolumeDirection,
    metrics: Mapping[str, float],
) -> tuple[float, dict[str, Any], list[str]]:
    dominance = _directional_volume_dominance(candles, direction)
    score = 4.0 + _relative_volume_points(metrics["event_relative_volume"], strong=1.4)
    score += _scale(dominance, 0.45, 0.75, 0.0, 1.5)
    pattern = {
        "volume_expansion": metrics["event_relative_volume"] >= 1.2,
        "directional_volume_support": dominance >= 0.6,
    }
    return score, pattern, ["Generic volume confirmation was evaluated."]


def _base_metrics(
    event_candles: Sequence[_Candle],
    volume_ma: float,
    reference: Sequence[_Candle],
    reference_range_ma: float,
) -> dict[str, float]:
    event_volumes = [candle.volume for candle in event_candles]
    event_ranges = [candle.range for candle in event_candles if candle.range > 0]
    event_avg = mean(event_volumes)
    event_sum = sum(event_volumes)
    event_max = max(event_volumes)
    event_avg_range = mean(event_ranges) if event_ranges else 0.0
    reference_volumes = [candle.volume for candle in reference]
    return {
        "volume_ma": round(volume_ma, 4),
        "volume_std": round(pstdev(reference_volumes), 4) if len(reference_volumes) > 1 else 0.0,
        "event_avg_volume": round(event_avg, 4),
        "event_volume_sum": round(event_sum, 4),
        "event_max_volume": round(event_max, 4),
        "event_relative_volume": round(event_avg / volume_ma, 4),
        "max_relative_volume": round(event_max / volume_ma, 4),
        "average_body_to_range_ratio": round(
            mean([candle.body_to_range_ratio for candle in event_candles]),
            4,
        ),
        "average_upper_wick_ratio": round(
            mean([candle.upper_wick_ratio for candle in event_candles]),
            4,
        ),
        "average_lower_wick_ratio": round(
            mean([candle.lower_wick_ratio for candle in event_candles]),
            4,
        ),
        "average_bullish_close_position": round(
            mean([candle.bullish_close_position for candle in event_candles]),
            4,
        ),
        "average_bearish_close_position": round(
            mean([candle.bearish_close_position for candle in event_candles]),
            4,
        ),
        "event_avg_range": round(event_avg_range, 4),
        "reference_avg_range": round(reference_range_ma, 4),
        "range_expansion_ratio": round(
            event_avg_range / reference_range_ma if reference_range_ma > 0 else 0.0,
            4,
        ),
    }


def _directional_volume_dominance(
    candles: Sequence[_Candle],
    direction: VolumeDirection,
) -> float:
    total = sum(candle.volume for candle in candles)
    if total <= 0:
        return 0.0
    if direction == VolumeDirection.BULLISH:
        directional = sum(candle.volume for candle in candles if candle.bullish)
    elif direction == VolumeDirection.BEARISH:
        directional = sum(candle.volume for candle in candles if candle.bearish)
    else:
        directional = total / 2
    return directional / total


def _average_directional_close(
    candles: Sequence[_Candle],
    direction: VolumeDirection,
) -> float:
    if direction == VolumeDirection.BEARISH:
        return mean([candle.bearish_close_position for candle in candles])
    return mean([candle.bullish_close_position for candle in candles])


def _follow_through_volume(
    event: Mapping[str, Any],
    metrics: Mapping[str, float],
) -> bool:
    explicit = _get(event, "follow_through_volume_confirmed", default=None)
    if explicit is not None:
        return bool(explicit)
    reaction_avg = float(_get(event, "reaction_avg_volume", default=0.0))
    return reaction_avg > 0 and reaction_avg >= metrics["event_avg_volume"] * 0.8


def _indexed_average_volume(
    candles: Sequence[_Candle],
    indices: Sequence[Any],
) -> float:
    wanted = {int(item) for item in indices or []}
    selected = [candle.volume for candle in candles if candle.index in wanted]
    return mean(selected) if selected else 0.0


def _relative_volume_points(relative: float, *, strong: float) -> float:
    if relative < 0.7:
        return 0.25
    if relative < 1.0:
        return 0.75
    if relative < 1.2:
        return 1.25
    if relative < strong:
        return 2.0
    if relative < 2.5:
        return 3.0
    return 2.0


def _confirmation_status(
    score: float,
    pattern: Mapping[str, Any],
) -> VolumeConfirmationStatus:
    if pattern.get("volume_contradiction"):
        return VolumeConfirmationStatus.CONTRADICTORY
    if pattern.get("news_spike_warning"):
        return VolumeConfirmationStatus.NEWS_SPIKE_WARNING
    if pattern.get("low_volume_pullback") and score >= 7:
        return VolumeConfirmationStatus.LOW_VOLUME_PULLBACK
    if score >= 9:
        return VolumeConfirmationStatus.EXCELLENT
    if score >= 7:
        return VolumeConfirmationStatus.STRONG
    if score >= 5:
        return VolumeConfirmationStatus.NEUTRAL
    if score >= 3:
        return VolumeConfirmationStatus.WEAK
    return VolumeConfirmationStatus.CONTRADICTORY


def _interpretation(
    event_type: VolumeEventType,
    direction: VolumeDirection,
    status: VolumeConfirmationStatus,
    reasons: Sequence[str],
) -> str:
    readable = event_type.value.replace("_", " ")
    side = direction.value
    if status == VolumeConfirmationStatus.NEWS_SPIKE_WARNING:
        prefix = "Abnormal news-driven volume; do not treat it as clean confirmation."
    elif status in {VolumeConfirmationStatus.STRONG, VolumeConfirmationStatus.EXCELLENT}:
        prefix = f"Volume supports {side} {readable}."
    elif status == VolumeConfirmationStatus.LOW_VOLUME_PULLBACK:
        prefix = f"Volume supports healthy corrective {side} pullback/retest context."
    elif status == VolumeConfirmationStatus.CONTRADICTORY:
        prefix = f"Volume contradicts or fails to support {side} {readable}."
    else:
        prefix = f"Volume gives {status.value.replace('_', ' ')} for {side} {readable}."
    if not reasons:
        return prefix
    return f"{prefix} {' '.join(reasons)}"


def _event_type(value: str) -> VolumeEventType:
    for item in VolumeEventType:
        if value == item.value:
            return item
    return VolumeEventType.MSS_CONFIRMATION


def _direction(value: str) -> VolumeDirection:
    if value == VolumeDirection.BEARISH.value:
        return VolumeDirection.BEARISH
    if value == VolumeDirection.BULLISH.value:
        return VolumeDirection.BULLISH
    return VolumeDirection.NEUTRAL


def _empty_result(
    event: Mapping[str, Any],
    interpretation: str,
    *,
    warnings: Sequence[str] | None = None,
) -> dict[str, Any]:
    return {
        "function": "score_volume_confirmation",
        "event_id": _get(event, "event_id", default=None),
        "event_type": str(_get(event, "event_type", default="unknown")),
        "direction": str(_get(event, "direction", default="neutral")),
        "candle_indices": list(_get(event, "candle_indices", default=[]) or []),
        "volume_score": 0.0,
        "confirmation_status": VolumeConfirmationStatus.CONTRADICTORY.value,
        "interpretation": interpretation,
        "metrics": {},
        "volume_pattern": {},
        "warnings": list(warnings or []),
        "reasons": [],
        "entry_allowed_from_volume_alone": False,
    }


def _to_candles(rows: Sequence[Mapping[str, Any]]) -> list[_Candle]:
    candles: list[_Candle] = []
    for position, row in enumerate(_records(rows)):
        is_closed = bool(_get(row, "is_closed", "closed", default=True))
        if not is_closed:
            continue
        candles.append(
            _Candle(
                index=int(_get(row, "index", default=position)),
                timestamp=_get(row, "timestamp", "time", "datetime", default=position),
                open=float(_get(row, "open", default=0.0)),
                high=float(_get(row, "high", default=0.0)),
                low=float(_get(row, "low", default=0.0)),
                close=float(_get(row, "close", default=0.0)),
                volume=float(_get(row, "volume", default=0.0)),
                is_closed=is_closed,
            )
        )
    return sorted(candles, key=lambda candle: (str(candle.timestamp), candle.index))


def _records(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if hasattr(rows, "to_dict"):
        return list(rows.to_dict("records"))  # type: ignore[call-arg, union-attr]
    return list(rows)


def _get(row: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return default


def _optional_float(row: Mapping[str, Any], key: str) -> float | None:
    value = _get(row, key, default=None)
    if value is None:
        return None
    return float(value)


def _scale(value: float, low: float, high: float, out_low: float, out_high: float) -> float:
    if value <= low:
        return out_low
    if value >= high:
        return out_high
    ratio = (value - low) / (high - low)
    return out_low + ratio * (out_high - out_low)


def _inverse_scale(
    value: float,
    low: float,
    high: float,
    out_high: float,
    out_low: float,
) -> float:
    if value <= low:
        return out_high
    if value >= high:
        return out_low
    ratio = (value - low) / (high - low)
    return out_high + ratio * (out_low - out_high)
