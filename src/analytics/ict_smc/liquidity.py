"""Rule-based ICT/SMC liquidity pool detection.

Liquidity is treated as a map concept: target, sweep area, or continuation
reference. It is not a standalone entry trigger.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from statistics import median
from typing import Any, Mapping, Sequence

from src.analytics.ict_smc.swing_points import (
    DetectedSwingPoint,
    SwingLiquidityType,
    SwingPointStatus,
    SwingPointType,
    SwingStrengthLabel,
)
from src.core.domain.market_data import CandleNode


class LiquidityDirection(str, Enum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


class LiquidityType(str, Enum):
    SWING_HIGH = "swing_high_liquidity"
    SWING_LOW = "swing_low_liquidity"
    EQUAL_HIGHS = "equal_highs"
    EQUAL_LOWS = "equal_lows"
    PREVIOUS_DAY_HIGH = "previous_day_high"
    PREVIOUS_DAY_LOW = "previous_day_low"
    SESSION_HIGH = "session_high"
    SESSION_LOW = "session_low"
    RANGE_HIGH = "range_high"
    RANGE_LOW = "range_low"
    TRENDLINE_HIGHS = "trendline_highs"
    TRENDLINE_LOWS = "trendline_lows"


class LiquidityStatus(str, Enum):
    UNSWEPT = "unswept"
    TOUCHED = "touched"
    SWEPT = "swept"
    BROKEN = "broken"
    STALE = "stale"
    INVALID = "invalid"


class LiquidityQualityGrade(str, Enum):
    VERY_WEAK = "very_weak"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True, slots=True)
class LiquidityDetectionConfig:
    minimum_liquidity_swing_strength: float = 3.0
    equal_level_atr_multiplier: float = 0.15
    zone_atr_multiplier: float = 0.12
    break_buffer_atr_multiplier: float = 0.05
    minimum_touch_count: int = 2
    minimum_touch_separation: int = 3
    stale_touch_count: int = 5
    atr_period: int = 14
    timeframe: str | None = None
    include_swing_liquidity: bool = True
    include_equal_levels: bool = True
    include_previous_day_levels: bool = True
    include_session_levels: bool = True
    include_range_levels: bool = True
    include_trendline_levels: bool = False
    range_window: int = 24
    range_min_touch_count: int = 2
    range_min_atr: float = 1.0

    def __post_init__(self) -> None:
        if self.minimum_liquidity_swing_strength < 0:
            raise ValueError("minimum_liquidity_swing_strength cannot be negative.")
        if self.equal_level_atr_multiplier < 0 or self.zone_atr_multiplier < 0:
            raise ValueError("ATR multipliers cannot be negative.")
        if self.minimum_touch_count < 2:
            raise ValueError("minimum_touch_count must be at least 2.")
        if self.minimum_touch_separation < 0:
            raise ValueError("minimum_touch_separation cannot be negative.")
        if self.atr_period < 1:
            raise ValueError("atr_period must be positive.")


@dataclass(frozen=True, slots=True)
class LiquidityPriceZone:
    zone_low: float
    zone_mid: float
    zone_high: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LiquidityTolerance:
    method: str
    atr_value: float
    multiplier: float
    tolerance_value: float


@dataclass(frozen=True, slots=True)
class LiquidityConfluence:
    has_confluence: bool
    confluence_sources: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class LiquiditySweepDetails:
    sweep_type: str = "none"
    sweep_candle_index: int | None = None
    sweep_candle_high: float | None = None
    sweep_candle_low: float | None = None
    sweep_candle_close: float | None = None
    closed_back_inside_zone: bool = False


@dataclass(frozen=True, slots=True)
class LiquidityPool:
    liquidity_id: str
    concept_name: str
    symbol: str
    liquidity_type: LiquidityType
    source: str
    direction: LiquidityDirection
    timeframe: str
    price_zone: LiquidityPriceZone
    touched_count: int
    member_indexes: tuple[int, ...]
    member_prices: tuple[float, ...]
    first_created_index: int
    last_touched_index: int | None
    swept_status: LiquidityStatus
    sweep_candle_index: int | None
    broken_candle_index: int | None
    quality_score: float
    quality_grade: LiquidityQualityGrade
    role: tuple[str, ...]
    confluence: LiquidityConfluence
    tolerance: LiquidityTolerance
    sweep_details: LiquiditySweepDetails
    reasons: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def zone_low(self) -> float:
        return self.price_zone.zone_low

    @property
    def zone_mid(self) -> float:
        return self.price_zone.zone_mid

    @property
    def zone_high(self) -> float:
        return self.price_zone.zone_high

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["liquidity_type"] = self.liquidity_type.value
        payload["direction"] = self.direction.value
        payload["swept_status"] = self.swept_status.value
        payload["quality_grade"] = self.quality_grade.value
        return payload


@dataclass(frozen=True, slots=True)
class _LiquidityCandle:
    index: int
    timestamp: datetime
    symbol: str
    timeframe: str
    open_p: float
    high_p: float
    low_p: float
    close_p: float
    volume: float
    is_closed: bool = True
    session_name: str | None = None

    @property
    def range(self) -> float:
        return max(0.0, self.high_p - self.low_p)

    @property
    def body(self) -> float:
        return abs(self.close_p - self.open_p)

    @property
    def day(self) -> date:
        return self.timestamp.date()


class ICTLiquidityDetector:
    """Detects major horizontal and optional diagonal liquidity pools."""

    def __init__(self, config: LiquidityDetectionConfig | None = None) -> None:
        self.config = config or LiquidityDetectionConfig()

    def detect(
        self,
        candles: Sequence[CandleNode | Mapping[str, Any]],
        swings: Sequence[DetectedSwingPoint | Mapping[str, Any]],
    ) -> tuple[LiquidityPool, ...]:
        closed = tuple(candle for candle in self._normalize_candles(candles) if candle.is_closed)
        normalized_swings = tuple(self._normalize_swings(swings))
        if not closed and not normalized_swings:
            return tuple()

        atr_values = _calculate_atr(closed, self.config.atr_period) if closed else []
        pools: list[LiquidityPool] = []
        if self.config.include_swing_liquidity:
            pools.extend(self._detect_swing_liquidity(closed, normalized_swings, atr_values))
        if self.config.include_equal_levels:
            pools.extend(self._detect_equal_levels(closed, normalized_swings, atr_values))
        if self.config.include_previous_day_levels and closed:
            pools.extend(self._detect_previous_day_levels(closed, atr_values))
        if self.config.include_session_levels and closed:
            pools.extend(self._detect_session_levels(closed, atr_values))
        if self.config.include_range_levels and closed:
            pools.extend(self._detect_range_levels(closed, atr_values))
        if self.config.include_trendline_levels:
            pools.extend(self._detect_trendline_levels(closed, normalized_swings, atr_values))

        confluence_map = self._find_confluence(pools)
        finalized = [self._finalize_pool(pool, closed, atr_values, confluence_map.get(pool.liquidity_id, ())) for pool in pools]
        return tuple(sorted(finalized, key=lambda pool: (-pool.quality_score, pool.first_created_index, pool.zone_mid)))

    def detect_equal_highs(
        self,
        candles: Sequence[CandleNode | Mapping[str, Any]],
        swings: Sequence[DetectedSwingPoint | Mapping[str, Any]],
    ) -> tuple[LiquidityPool, ...]:
        closed = tuple(candle for candle in self._normalize_candles(candles) if candle.is_closed)
        normalized_swings = tuple(self._normalize_swings(swings))
        atr_values = _calculate_atr(closed, self.config.atr_period) if closed else []
        pools = [pool for pool in self._detect_equal_levels(closed, normalized_swings, atr_values) if pool.liquidity_type == LiquidityType.EQUAL_HIGHS]
        confluence_map = self._find_confluence(pools)
        return tuple(self._finalize_pool(pool, closed, atr_values, confluence_map.get(pool.liquidity_id, ())) for pool in pools)

    def detect_equal_lows(
        self,
        candles: Sequence[CandleNode | Mapping[str, Any]],
        swings: Sequence[DetectedSwingPoint | Mapping[str, Any]],
    ) -> tuple[LiquidityPool, ...]:
        closed = tuple(candle for candle in self._normalize_candles(candles) if candle.is_closed)
        normalized_swings = tuple(self._normalize_swings(swings))
        atr_values = _calculate_atr(closed, self.config.atr_period) if closed else []
        pools = [pool for pool in self._detect_equal_levels(closed, normalized_swings, atr_values) if pool.liquidity_type == LiquidityType.EQUAL_LOWS]
        confluence_map = self._find_confluence(pools)
        return tuple(self._finalize_pool(pool, closed, atr_values, confluence_map.get(pool.liquidity_id, ())) for pool in pools)

    def _detect_swing_liquidity(
        self,
        candles: Sequence[_LiquidityCandle],
        swings: Sequence[DetectedSwingPoint],
        atr_values: Sequence[float],
    ) -> list[LiquidityPool]:
        pools: list[LiquidityPool] = []
        for swing in swings:
            if swing.strength_score < self.config.minimum_liquidity_swing_strength:
                continue
            direction = LiquidityDirection.BUY_SIDE if swing.type == SwingPointType.SWING_HIGH else LiquidityDirection.SELL_SIDE
            liquidity_type = LiquidityType.SWING_HIGH if swing.type == SwingPointType.SWING_HIGH else LiquidityType.SWING_LOW
            source = "confirmed_swing_high" if direction == LiquidityDirection.BUY_SIDE else "confirmed_swing_low"
            tolerance = self._tolerance_for_price(candles, atr_values, swing.price)
            pools.append(
                self._build_pool(
                    liquidity_type=liquidity_type,
                    source=source,
                    direction=direction,
                    symbol=_symbol(candles),
                    timeframe=swing.timeframe,
                    prices=(swing.price,),
                    indexes=(swing.index,),
                    first_created_index=swing.confirmation_index,
                    tolerance=tolerance,
                    reasons=("confirmed_swing_creates_liquidity",),
                )
            )
        return pools

    def _detect_equal_levels(
        self,
        candles: Sequence[_LiquidityCandle],
        swings: Sequence[DetectedSwingPoint],
        atr_values: Sequence[float],
    ) -> list[LiquidityPool]:
        pools: list[LiquidityPool] = []
        pools.extend(self._group_equal_swings(candles, swings, atr_values, SwingPointType.SWING_HIGH))
        pools.extend(self._group_equal_swings(candles, swings, atr_values, SwingPointType.SWING_LOW))
        return pools

    def _group_equal_swings(
        self,
        candles: Sequence[_LiquidityCandle],
        swings: Sequence[DetectedSwingPoint],
        atr_values: Sequence[float],
        swing_type: SwingPointType,
    ) -> list[LiquidityPool]:
        valid = sorted(
            [
                swing
                for swing in swings
                if swing.type == swing_type and swing.strength_score >= self.config.minimum_liquidity_swing_strength
            ],
            key=lambda swing: swing.index,
        )
        pools: list[LiquidityPool] = []
        used_groups: set[tuple[int, ...]] = set()
        for seed in valid:
            tolerance = self._tolerance_for_price(candles, atr_values, seed.price, equal=True)
            group = [seed]
            for candidate in valid:
                if candidate.index == seed.index:
                    continue
                if abs(candidate.price - seed.price) > tolerance.tolerance_value:
                    continue
                if all(abs(candidate.index - member.index) >= self.config.minimum_touch_separation for member in group):
                    group.append(candidate)
            if len(group) < self.config.minimum_touch_count:
                continue
            group = sorted(group, key=lambda swing: swing.index)
            group_key = tuple(swing.index for swing in group)
            if group_key in used_groups:
                continue
            used_groups.add(group_key)
            prices = tuple(swing.price for swing in group)
            indexes = tuple(swing.index for swing in group)
            direction = LiquidityDirection.BUY_SIDE if swing_type == SwingPointType.SWING_HIGH else LiquidityDirection.SELL_SIDE
            liquidity_type = LiquidityType.EQUAL_HIGHS if swing_type == SwingPointType.SWING_HIGH else LiquidityType.EQUAL_LOWS
            source = "confirmed_swing_highs" if direction == LiquidityDirection.BUY_SIDE else "confirmed_swing_lows"
            pools.append(
                self._build_pool(
                    liquidity_type=liquidity_type,
                    source=source,
                    direction=direction,
                    symbol=_symbol(candles),
                    timeframe=_dominant_timeframe(group),
                    prices=prices,
                    indexes=indexes,
                    first_created_index=max(swing.confirmation_index for swing in group),
                    tolerance=tolerance,
                    reasons=("confirmed_equal_highs_grouped" if direction == LiquidityDirection.BUY_SIDE else "confirmed_equal_lows_grouped",),
                )
            )
        return pools

    def _detect_previous_day_levels(
        self, candles: Sequence[_LiquidityCandle], atr_values: Sequence[float]
    ) -> list[LiquidityPool]:
        by_day: dict[date, list[_LiquidityCandle]] = {}
        for candle in candles:
            by_day.setdefault(candle.day, []).append(candle)
        days = sorted(by_day)
        pools: list[LiquidityPool] = []
        for current, previous in zip(days[1:], days[:-1]):
            previous_candles = by_day[previous]
            current_candles = by_day[current]
            pdh = max(c.high_p for c in previous_candles)
            pdl = min(c.low_p for c in previous_candles)
            first_created = current_candles[0].index
            pools.append(
                self._build_pool(
                    liquidity_type=LiquidityType.PREVIOUS_DAY_HIGH,
                    source="daily_session_level",
                    direction=LiquidityDirection.BUY_SIDE,
                    symbol=_symbol(candles),
                    timeframe=self.config.timeframe or current_candles[0].timeframe,
                    prices=(pdh,),
                    indexes=tuple(c.index for c in previous_candles if c.high_p == pdh),
                    first_created_index=first_created,
                    tolerance=self._tolerance_for_price(candles, atr_values, pdh),
                    reasons=("previous_day_high_is_high_visibility_liquidity",),
                )
            )
            pools.append(
                self._build_pool(
                    liquidity_type=LiquidityType.PREVIOUS_DAY_LOW,
                    source="daily_session_level",
                    direction=LiquidityDirection.SELL_SIDE,
                    symbol=_symbol(candles),
                    timeframe=self.config.timeframe or current_candles[0].timeframe,
                    prices=(pdl,),
                    indexes=tuple(c.index for c in previous_candles if c.low_p == pdl),
                    first_created_index=first_created,
                    tolerance=self._tolerance_for_price(candles, atr_values, pdl),
                    reasons=("previous_day_low_is_high_visibility_liquidity",),
                )
            )
        return pools

    def _detect_session_levels(
        self, candles: Sequence[_LiquidityCandle], atr_values: Sequence[float]
    ) -> list[LiquidityPool]:
        groups: dict[tuple[date, str], list[_LiquidityCandle]] = {}
        for candle in candles:
            if candle.session_name:
                groups.setdefault((candle.day, candle.session_name), []).append(candle)
        pools: list[LiquidityPool] = []
        for (_, session), group in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1])):
            if len(group) < 2:
                continue
            high = max(c.high_p for c in group)
            low = min(c.low_p for c in group)
            pools.append(
                self._build_pool(
                    liquidity_type=LiquidityType.SESSION_HIGH,
                    source=f"{session}_session_high",
                    direction=LiquidityDirection.BUY_SIDE,
                    symbol=_symbol(candles),
                    timeframe=self.config.timeframe or group[0].timeframe,
                    prices=(high,),
                    indexes=tuple(c.index for c in group if c.high_p == high),
                    first_created_index=group[-1].index,
                    tolerance=self._tolerance_for_price(candles, atr_values, high),
                    reasons=("session_high_creates_buy_side_liquidity",),
                )
            )
            pools.append(
                self._build_pool(
                    liquidity_type=LiquidityType.SESSION_LOW,
                    source=f"{session}_session_low",
                    direction=LiquidityDirection.SELL_SIDE,
                    symbol=_symbol(candles),
                    timeframe=self.config.timeframe or group[0].timeframe,
                    prices=(low,),
                    indexes=tuple(c.index for c in group if c.low_p == low),
                    first_created_index=group[-1].index,
                    tolerance=self._tolerance_for_price(candles, atr_values, low),
                    reasons=("session_low_creates_sell_side_liquidity",),
                )
            )
        return pools

    def _detect_range_levels(
        self, candles: Sequence[_LiquidityCandle], atr_values: Sequence[float]
    ) -> list[LiquidityPool]:
        if len(candles) < self.config.range_window:
            return []
        window = candles[-self.config.range_window :]
        atr = atr_values[-1] if atr_values else _average_range(candles)
        high = max(c.high_p for c in window)
        low = min(c.low_p for c in window)
        if high - low < atr * self.config.range_min_atr:
            return []
        high_touches = sum(1 for candle in window if abs(candle.high_p - high) <= atr * self.config.equal_level_atr_multiplier)
        low_touches = sum(1 for candle in window if abs(candle.low_p - low) <= atr * self.config.equal_level_atr_multiplier)
        if high_touches < self.config.range_min_touch_count or low_touches < self.config.range_min_touch_count:
            return []
        return [
            self._build_pool(
                LiquidityType.RANGE_HIGH,
                "consolidation_range_high",
                LiquidityDirection.BUY_SIDE,
                _symbol(candles),
                self.config.timeframe or window[-1].timeframe,
                (high,),
                tuple(c.index for c in window if abs(c.high_p - high) <= atr * self.config.equal_level_atr_multiplier),
                window[-1].index,
                self._tolerance_for_price(candles, atr_values, high),
                ("range_high_with_multiple_touches",),
            ),
            self._build_pool(
                LiquidityType.RANGE_LOW,
                "consolidation_range_low",
                LiquidityDirection.SELL_SIDE,
                _symbol(candles),
                self.config.timeframe or window[-1].timeframe,
                (low,),
                tuple(c.index for c in window if abs(c.low_p - low) <= atr * self.config.equal_level_atr_multiplier),
                window[-1].index,
                self._tolerance_for_price(candles, atr_values, low),
                ("range_low_with_multiple_touches",),
            ),
        ]

    def _detect_trendline_levels(
        self,
        candles: Sequence[_LiquidityCandle],
        swings: Sequence[DetectedSwingPoint],
        atr_values: Sequence[float],
    ) -> list[LiquidityPool]:
        pools: list[LiquidityPool] = []
        for swing_type, liquidity_type, direction in (
            (SwingPointType.SWING_HIGH, LiquidityType.TRENDLINE_HIGHS, LiquidityDirection.BUY_SIDE),
            (SwingPointType.SWING_LOW, LiquidityType.TRENDLINE_LOWS, LiquidityDirection.SELL_SIDE),
        ):
            points = [s for s in swings if s.type == swing_type and s.strength_score >= self.config.minimum_liquidity_swing_strength]
            if len(points) < 3:
                continue
            group = points[-3:]
            slope = (group[-1].price - group[0].price) / max(1, group[-1].index - group[0].index)
            tolerance = self._tolerance_for_price(candles, atr_values, group[-1].price, equal=True)
            if all(abs((group[0].price + slope * (s.index - group[0].index)) - s.price) <= tolerance.tolerance_value for s in group):
                pools.append(
                    self._build_pool(
                        liquidity_type,
                        "diagonal_swing_alignment",
                        direction,
                        _symbol(candles),
                        _dominant_timeframe(group),
                        tuple(s.price for s in group),
                        tuple(s.index for s in group),
                        max(s.confirmation_index for s in group),
                        tolerance,
                        ("trendline_liquidity_is_approximate",),
                        ("trendline_liquidity_lower_confidence_than_horizontal_liquidity",),
                    )
                )
        return pools

    def _build_pool(
        self,
        liquidity_type: LiquidityType,
        source: str,
        direction: LiquidityDirection,
        symbol: str,
        timeframe: str,
        prices: Sequence[float],
        indexes: Sequence[int],
        first_created_index: int,
        tolerance: LiquidityTolerance,
        reasons: Sequence[str] = (),
        warnings: Sequence[str] = (),
    ) -> LiquidityPool:
        zone_low = min(prices) - tolerance.tolerance_value
        zone_high = max(prices) + tolerance.tolerance_value
        zone_mid = median(prices)
        liquidity_id = f"LQ_{timeframe}_{liquidity_type.value}_{first_created_index}_{round(zone_mid, 5)}"
        return LiquidityPool(
            liquidity_id=liquidity_id,
            concept_name="Liquidity",
            symbol=symbol,
            liquidity_type=liquidity_type,
            source=source,
            direction=direction,
            timeframe=timeframe,
            price_zone=LiquidityPriceZone(round(zone_low, 6), round(zone_mid, 6), round(zone_high, 6)),
            touched_count=len(indexes) if indexes else 1,
            member_indexes=tuple(indexes),
            member_prices=tuple(round(price, 6) for price in prices),
            first_created_index=first_created_index,
            last_touched_index=None,
            swept_status=LiquidityStatus.UNSWEPT,
            sweep_candle_index=None,
            broken_candle_index=None,
            quality_score=0.0,
            quality_grade=LiquidityQualityGrade.WEAK,
            role=_roles(direction, LiquidityStatus.UNSWEPT),
            confluence=LiquidityConfluence(False),
            tolerance=tolerance,
            sweep_details=LiquiditySweepDetails(),
            reasons=tuple(reasons),
            warnings=tuple(warnings) + ("liquidity_alone_is_not_entry_signal",),
        )

    def _finalize_pool(
        self,
        pool: LiquidityPool,
        candles: Sequence[_LiquidityCandle],
        atr_values: Sequence[float],
        confluence_sources: Sequence[str],
    ) -> LiquidityPool:
        status, last_touch, sweep_details, broken_index = self._status_for_pool(pool, candles, atr_values)
        confluence = LiquidityConfluence(bool(confluence_sources), tuple(sorted(set(confluence_sources))))
        score, grade, reasons, warnings = self._score_pool(pool, status, confluence, candles)
        return LiquidityPool(
            liquidity_id=pool.liquidity_id,
            concept_name=pool.concept_name,
            symbol=pool.symbol,
            liquidity_type=pool.liquidity_type,
            source=pool.source,
            direction=pool.direction,
            timeframe=pool.timeframe,
            price_zone=pool.price_zone,
            touched_count=pool.touched_count,
            member_indexes=pool.member_indexes,
            member_prices=pool.member_prices,
            first_created_index=pool.first_created_index,
            last_touched_index=last_touch,
            swept_status=status,
            sweep_candle_index=sweep_details.sweep_candle_index,
            broken_candle_index=broken_index,
            quality_score=score,
            quality_grade=grade,
            role=_roles(pool.direction, status),
            confluence=confluence,
            tolerance=pool.tolerance,
            sweep_details=sweep_details,
            reasons=tuple(dict.fromkeys(pool.reasons + tuple(reasons))),
            warnings=tuple(dict.fromkeys(pool.warnings + tuple(warnings))),
        )

    def _status_for_pool(
        self, pool: LiquidityPool, candles: Sequence[_LiquidityCandle], atr_values: Sequence[float]
    ) -> tuple[LiquidityStatus, int | None, LiquiditySweepDetails, int | None]:
        status = LiquidityStatus.UNSWEPT
        last_touch: int | None = None
        sweep_details = LiquiditySweepDetails()
        broken_index: int | None = None
        break_buffer = (atr_values[-1] if atr_values else _average_range(candles)) * self.config.break_buffer_atr_multiplier
        for candle in candles:
            if candle.index < pool.first_created_index:
                continue
            if pool.direction == LiquidityDirection.BUY_SIDE:
                if candle.close_p > pool.zone_high + break_buffer:
                    return LiquidityStatus.BROKEN, candle.index, sweep_details, candle.index
                if candle.high_p > pool.zone_high and candle.close_p < pool.zone_high:
                    status = LiquidityStatus.SWEPT
                    last_touch = candle.index
                    sweep_details = LiquiditySweepDetails(
                        "buy_side_sweep", candle.index, candle.high_p, candle.low_p, candle.close_p, True
                    )
                    continue
                if candle.high_p >= pool.zone_low:
                    status = LiquidityStatus.TOUCHED if status == LiquidityStatus.UNSWEPT else status
                    last_touch = candle.index
            else:
                if candle.close_p < pool.zone_low - break_buffer:
                    return LiquidityStatus.BROKEN, candle.index, sweep_details, candle.index
                if candle.low_p < pool.zone_low and candle.close_p > pool.zone_low:
                    status = LiquidityStatus.SWEPT
                    last_touch = candle.index
                    sweep_details = LiquiditySweepDetails(
                        "sell_side_sweep", candle.index, candle.high_p, candle.low_p, candle.close_p, True
                    )
                    continue
                if candle.low_p <= pool.zone_high:
                    status = LiquidityStatus.TOUCHED if status == LiquidityStatus.UNSWEPT else status
                    last_touch = candle.index
        if pool.touched_count >= self.config.stale_touch_count and status == LiquidityStatus.TOUCHED:
            status = LiquidityStatus.STALE
        return status, last_touch, sweep_details, broken_index

    def _score_pool(
        self,
        pool: LiquidityPool,
        status: LiquidityStatus,
        confluence: LiquidityConfluence,
        candles: Sequence[_LiquidityCandle],
    ) -> tuple[float, LiquidityQualityGrade, list[str], list[str]]:
        reasons: list[str] = []
        warnings: list[str] = []
        score = _source_weight(pool.liquidity_type)
        score += _timeframe_score(pool.timeframe)
        score += min(1.5, 0.5 + 0.5 * max(0, pool.touched_count - 1))
        if status == LiquidityStatus.UNSWEPT:
            score += 1.5
            reasons.append("liquidity_pool_is_unswept")
        elif status == LiquidityStatus.TOUCHED:
            score += 0.75
            warnings.append("liquidity_pool_already_touched")
        elif status == LiquidityStatus.SWEPT:
            warnings.append("liquidity_pool_already_swept")
        elif status == LiquidityStatus.BROKEN:
            warnings.append("liquidity_pool_broken_not_fresh")

        zone_width = pool.zone_high - pool.zone_low
        avg_range = max(_average_range(candles), 1e-9) if candles else max(zone_width, 1e-9)
        if zone_width <= avg_range * 0.35:
            score += 1.0
            reasons.append("liquidity_zone_is_clean")
        elif zone_width <= avg_range * 0.75:
            score += 0.5
        else:
            warnings.append("liquidity_zone_is_wide")
            score -= 0.5

        if confluence.has_confluence:
            score += min(1.0, 0.5 * len(confluence.confluence_sources))
            reasons.append("liquidity_has_confluence")
        else:
            warnings.append("weak_confluence")

        if candles:
            distance = abs(pool.zone_mid - candles[-1].close_p)
            if distance >= avg_range * 0.5:
                score += 0.75
                reasons.append("liquidity_has_useful_target_distance")
            else:
                warnings.append("liquidity_too_close_to_current_price")

        if pool.timeframe.lower() in {"1m", "m1"} and not confluence.has_confluence:
            score -= 1.0
            warnings.append("low_timeframe_noise")
        if _is_choppy(candles):
            score -= 0.75
            warnings.append("choppy_market")
        score = round(_clamp(score, 0.0, 10.0), 2)
        return score, _grade(score), reasons, warnings

    def _find_confluence(self, pools: Sequence[LiquidityPool]) -> dict[str, tuple[str, ...]]:
        confluence: dict[str, list[str]] = {pool.liquidity_id: [] for pool in pools}
        for left in pools:
            for right in pools:
                if left.liquidity_id == right.liquidity_id or left.direction != right.direction:
                    continue
                if max(left.zone_low, right.zone_low) <= min(left.zone_high, right.zone_high):
                    confluence[left.liquidity_id].append(right.liquidity_type.value)
        return {key: tuple(value) for key, value in confluence.items()}

    def _tolerance_for_price(
        self,
        candles: Sequence[_LiquidityCandle],
        atr_values: Sequence[float],
        price: float,
        equal: bool = False,
    ) -> LiquidityTolerance:
        atr = atr_values[-1] if atr_values else max(price * 0.0005, 1e-9)
        multiplier = self.config.equal_level_atr_multiplier if equal else self.config.zone_atr_multiplier
        tolerance = max(atr * multiplier, price * 0.00002)
        return LiquidityTolerance("ATR", round(atr, 6), multiplier, round(tolerance, 6))

    def _normalize_candles(self, candles: Sequence[CandleNode | Mapping[str, Any]]) -> list[_LiquidityCandle]:
        normalized: list[_LiquidityCandle] = []
        for fallback_index, candle in enumerate(candles):
            if isinstance(candle, CandleNode):
                normalized.append(
                    _LiquidityCandle(
                        index=candle.sequence_id if candle.sequence_id else fallback_index,
                        timestamp=candle.end_time,
                        symbol=candle.symbol,
                        timeframe=self.config.timeframe or candle.timeframe,
                        open_p=float(candle.open_p),
                        high_p=float(candle.high_p),
                        low_p=float(candle.low_p),
                        close_p=float(candle.close_p),
                        volume=float(candle.volume),
                        is_closed=candle.is_closed,
                    )
                )
                continue
            normalized.append(
                _LiquidityCandle(
                    index=int(_first_present(candle, "index", default=fallback_index)),
                    timestamp=_coerce_datetime(_first_present(candle, "timestamp", "time", "end_time")),
                    symbol=str(_first_present(candle, "symbol", default="unknown")),
                    timeframe=str(_first_present(candle, "timeframe", default=self.config.timeframe or "unknown")),
                    open_p=float(_first_present(candle, "open", "open_p")),
                    high_p=float(_first_present(candle, "high", "high_p")),
                    low_p=float(_first_present(candle, "low", "low_p")),
                    close_p=float(_first_present(candle, "close", "close_p")),
                    volume=float(_first_present(candle, "volume", default=0.0)),
                    is_closed=bool(_first_present(candle, "is_closed", default=True)),
                    session_name=_optional_string(candle.get("session_name")),
                )
            )
        return normalized

    def _normalize_swings(self, swings: Sequence[DetectedSwingPoint | Mapping[str, Any]]) -> list[DetectedSwingPoint]:
        normalized: list[DetectedSwingPoint] = []
        for swing in swings:
            if isinstance(swing, DetectedSwingPoint):
                normalized.append(swing)
            else:
                normalized.append(_swing_from_mapping(swing))
        return normalized


def detect_equal_highs(
    df: Sequence[CandleNode | Mapping[str, Any]],
    swings: Sequence[DetectedSwingPoint | Mapping[str, Any]],
    **config_overrides: Any,
) -> list[dict[str, Any]]:
    config = LiquidityDetectionConfig(**config_overrides) if config_overrides else LiquidityDetectionConfig()
    return [pool.as_dict() for pool in ICTLiquidityDetector(config).detect_equal_highs(df, swings)]


def detect_equal_lows(
    df: Sequence[CandleNode | Mapping[str, Any]],
    swings: Sequence[DetectedSwingPoint | Mapping[str, Any]],
    **config_overrides: Any,
) -> list[dict[str, Any]]:
    config = LiquidityDetectionConfig(**config_overrides) if config_overrides else LiquidityDetectionConfig()
    return [pool.as_dict() for pool in ICTLiquidityDetector(config).detect_equal_lows(df, swings)]


def detect_liquidity_pools(
    df: Sequence[CandleNode | Mapping[str, Any]],
    swings: Sequence[DetectedSwingPoint | Mapping[str, Any]],
    **config_overrides: Any,
) -> list[dict[str, Any]]:
    config = LiquidityDetectionConfig(**config_overrides) if config_overrides else LiquidityDetectionConfig()
    return [pool.as_dict() for pool in ICTLiquidityDetector(config).detect(df, swings)]


def _calculate_atr(candles: Sequence[_LiquidityCandle], period: int) -> list[float]:
    atr_values: list[float] = []
    true_ranges: list[float] = []
    for position, candle in enumerate(candles):
        if position == 0:
            true_range = candle.range
        else:
            previous_close = candles[position - 1].close_p
            true_range = max(
                candle.high_p - candle.low_p,
                abs(candle.high_p - previous_close),
                abs(candle.low_p - previous_close),
            )
        true_ranges.append(true_range)
        window = true_ranges[max(0, position - period + 1) : position + 1]
        atr_values.append(sum(window) / len(window) if window else 0.0)
    return atr_values


def _swing_from_mapping(swing: Mapping[str, Any]) -> DetectedSwingPoint:
    swing_type = SwingPointType(str(_first_present(swing, "type")))
    liquidity_default = "buy_side_liquidity" if swing_type == SwingPointType.SWING_HIGH else "sell_side_liquidity"
    return DetectedSwingPoint(
        index=int(_first_present(swing, "index")),
        timestamp=_coerce_datetime(_first_present(swing, "timestamp")),
        confirmation_index=int(_first_present(swing, "confirmation_index")),
        confirmation_timestamp=_coerce_datetime(_first_present(swing, "confirmation_timestamp")),
        price=float(_first_present(swing, "price")),
        type=swing_type,
        strength_score=float(_first_present(swing, "strength_score", default=0.0)),
        strength_label=SwingStrengthLabel(str(_first_present(swing, "strength_label", default="weak"))),
        timeframe=str(_first_present(swing, "timeframe", default="unknown")),
        timeframe_weight=float(_first_present(swing, "timeframe_weight", default=1.0)),
        liquidity_type=SwingLiquidityType(str(_first_present(swing, "liquidity_type", default=liquidity_default))),
        status=SwingPointStatus(str(_first_present(swing, "status", default=SwingPointStatus.UNSWEPT.value))),
        used_for=tuple(_first_present(swing, "used_for", default=())),
        atr_reaction=float(_first_present(swing, "atr_reaction", default=0.0)),
        distance_from_previous_swing=_optional_float(swing.get("distance_from_previous_swing")),
        reasons=tuple(_first_present(swing, "reasons", default=())),
        warnings=tuple(_first_present(swing, "warnings", default=())),
    )


def _roles(direction: LiquidityDirection, status: LiquidityStatus) -> tuple[str, ...]:
    if direction == LiquidityDirection.BUY_SIDE:
        if status == LiquidityStatus.SWEPT:
            return ("buy_side_sweep_area", "possible_bearish_reversal_context")
        if status == LiquidityStatus.BROKEN:
            return ("bullish_continuation_or_bos_context",)
        return ("bullish_target", "buy_side_sweep_area")
    if status == LiquidityStatus.SWEPT:
        return ("sell_side_sweep_area", "possible_bullish_reversal_context")
    if status == LiquidityStatus.BROKEN:
        return ("bearish_continuation_or_bos_context",)
    return ("bearish_target", "sell_side_sweep_area")


def _source_weight(liquidity_type: LiquidityType) -> float:
    if liquidity_type in {LiquidityType.PREVIOUS_DAY_HIGH, LiquidityType.PREVIOUS_DAY_LOW}:
        return 2.0
    if liquidity_type in {LiquidityType.EQUAL_HIGHS, LiquidityType.EQUAL_LOWS, LiquidityType.RANGE_HIGH, LiquidityType.RANGE_LOW}:
        return 1.5
    if liquidity_type in {LiquidityType.SESSION_HIGH, LiquidityType.SESSION_LOW}:
        return 1.25
    if liquidity_type in {LiquidityType.SWING_HIGH, LiquidityType.SWING_LOW}:
        return 1.0
    return 0.75


def _timeframe_score(timeframe: str) -> float:
    value = timeframe.lower()
    if value in {"1m", "m1"}:
        return 0.25
    if value in {"5m", "m5"}:
        return 0.5
    if value in {"15m", "m15"}:
        return 0.75
    if value in {"1h", "h1", "60m"}:
        return 1.0
    if value in {"4h", "h4", "240m"}:
        return 1.25
    if value in {"1d", "d1", "daily", "1w", "w1", "weekly"}:
        return 1.5
    return 0.5


def _grade(score: float) -> LiquidityQualityGrade:
    if score >= 9.0:
        return LiquidityQualityGrade.HIGH_QUALITY
    if score >= 7.0:
        return LiquidityQualityGrade.STRONG
    if score >= 5.0:
        return LiquidityQualityGrade.MODERATE
    if score >= 3.0:
        return LiquidityQualityGrade.WEAK
    return LiquidityQualityGrade.VERY_WEAK


def _is_choppy(candles: Sequence[_LiquidityCandle]) -> bool:
    if len(candles) < 8:
        return False
    window = candles[-8:]
    total_range = max(c.high_p for c in window) - min(c.low_p for c in window)
    body_sum = sum(c.body for c in window)
    if total_range <= 0:
        return True
    return body_sum / total_range < 0.65


def _average_range(candles: Sequence[_LiquidityCandle]) -> float:
    if not candles:
        return 0.0
    return sum(c.range for c in candles) / len(candles)


def _dominant_timeframe(swings: Sequence[DetectedSwingPoint]) -> str:
    if not swings:
        return "unknown"
    return max(swings, key=lambda swing: swing.timeframe_weight).timeframe


def _symbol(candles: Sequence[_LiquidityCandle]) -> str:
    return candles[-1].symbol if candles else "unknown"


def _first_present(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    if default is not None:
        return default
    raise KeyError(f"Missing required key. Tried: {', '.join(keys)}")


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"Cannot coerce {value!r} to datetime.")


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
