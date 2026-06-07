"""Equal highs and equal lows detection for ICT/SMC liquidity mapping.

The function in this module exposes the focused concept requested by the
trader-facing specification: detect equal highs/lows from confirmed swing
points, classify their liquidity side, evaluate sweep status, and score their
quality. It is intentionally observer-only and never authorizes entries.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence

from src.core.domain.market_data import CandleNode


class EqualLiquidityType(str, Enum):
    EQUAL_HIGHS = "equal_highs"
    EQUAL_LOWS = "equal_lows"


class EqualLiquidityDirection(str, Enum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


class EqualLiquidityStatus(str, Enum):
    ACTIVE = "active"
    SWEPT_REJECTED = "swept_rejected"
    SWEPT_RECLAIMED = "swept_reclaimed"
    BROKEN_ABOVE = "broken_or_accepted_above"
    BROKEN_BELOW = "broken_or_accepted_below"
    STALE = "stale"
    INVALID = "invalid"


class EqualLiquiditySweepType(str, Enum):
    NONE = "none"
    BUY_SIDE_SWEEP_REJECTION = "buy_side_sweep_and_rejection"
    SELL_SIDE_SWEEP_RECLAIM = "sell_side_sweep_and_reclaim"
    ACCEPTED_BREAKOUT = "accepted_breakout"
    ACCEPTED_BREAKDOWN = "accepted_breakdown"


class EqualLiquidityQualityGrade(str, Enum):
    INVALID = "invalid"
    WEAK = "weak"
    USABLE = "usable"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"
    STRONG_SWEEP_EVENT = "strong_sweep_event"
    INACTIVE_BROKEN = "inactive_broken_liquidity"


@dataclass(frozen=True, slots=True)
class EqualLiquidityTolerance:
    tolerance_percent: float
    tolerance_value: float
    atr_value: float
    zone_width_within_tolerance: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EqualLiquidityStatusDetails:
    swept: bool
    active_status: EqualLiquidityStatus
    sweep_type: EqualLiquiditySweepType
    swept_at_index: int | None
    swept_at_timestamp: datetime | None
    sweep_condition: str | None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["active_status"] = self.active_status.value
        payload["sweep_type"] = None if self.sweep_type == EqualLiquiditySweepType.NONE else self.sweep_type.value
        if self.swept_at_timestamp is not None:
            payload["swept_at_timestamp"] = self.swept_at_timestamp.isoformat()
        return payload


@dataclass(frozen=True, slots=True)
class EqualLiquidityPool:
    concept_name: str
    symbol: str
    timeframe: str
    liquidity_id: str
    type: EqualLiquidityType
    direction: EqualLiquidityDirection
    zone_low: float
    zone_high: float
    zone_mid: float
    zone_width: float
    touch_count: int
    touched_indexes: tuple[int, ...]
    touched_timestamps: tuple[datetime, ...]
    creation_index: int
    creation_timestamp: datetime
    tolerance: EqualLiquidityTolerance
    status: EqualLiquidityStatusDetails
    liquidity_context: dict[str, Any]
    quality_score: float
    quality_grade: EqualLiquidityQualityGrade
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    entry_allowed_from_equal_liquidity_alone: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["type"] = self.type.value
        payload["direction"] = self.direction.value
        payload["touched_timestamps"] = [item.isoformat() for item in self.touched_timestamps]
        payload["creation_timestamp"] = self.creation_timestamp.isoformat()
        payload["tolerance"] = self.tolerance.as_dict()
        payload["status"] = self.status.as_dict()
        payload["swept"] = self.status.swept
        payload["active_status"] = self.status.active_status.value
        payload["sweep_type"] = payload["status"]["sweep_type"]
        payload["quality_grade"] = self.quality_grade.value
        payload["zone_low"] = round(self.zone_low, 5)
        payload["zone_high"] = round(self.zone_high, 5)
        payload["zone_mid"] = round(self.zone_mid, 5)
        payload["zone_width"] = round(self.zone_width, 5)
        payload["quality_score"] = round(self.quality_score, 2)
        payload["reasons"] = list(self.reasons)
        payload["warnings"] = list(self.warnings)
        return payload


@dataclass(frozen=True, slots=True)
class _EqualCandle:
    index: int
    timestamp: datetime
    open_p: float
    high_p: float
    low_p: float
    close_p: float
    volume: float
    timeframe: str
    symbol: str
    is_closed: bool = True

    @property
    def range(self) -> float:
        return max(0.0, self.high_p - self.low_p)


@dataclass(frozen=True, slots=True)
class _EqualSwing:
    index: int
    timestamp: datetime
    price: float
    type: str
    strength_score: float
    confirmed_status: bool
    timeframe: str


@dataclass(frozen=True, slots=True)
class _CandidateGroup:
    liquidity_type: EqualLiquidityType
    swings: tuple[_EqualSwing, ...]
    tolerance_value: float
    atr_value: float


def detect_equal_highs_lows(
    df: Sequence[CandleNode | Mapping[str, Any]] | Any,
    tolerance_percent: float,
    min_touches: int,
    *,
    swings: Sequence[Mapping[str, Any] | Any] | None = None,
    left_bars: int = 2,
    right_bars: int = 2,
    min_swing_strength: float = 4.0,
    min_touch_spacing: int = 3,
    max_zone_width_atr: float = 0.50,
    sweep_buffer_atr: float = 0.05,
    close_buffer_atr: float = 0.05,
    timeframe: str | None = None,
    atr_period: int = 14,
    symbol: str = "unknown",
) -> list[dict[str, Any]]:
    """Detect equal highs and equal lows as rule-based ICT/SMC liquidity pools."""
    if tolerance_percent <= 0:
        raise ValueError("tolerance_percent must be positive.")
    if min_touches < 2:
        raise ValueError("min_touches must be at least 2.")
    if left_bars < 1 or right_bars < 1:
        raise ValueError("left_bars and right_bars must be positive.")

    candles = _normalize_candles(df, timeframe, symbol)
    closed = [candle for candle in candles if candle.is_closed]
    if len(closed) < left_bars + right_bars + 1:
        return []

    atr_values = _calculate_atr(closed, atr_period)
    if swings is None:
        normalized_swings = _detect_basic_swings(closed, atr_values, left_bars, right_bars, timeframe)
    else:
        normalized_swings = [_normalize_swing(item, timeframe) for item in swings]

    valid_swings = [
        swing
        for swing in normalized_swings
        if swing.confirmed_status and swing.strength_score >= min_swing_strength
    ]
    groups = _build_groups(
        valid_swings,
        closed,
        atr_values,
        tolerance_percent,
        min_touches,
        min_touch_spacing,
    )
    pools = [
        _build_pool(
            group,
            closed,
            atr_values,
            tolerance_percent,
            max_zone_width_atr,
            sweep_buffer_atr,
            close_buffer_atr,
            min_touches,
            symbol,
        )
        for group in groups
    ]
    return [pool.as_dict() for pool in _dedupe_overlapping(pools)]


def _build_groups(
    swings: Sequence[_EqualSwing],
    candles: Sequence[_EqualCandle],
    atr_values: Sequence[float],
    tolerance_percent: float,
    min_touches: int,
    min_touch_spacing: int,
) -> list[_CandidateGroup]:
    groups: list[_CandidateGroup] = []
    for liquidity_type, swing_type in (
        (EqualLiquidityType.EQUAL_HIGHS, "swing_high"),
        (EqualLiquidityType.EQUAL_LOWS, "swing_low"),
    ):
        typed = sorted([swing for swing in swings if swing.type == swing_type], key=lambda item: item.index)
        used_exact_groups: set[tuple[int, ...]] = set()
        for seed in typed:
            tolerance_value, atr_value = _tolerance_for(seed.price, candles, atr_values, seed.index, tolerance_percent)
            group = [seed]
            for candidate in typed:
                if candidate.index <= seed.index:
                    continue
                if abs(candidate.price - seed.price) > tolerance_value:
                    continue
                if not all(abs(candidate.index - member.index) >= min_touch_spacing for member in group):
                    continue
                group.append(candidate)
            if len(group) < min_touches:
                continue
            group = sorted(group, key=lambda item: item.index)
            key = tuple(item.index for item in group)
            if key in used_exact_groups:
                continue
            used_exact_groups.add(key)
            groups.append(_CandidateGroup(liquidity_type, tuple(group), tolerance_value, atr_value))
    return groups


def _build_pool(
    group: _CandidateGroup,
    candles: Sequence[_EqualCandle],
    atr_values: Sequence[float],
    tolerance_percent: float,
    max_zone_width_atr: float,
    sweep_buffer_atr: float,
    close_buffer_atr: float,
    min_touches: int,
    symbol: str,
) -> EqualLiquidityPool:
    prices = [swing.price for swing in group.swings]
    indexes = tuple(swing.index for swing in group.swings)
    timestamps = tuple(swing.timestamp for swing in group.swings)
    zone_low = min(prices)
    zone_high = max(prices)
    zone_mid = (zone_low + zone_high) / 2.0
    zone_width = zone_high - zone_low
    creation_swing = group.swings[min(len(group.swings), min_touches) - 1]
    atr = max(group.atr_value, _atr_near_index(candles, atr_values, creation_swing.index), 1e-9)
    width_limit = atr * max_zone_width_atr
    status = _status_after_creation(
        group.liquidity_type,
        candles,
        creation_swing.index,
        zone_low,
        zone_high,
        atr * sweep_buffer_atr,
        atr * close_buffer_atr,
    )
    direction = (
        EqualLiquidityDirection.BUY_SIDE
        if group.liquidity_type == EqualLiquidityType.EQUAL_HIGHS
        else EqualLiquidityDirection.SELL_SIDE
    )
    reasons = _base_reasons(group.liquidity_type, len(group.swings), status, zone_width, width_limit)
    warnings = _base_warnings(group, status, zone_width, width_limit, candles)
    score = _quality_score(
        group,
        status,
        zone_width,
        width_limit,
        min_touches,
        candles,
        reasons,
        warnings,
    )
    quality_grade = _quality_grade(score, status)
    timeframe = _dominant_timeframe(group.swings)
    prefix = "EQH" if direction == EqualLiquidityDirection.BUY_SIDE else "EQL"
    liquidity_id = f"{prefix}_{timeframe}_{creation_swing.index}"
    tolerance = EqualLiquidityTolerance(
        tolerance_percent=round(tolerance_percent, 6),
        tolerance_value=round(group.tolerance_value, 6),
        atr_value=round(atr, 6),
        zone_width_within_tolerance=zone_width <= group.tolerance_value,
    )
    return EqualLiquidityPool(
        concept_name="Equal Highs / Equal Lows",
        symbol=symbol,
        timeframe=timeframe,
        liquidity_id=liquidity_id,
        type=group.liquidity_type,
        direction=direction,
        zone_low=zone_low,
        zone_high=zone_high,
        zone_mid=zone_mid,
        zone_width=zone_width,
        touch_count=len(group.swings),
        touched_indexes=indexes,
        touched_timestamps=timestamps,
        creation_index=creation_swing.index,
        creation_timestamp=creation_swing.timestamp,
        tolerance=tolerance,
        status=status,
        liquidity_context=_liquidity_context(group.liquidity_type, status),
        quality_score=score,
        quality_grade=quality_grade,
        reasons=tuple(dict.fromkeys(reasons)),
        warnings=tuple(dict.fromkeys(warnings + ["equal_highs_lows_are_liquidity_not_entry_signals"])),
    )


def _status_after_creation(
    liquidity_type: EqualLiquidityType,
    candles: Sequence[_EqualCandle],
    creation_index: int,
    zone_low: float,
    zone_high: float,
    sweep_buffer: float,
    close_buffer: float,
) -> EqualLiquidityStatusDetails:
    for candle in candles:
        if candle.index <= creation_index:
            continue
        if liquidity_type == EqualLiquidityType.EQUAL_HIGHS:
            if candle.close_p > zone_high + close_buffer:
                return EqualLiquidityStatusDetails(
                    True,
                    EqualLiquidityStatus.BROKEN_ABOVE,
                    EqualLiquiditySweepType.ACCEPTED_BREAKOUT,
                    candle.index,
                    candle.timestamp,
                    "candle_close_above_zone_high_with_buffer",
                )
            if candle.high_p > zone_high + sweep_buffer and candle.close_p < zone_high:
                return EqualLiquidityStatusDetails(
                    True,
                    EqualLiquidityStatus.SWEPT_REJECTED,
                    EqualLiquiditySweepType.BUY_SIDE_SWEEP_REJECTION,
                    candle.index,
                    candle.timestamp,
                    "candle_high_above_zone_high_and_close_back_below_zone_high",
                )
        else:
            if candle.close_p < zone_low - close_buffer:
                return EqualLiquidityStatusDetails(
                    True,
                    EqualLiquidityStatus.BROKEN_BELOW,
                    EqualLiquiditySweepType.ACCEPTED_BREAKDOWN,
                    candle.index,
                    candle.timestamp,
                    "candle_close_below_zone_low_with_buffer",
                )
            if candle.low_p < zone_low - sweep_buffer and candle.close_p > zone_low:
                return EqualLiquidityStatusDetails(
                    True,
                    EqualLiquidityStatus.SWEPT_RECLAIMED,
                    EqualLiquiditySweepType.SELL_SIDE_SWEEP_RECLAIM,
                    candle.index,
                    candle.timestamp,
                    "candle_low_below_zone_low_and_close_back_above_zone_low",
                )
    return EqualLiquidityStatusDetails(
        False,
        EqualLiquidityStatus.ACTIVE,
        EqualLiquiditySweepType.NONE,
        None,
        None,
        None,
    )


def _quality_score(
    group: _CandidateGroup,
    status: EqualLiquidityStatusDetails,
    zone_width: float,
    width_limit: float,
    min_touches: int,
    candles: Sequence[_EqualCandle],
    reasons: list[str],
    warnings: list[str],
) -> float:
    score = 0.0
    touch_count = len(group.swings)
    score += 1.0 if touch_count == 2 else 1.5 if touch_count == 3 else 2.0
    score += _zone_cleanliness_points(zone_width, width_limit, reasons, warnings)
    avg_strength = mean(swing.strength_score for swing in group.swings)
    score += 0.5 if avg_strength < 5 else 1.0 if avg_strength < 7 else 1.5
    score += _timeframe_points(_dominant_timeframe(group.swings))
    score += _status_points(status, reasons, warnings)
    score += _visibility_points(group, warnings)
    score += _context_points(group, candles, warnings)
    score += 0.5

    if touch_count < min_touches:
        score = min(score, 4.0)
    if zone_width > width_limit:
        score = min(score - 1.0, 5.0)
    if status.active_status in {EqualLiquidityStatus.SWEPT_REJECTED, EqualLiquidityStatus.SWEPT_RECLAIMED}:
        score = min(score, 8.0)
    if status.active_status in {EqualLiquidityStatus.BROKEN_ABOVE, EqualLiquidityStatus.BROKEN_BELOW}:
        score = min(score - 1.5, 3.0)
    if "noisy_equal_highs_lows_filtered" in warnings:
        score = min(score, 3.0)
    return round(max(0.0, min(10.0, score)), 2)


def _zone_cleanliness_points(zone_width: float, width_limit: float, reasons: list[str], warnings: list[str]) -> float:
    if width_limit <= 0:
        return 0.5
    ratio = zone_width / width_limit
    if ratio <= 0.30:
        reasons.append("zone_width_is_clean_and_narrow_relative_to_atr")
        return 1.5
    if ratio <= 0.60:
        return 1.0
    if ratio <= 1.0:
        warnings.append("equal_liquidity_zone_is_moderately_wide")
        return 0.5
    warnings.append("equal_liquidity_zone_too_wide_relative_to_atr")
    return 0.0


def _status_points(status: EqualLiquidityStatusDetails, reasons: list[str], warnings: list[str]) -> float:
    if status.active_status == EqualLiquidityStatus.ACTIVE:
        reasons.append("liquidity_is_active_and_unswept")
        return 1.0
    if status.active_status in {EqualLiquidityStatus.SWEPT_REJECTED, EqualLiquidityStatus.SWEPT_RECLAIMED}:
        warnings.append("liquidity_no_longer_active_as_untouched_pool")
        return 0.3
    warnings.append("liquidity_broken_or_accepted_beyond_zone")
    return 0.0


def _visibility_points(group: _CandidateGroup, warnings: list[str]) -> float:
    indexes = [swing.index for swing in group.swings]
    spacings = [b - a for a, b in zip(indexes, indexes[1:])]
    if spacings and min(spacings) <= 1:
        warnings.append("noisy_equal_highs_lows_filtered")
        return 0.0
    if len(group.swings) >= 3:
        return 1.0
    return 0.5


def _context_points(group: _CandidateGroup, candles: Sequence[_EqualCandle], warnings: list[str]) -> float:
    timeframe = _dominant_timeframe(group.swings).lower()
    if timeframe in {"4h", "h4", "1d", "d1", "daily", "weekly", "1w", "w1"}:
        return 1.0
    if candles and _is_choppy(candles):
        warnings.append("heavy_chop_reduces_equal_liquidity_quality")
        return 0.0
    return 0.5


def _base_reasons(
    liquidity_type: EqualLiquidityType,
    touch_count: int,
    status: EqualLiquidityStatusDetails,
    zone_width: float,
    width_limit: float,
) -> list[str]:
    side = "swing highs" if liquidity_type == EqualLiquidityType.EQUAL_HIGHS else "swing lows"
    reasons = [
        f"{touch_count} confirmed {side} formed within tolerance",
        "equal_highs_lows_create_visible_liquidity_pool",
    ]
    if status.swept:
        reasons.append("price_traded_beyond_equal_liquidity_zone")
    if width_limit > 0 and zone_width <= width_limit:
        reasons.append("zone_width_passed_atr_cleanliness_filter")
    return reasons


def _base_warnings(
    group: _CandidateGroup,
    status: EqualLiquidityStatusDetails,
    zone_width: float,
    width_limit: float,
    candles: Sequence[_EqualCandle],
) -> list[str]:
    warnings: list[str] = []
    if len(group.swings) > 5:
        warnings.append("many_touches_may_indicate_stale_or_choppy_liquidity")
    if width_limit > 0 and zone_width > width_limit:
        warnings.append("zone_width_exceeds_max_zone_width_atr")
    if status.active_status in {EqualLiquidityStatus.BROKEN_ABOVE, EqualLiquidityStatus.BROKEN_BELOW}:
        warnings.append("do_not_treat_as_unswept_liquidity_target")
    if _dominant_timeframe(group.swings).lower() in {"1m", "m1"} and _is_choppy(candles):
        warnings.append("low_timeframe_noise")
    return warnings


def _liquidity_context(liquidity_type: EqualLiquidityType, status: EqualLiquidityStatusDetails) -> dict[str, Any]:
    if liquidity_type == EqualLiquidityType.EQUAL_HIGHS:
        role = "buy_side_liquidity"
        target_use = "long_target_or_bearish_sweep_area"
        if status.active_status == EqualLiquidityStatus.SWEPT_REJECTED:
            target_use = "sweep_event_for_bearish_MSS_or_reversal_context"
    else:
        role = "sell_side_liquidity"
        target_use = "short_target_or_bullish_sweep_area"
        if status.active_status == EqualLiquidityStatus.SWEPT_RECLAIMED:
            target_use = "sweep_event_for_bullish_MSS_or_reversal_context"
    return {
        "liquidity_role": role,
        "target_use": target_use,
        "internal_or_external": "internal_or_external_to_be_classified_by_dealing_range",
    }


def _dedupe_overlapping(pools: Sequence[EqualLiquidityPool]) -> list[EqualLiquidityPool]:
    kept: list[EqualLiquidityPool] = []
    for pool in sorted(pools, key=lambda item: (-item.quality_score, -item.touch_count, item.creation_index)):
        if any(_overlaps(pool, existing) and pool.type == existing.type for existing in kept):
            continue
        kept.append(pool)
    return sorted(kept, key=lambda item: (-item.quality_score, item.creation_index))


def _overlaps(left: EqualLiquidityPool, right: EqualLiquidityPool) -> bool:
    overlap = max(left.zone_low, right.zone_low) <= min(left.zone_high, right.zone_high)
    shared_touch = bool(set(left.touched_indexes) & set(right.touched_indexes))
    return overlap or shared_touch


def _detect_basic_swings(
    candles: Sequence[_EqualCandle],
    atr_values: Sequence[float],
    left_bars: int,
    right_bars: int,
    timeframe: str | None,
) -> list[_EqualSwing]:
    swings: list[_EqualSwing] = []
    for position in range(left_bars, len(candles) - right_bars):
        candle = candles[position]
        left = candles[position - left_bars : position]
        right = candles[position + 1 : position + right_bars + 1]
        atr = max(atr_values[position], 1e-9)
        if all(candle.high_p >= other.high_p for other in left + right):
            reaction = max(candle.high_p - min(item.low_p for item in right), 0.0)
            swings.append(
                _EqualSwing(
                    candle.index,
                    candle.timestamp,
                    candle.high_p,
                    "swing_high",
                    _swing_strength(reaction / atr),
                    True,
                    timeframe or candle.timeframe,
                )
            )
        if all(candle.low_p <= other.low_p for other in left + right):
            reaction = max(max(item.high_p for item in right) - candle.low_p, 0.0)
            swings.append(
                _EqualSwing(
                    candle.index,
                    candle.timestamp,
                    candle.low_p,
                    "swing_low",
                    _swing_strength(reaction / atr),
                    True,
                    timeframe or candle.timeframe,
                )
            )
    return swings


def _normalize_candles(
    source: Sequence[CandleNode | Mapping[str, Any]] | Any,
    timeframe: str | None,
    symbol: str,
) -> list[_EqualCandle]:
    records = _records(source)
    normalized: list[_EqualCandle] = []
    for fallback_index, candle in enumerate(records):
        if isinstance(candle, CandleNode):
            normalized.append(
                _EqualCandle(
                    candle.sequence_id if candle.sequence_id else fallback_index,
                    candle.end_time,
                    float(candle.open_p),
                    float(candle.high_p),
                    float(candle.low_p),
                    float(candle.close_p),
                    float(candle.volume),
                    timeframe or candle.timeframe,
                    candle.symbol,
                    candle.is_closed,
                )
            )
            continue
        if not isinstance(candle, Mapping):
            continue
        normalized.append(
            _EqualCandle(
                int(_first_present(candle, "index", default=fallback_index)),
                _coerce_datetime(_first_present(candle, "timestamp", "time", "end_time")),
                float(_first_present(candle, "open", "open_p")),
                float(_first_present(candle, "high", "high_p")),
                float(_first_present(candle, "low", "low_p")),
                float(_first_present(candle, "close", "close_p")),
                float(_first_present(candle, "volume", default=0.0)),
                str(_first_present(candle, "timeframe", default=timeframe or "unknown")),
                str(_first_present(candle, "symbol", default=symbol)),
                bool(_first_present(candle, "is_closed", default=True)),
            )
        )
    return normalized


def _normalize_swing(source: Mapping[str, Any] | Any, timeframe: str | None) -> _EqualSwing:
    if hasattr(source, "as_dict"):
        source = source.as_dict()
    if not isinstance(source, Mapping):
        raise TypeError("swings must contain mappings or objects with as_dict().")
    return _EqualSwing(
        index=int(_first_present(source, "index")),
        timestamp=_coerce_datetime(_first_present(source, "timestamp", "time")),
        price=float(_first_present(source, "price")),
        type=str(_first_present(source, "type")),
        strength_score=float(_first_present(source, "strength_score", default=6.0)),
        confirmed_status=bool(_first_present(source, "confirmed_status", "confirmed", default=True)),
        timeframe=str(_first_present(source, "timeframe", default=timeframe or "unknown")),
    )


def _records(source: Any) -> list[Any]:
    if source is None:
        return []
    if hasattr(source, "to_dict"):
        records = source.to_dict("records")
        if isinstance(records, list):
            return records
    return list(source)


def _calculate_atr(candles: Sequence[_EqualCandle], period: int) -> list[float]:
    values: list[float] = []
    ranges: list[float] = []
    for position, candle in enumerate(candles):
        if position == 0:
            true_range = candle.range
        else:
            previous_close = candles[position - 1].close_p
            true_range = max(candle.range, abs(candle.high_p - previous_close), abs(candle.low_p - previous_close))
        ranges.append(true_range)
        window = ranges[max(0, position - max(1, period) + 1) : position + 1]
        values.append(sum(window) / len(window) if window else 0.0)
    return values


def _tolerance_for(
    price: float,
    candles: Sequence[_EqualCandle],
    atr_values: Sequence[float],
    index: int,
    tolerance_percent: float,
) -> tuple[float, float]:
    percent_tolerance = price * tolerance_percent / 100.0
    atr = _atr_near_index(candles, atr_values, index)
    atr_floor = atr * 0.05 if atr > 0 else 0.0
    atr_cap = atr * 0.25 if atr > 0 else percent_tolerance
    tolerance = max(percent_tolerance, atr_floor)
    tolerance = min(tolerance, atr_cap) if atr_cap > 0 else tolerance
    return max(tolerance, 1e-9), atr


def _atr_near_index(candles: Sequence[_EqualCandle], atr_values: Sequence[float], index: int) -> float:
    if not candles or not atr_values:
        return 0.0
    nearest_position = min(range(len(candles)), key=lambda pos: abs(candles[pos].index - index))
    return atr_values[nearest_position]


def _timeframe_points(timeframe: str) -> float:
    lowered = timeframe.lower()
    if lowered in {"1m", "m1", "3m", "m3", "5m", "m5"}:
        return 0.5
    if lowered in {"15m", "m15", "30m", "m30", "1h", "h1"}:
        return 1.0
    return 1.5


def _dominant_timeframe(swings: Sequence[_EqualSwing]) -> str:
    counts: dict[str, int] = {}
    for swing in swings:
        counts[swing.timeframe] = counts.get(swing.timeframe, 0) + 1
    return max(counts, key=counts.get) if counts else "unknown"


def _quality_grade(score: float, status: EqualLiquidityStatusDetails) -> EqualLiquidityQualityGrade:
    if status.active_status in {EqualLiquidityStatus.BROKEN_ABOVE, EqualLiquidityStatus.BROKEN_BELOW}:
        return EqualLiquidityQualityGrade.INACTIVE_BROKEN
    if status.active_status in {EqualLiquidityStatus.SWEPT_REJECTED, EqualLiquidityStatus.SWEPT_RECLAIMED}:
        return EqualLiquidityQualityGrade.STRONG_SWEEP_EVENT if score >= 6.5 else EqualLiquidityQualityGrade.USABLE
    if score < 2.5:
        return EqualLiquidityQualityGrade.INVALID
    if score < 5.0:
        return EqualLiquidityQualityGrade.WEAK
    if score < 7.0:
        return EqualLiquidityQualityGrade.USABLE
    if score < 9.0:
        return EqualLiquidityQualityGrade.STRONG
    return EqualLiquidityQualityGrade.HIGH_QUALITY


def _swing_strength(reaction_atr: float) -> float:
    if reaction_atr >= 2.0:
        return 8.0
    if reaction_atr >= 1.0:
        return 6.5
    if reaction_atr >= 0.5:
        return 5.0
    return 3.0


def _is_choppy(candles: Sequence[_EqualCandle]) -> bool:
    if len(candles) < 6:
        return False
    recent = candles[-8:]
    closes = [candle.close_p for candle in recent]
    highs = [candle.high_p for candle in recent]
    lows = [candle.low_p for candle in recent]
    total_range = max(highs) - min(lows)
    average_range = mean(candle.range for candle in recent)
    direction_changes = sum(
        1
        for left, right, third in zip(closes, closes[1:], closes[2:])
        if (right - left) * (third - right) < 0
    )
    return total_range <= average_range * 3.0 and direction_changes >= 3


def _first_present(source: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    if default is not None:
        return default
    raise KeyError(f"Missing required field from equal highs/lows input: {keys}")


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
