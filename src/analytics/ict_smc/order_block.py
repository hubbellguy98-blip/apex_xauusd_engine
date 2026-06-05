"""Rule-based ICT/SMC order block detection.

Order blocks are reaction zones, not entries. This module detects the last
opposite candle before displacement that caused BOS/MSS, then tracks zone
definitions, mitigation, failure, breaker potential, and quality.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from src.core.domain.market_data import CandleNode


class OrderBlockDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class OrderBlockFreshStatus(str, Enum):
    FRESH = "fresh"
    TOUCHED = "touched"
    PARTIALLY_MITIGATED = "partially_mitigated"
    FULLY_MITIGATED_BUT_NOT_FAILED = "fully_mitigated_but_not_failed"
    FAILED = "failed"
    STALE = "stale"


class OrderBlockQualityGrade(str, Enum):
    INVALIDATED = "invalidated"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


class OrderBlockZoneMode(str, Enum):
    FULL_RANGE = "full_range"
    BODY_RANGE = "body_range"
    REFINED_RANGE = "refined_range"


class OrderBlockReactionStatus(str, Enum):
    WAITING_FOR_RETEST = "waiting_for_retest"
    REACTING = "reacting"
    NOT_TRADEABLE = "not_tradeable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class OrderBlockDetectionConfig:
    max_ob_lookback: int = 12
    max_structure_distance: int = 20
    atr_period: int = 14
    displacement_body_ratio: float = 0.55
    displacement_range_atr: float = 1.0
    close_near_extreme_ratio: float = 0.30
    selected_zone_mode: OrderBlockZoneMode = OrderBlockZoneMode.FULL_RANGE
    include_weak_candidates: bool = True
    minimum_quality_to_return: float = 0.0

    def __post_init__(self) -> None:
        if self.max_ob_lookback < 1:
            raise ValueError("max_ob_lookback must be positive.")
        if self.max_structure_distance < 1:
            raise ValueError("max_structure_distance must be positive.")
        if self.atr_period < 1:
            raise ValueError("atr_period must be positive.")


@dataclass(frozen=True, slots=True)
class OrderBlockPriceZone:
    zone_high: float
    zone_low: float
    zone_mid: float


@dataclass(frozen=True, slots=True)
class OrderBlockAlternativeZones:
    full_range: OrderBlockPriceZone
    body_range: OrderBlockPriceZone
    refined_range: OrderBlockPriceZone


@dataclass(frozen=True, slots=True)
class OrderBlockCandle:
    index: int
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    candle_type: str


@dataclass(frozen=True, slots=True)
class OrderBlockStructureReference:
    event_type: str
    direction: str
    confirmation_candle_index: int | None
    broken_level: float | None
    broken_swing_index: int | None
    quality_score: float | None


@dataclass(frozen=True, slots=True)
class OrderBlockDisplacement:
    displacement_required: bool
    displacement_present: bool
    displacement_strength: str
    displacement_start_index: int | None
    confirmation_index: int | None
    body_to_range_ratio: float
    range_to_atr_ratio: float
    close_position: str


@dataclass(frozen=True, slots=True)
class OrderBlockLiquidityContext:
    liquidity_sweep_before_displacement: bool
    sweep_type: str
    sweep_candle_index: int | None
    swept_liquidity_id: str | None
    sweep_quality_score: float | None


@dataclass(frozen=True, slots=True)
class OrderBlockFVGContext:
    fvg_created_after_displacement: bool
    fvg_direction: str
    fvg_zone_low: float | None
    fvg_zone_high: float | None
    ob_fvg_overlap: bool


@dataclass(frozen=True, slots=True)
class OrderBlockPremiumDiscountContext:
    poi_location: str
    premium_discount_alignment: bool
    dealing_range_low: float | None = None
    dealing_range_high: float | None = None
    equilibrium: float | None = None


@dataclass(frozen=True, slots=True)
class OrderBlockFreshness:
    fresh_status: OrderBlockFreshStatus
    mitigation_depth: str
    mean_threshold_touched: bool
    times_tapped: int


@dataclass(frozen=True, slots=True)
class OrderBlockFailureStatus:
    failed_order_block: bool
    failed_candle_index: int | None
    failed_timestamp: datetime | None
    failure_close: float | None
    failure_condition: str | None
    possible_breaker_created: bool
    possible_new_poi_type: str | None


@dataclass(frozen=True, slots=True)
class OrderBlockEntryLogic:
    entry_allowed_from_ob_alone: bool = False
    recommended_entry_style: str = "wait_for_price_retest_and_lower_timeframe_confirmation"
    invalidation_level: float = 0.0
    stop_loss_reference: str = "outside_order_block_or_sweep_extreme"
    target_reference: str = "nearest_opposite_side_liquidity"


@dataclass(frozen=True, slots=True)
class OrderBlock:
    concept_name: str
    symbol: str
    timeframe: str
    ob_id: str
    poi_type: str
    direction: OrderBlockDirection
    ob_candle: OrderBlockCandle
    zone_definition: OrderBlockAlternativeZones
    selected_zone_mode: OrderBlockZoneMode
    zone_high: float
    zone_low: float
    zone_mid: float
    mean_threshold: float
    created_by_event: str
    structure_event_reference: OrderBlockStructureReference
    displacement: OrderBlockDisplacement
    liquidity_context: OrderBlockLiquidityContext
    fvg_context: OrderBlockFVGContext
    premium_discount_context: OrderBlockPremiumDiscountContext
    freshness: OrderBlockFreshness
    failure_status: OrderBlockFailureStatus
    fresh_status: OrderBlockFreshStatus
    mitigation_depth: str
    failed_order_block: bool
    possible_breaker_created: bool
    reaction_status: OrderBlockReactionStatus
    invalidation_level: float
    entry_logic: OrderBlockEntryLogic
    quality_score: float
    quality_grade: OrderBlockQualityGrade
    reasons: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["direction"] = self.direction.value
        payload["selected_zone_mode"] = self.selected_zone_mode.value
        payload["fresh_status"] = self.fresh_status.value
        payload["freshness"]["fresh_status"] = self.freshness.fresh_status.value
        payload["reaction_status"] = self.reaction_status.value
        payload["quality_grade"] = self.quality_grade.value
        payload["entry_allowed_from_ob_alone"] = False
        return payload


@dataclass(frozen=True, slots=True)
class _Candle:
    position: int
    index: int
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def range(self) -> float:
        return max(self.high - self.low, 1e-9)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def body_ratio(self) -> float:
        return self.body / self.range

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open


@dataclass(frozen=True, slots=True)
class _StructureEvent:
    event_type: str
    direction: OrderBlockDirection
    confirmation_index: int
    broken_level: float | None
    broken_swing_index: int | None
    displacement_strength: str
    fvg_created: bool
    quality_score: float | None
    raw: Mapping[str, Any] | str


class ICTOrderBlockDetector:
    """Detects institutional-style order block reaction zones."""

    def __init__(self, config: OrderBlockDetectionConfig | None = None) -> None:
        self.config = config or OrderBlockDetectionConfig()

    def detect(
        self,
        candles: Sequence[CandleNode | Mapping[str, Any]],
        swings: Sequence[Mapping[str, Any]] | None = None,
        bos_events: Sequence[Mapping[str, Any] | str] | None = None,
        mss_events: Sequence[Mapping[str, Any] | str] | None = None,
        liquidity_sweeps: Sequence[Mapping[str, Any] | str] | None = None,
        *,
        symbol: str = "unknown",
        timeframe: str = "unknown",
        premium_discount_context: Mapping[str, Any] | None = None,
        htf_context: Mapping[str, Any] | None = None,
    ) -> tuple[OrderBlock, ...]:
        del swings  # confirmed swings are consumed upstream by BOS/MSS detectors.
        normalized = _normalize_candles(candles)
        if len(normalized) < 2:
            return ()

        atr_values = _atr_values(normalized, self.config.atr_period)
        index_to_position = {candle.index: candle.position for candle in normalized}
        events = _normalize_structure_events(bos_events or (), "BOS") + _normalize_structure_events(mss_events or (), "MSS")
        blocks: list[OrderBlock] = []

        for event in events:
            confirmation_position = index_to_position.get(event.confirmation_index)
            if confirmation_position is None:
                continue
            displacement_position = self._find_displacement_start(normalized, atr_values, event, confirmation_position)
            ob_candle = self._find_last_opposite_candle(normalized, event.direction, displacement_position)
            if ob_candle is None:
                continue
            distance = event.confirmation_index - ob_candle.index
            if distance < 1 or distance > self.config.max_structure_distance:
                continue
            blocks.append(
                self._build_order_block(
                    normalized,
                    atr_values,
                    event,
                    ob_candle,
                    displacement_position,
                    confirmation_position,
                    liquidity_sweeps or (),
                    symbol,
                    timeframe,
                    premium_discount_context or {},
                    htf_context or {},
                )
            )

        if self.config.include_weak_candidates and not blocks:
            blocks.extend(self._weak_candidates(normalized, atr_values, symbol, timeframe, premium_discount_context or {}, htf_context or {}))

        unique = _dedupe_order_blocks(blocks)
        return tuple(
            block
            for block in sorted(unique, key=lambda item: (item.ob_candle.index, item.quality_score), reverse=True)
            if block.quality_score >= self.config.minimum_quality_to_return
        )

    def _find_displacement_start(
        self,
        candles: Sequence[_Candle],
        atr_values: Sequence[float],
        event: _StructureEvent,
        confirmation_position: int,
    ) -> int:
        start = max(0, confirmation_position - self.config.max_ob_lookback)
        candidates = [
            position
            for position in range(start, confirmation_position + 1)
            if self._is_directional_displacement(candles[position], atr_values[position], event.direction)
        ]
        return candidates[0] if candidates else confirmation_position

    def _find_last_opposite_candle(
        self,
        candles: Sequence[_Candle],
        direction: OrderBlockDirection,
        displacement_position: int,
    ) -> _Candle | None:
        start = max(0, displacement_position - self.config.max_ob_lookback)
        for position in range(displacement_position - 1, start - 1, -1):
            candle = candles[position]
            if direction is OrderBlockDirection.BULLISH and candle.is_bearish:
                return candle
            if direction is OrderBlockDirection.BEARISH and candle.is_bullish:
                return candle
        return None

    def _build_order_block(
        self,
        candles: Sequence[_Candle],
        atr_values: Sequence[float],
        event: _StructureEvent,
        ob_candle: _Candle,
        displacement_position: int,
        confirmation_position: int,
        liquidity_sweeps: Sequence[Mapping[str, Any] | str],
        symbol: str,
        timeframe: str,
        premium_discount_context: Mapping[str, Any],
        htf_context: Mapping[str, Any],
    ) -> OrderBlock:
        zones = _zone_definitions(ob_candle, event.direction)
        selected_zone = _select_zone(zones, self.config.selected_zone_mode)
        mean_threshold = selected_zone.zone_mid
        freshness, failure = _mitigation_and_failure(candles, confirmation_position, selected_zone, mean_threshold, event.direction)
        fvg = _fvg_context(candles, ob_candle.position, confirmation_position, event.direction, selected_zone)
        liquidity = _liquidity_context(event.direction, liquidity_sweeps, ob_candle.index, event.confirmation_index)
        displacement_candle = candles[displacement_position]
        atr = atr_values[displacement_position]
        displacement = _displacement_snapshot(
            displacement_candle,
            atr,
            event,
            displacement_position,
            confirmation_position,
            self._is_directional_displacement(displacement_candle, atr, event.direction),
        )
        premium_discount = _premium_discount(event.direction, selected_zone, premium_discount_context, htf_context)
        structure_reference = OrderBlockStructureReference(
            event_type=event.event_type,
            direction=event.direction.value,
            confirmation_candle_index=event.confirmation_index,
            broken_level=event.broken_level,
            broken_swing_index=event.broken_swing_index,
            quality_score=event.quality_score,
        )
        created_by_event = _created_by_event(event, liquidity)
        reasons: list[str] = [
            f"clear_last_{'bearish' if event.direction is OrderBlockDirection.BULLISH else 'bullish'}_candle_before_{event.direction.value}_displacement",
            f"{event.direction.value}_displacement_caused_{event.event_type}",
        ]
        warnings: list[str] = [
            "order_block_is_reaction_zone_not_automatic_entry",
            "wait_for_lower_timeframe_confirmation_on_retest",
        ]
        score, score_reasons, score_warnings = _quality_score(
            event,
            displacement,
            liquidity,
            fvg,
            premium_discount,
            freshness,
            failure,
            selected_zone,
            atr,
        )
        reasons.extend(score_reasons)
        warnings.extend(score_warnings)
        invalidation_level = selected_zone.zone_low if event.direction is OrderBlockDirection.BULLISH else selected_zone.zone_high
        reaction_status = _reaction_status(freshness, failure)
        ob_id = f"OB_{timeframe}_{event.direction.value.upper()}_{ob_candle.index}"
        return OrderBlock(
            concept_name="Order Block",
            symbol=symbol,
            timeframe=timeframe,
            ob_id=ob_id,
            poi_type="order_block",
            direction=event.direction,
            ob_candle=OrderBlockCandle(
                index=ob_candle.index,
                timestamp=ob_candle.timestamp,
                open=ob_candle.open,
                high=ob_candle.high,
                low=ob_candle.low,
                close=ob_candle.close,
                candle_type="bullish" if ob_candle.is_bullish else "bearish",
            ),
            zone_definition=zones,
            selected_zone_mode=self.config.selected_zone_mode,
            zone_high=selected_zone.zone_high,
            zone_low=selected_zone.zone_low,
            zone_mid=selected_zone.zone_mid,
            mean_threshold=mean_threshold,
            created_by_event=created_by_event,
            structure_event_reference=structure_reference,
            displacement=displacement,
            liquidity_context=liquidity,
            fvg_context=fvg,
            premium_discount_context=premium_discount,
            freshness=freshness,
            failure_status=failure,
            fresh_status=freshness.fresh_status,
            mitigation_depth=freshness.mitigation_depth,
            failed_order_block=failure.failed_order_block,
            possible_breaker_created=failure.possible_breaker_created,
            reaction_status=reaction_status,
            invalidation_level=invalidation_level,
            entry_logic=OrderBlockEntryLogic(
                invalidation_level=invalidation_level,
                stop_loss_reference="below_zone_low_or_sweep_low"
                if event.direction is OrderBlockDirection.BULLISH
                else "above_zone_high_or_sweep_high",
                target_reference="nearest_buy_side_liquidity_above"
                if event.direction is OrderBlockDirection.BULLISH
                else "nearest_sell_side_liquidity_below",
            ),
            quality_score=score,
            quality_grade=_quality_grade(score, failure),
            reasons=tuple(dict.fromkeys(reasons + list(score_reasons))),
            warnings=tuple(dict.fromkeys(warnings + list(score_warnings))),
        )

    def _weak_candidates(
        self,
        candles: Sequence[_Candle],
        atr_values: Sequence[float],
        symbol: str,
        timeframe: str,
        premium_discount_context: Mapping[str, Any],
        htf_context: Mapping[str, Any],
    ) -> list[OrderBlock]:
        for position in range(1, len(candles)):
            previous = candles[position - 1]
            current = candles[position]
            if previous.is_bearish and current.is_bullish:
                direction = OrderBlockDirection.BULLISH
            elif previous.is_bullish and current.is_bearish:
                direction = OrderBlockDirection.BEARISH
            else:
                continue
            if self._is_directional_displacement(current, atr_values[position], direction):
                continue
            event = _StructureEvent(
                event_type="none",
                direction=direction,
                confirmation_index=current.index,
                broken_level=None,
                broken_swing_index=None,
                displacement_strength="none",
                fvg_created=False,
                quality_score=None,
                raw="none_or_minor_reaction",
            )
            return [
                self._build_order_block(
                    candles,
                    atr_values,
                    event,
                    previous,
                    position,
                    position,
                    (),
                    symbol,
                    timeframe,
                    premium_discount_context,
                    htf_context,
                )
            ]
        return []

    def _is_directional_displacement(self, candle: _Candle, atr: float, direction: OrderBlockDirection) -> bool:
        if direction is OrderBlockDirection.BULLISH and not candle.is_bullish:
            return False
        if direction is OrderBlockDirection.BEARISH and not candle.is_bearish:
            return False
        if candle.body_ratio < self.config.displacement_body_ratio:
            return False
        if candle.range < atr * self.config.displacement_range_atr:
            return False
        if direction is OrderBlockDirection.BULLISH:
            return (candle.close - candle.low) / candle.range >= 1.0 - self.config.close_near_extreme_ratio
        return (candle.high - candle.close) / candle.range >= 1.0 - self.config.close_near_extreme_ratio


def detect_order_blocks(
    df: Sequence[CandleNode | Mapping[str, Any]],
    swings: Sequence[Mapping[str, Any]] | None = None,
    bos_events: Sequence[Mapping[str, Any] | str] | None = None,
    mss_events: Sequence[Mapping[str, Any] | str] | None = None,
    liquidity_sweeps: Sequence[Mapping[str, Any] | str] | None = None,
    *,
    symbol: str = "unknown",
    timeframe: str = "unknown",
    premium_discount_context: Mapping[str, Any] | None = None,
    htf_context: Mapping[str, Any] | None = None,
    config: OrderBlockDetectionConfig | None = None,
) -> list[dict[str, Any]]:
    detector = ICTOrderBlockDetector(config)
    return [
        block.as_dict()
        for block in detector.detect(
            df,
            swings,
            bos_events,
            mss_events,
            liquidity_sweeps,
            symbol=symbol,
            timeframe=timeframe,
            premium_discount_context=premium_discount_context,
            htf_context=htf_context,
        )
    ]


def _normalize_candles(candles: Sequence[CandleNode | Mapping[str, Any]]) -> list[_Candle]:
    normalized: list[_Candle] = []
    for position, raw in enumerate(candles):
        if isinstance(raw, CandleNode):
            is_closed = raw.is_closed
            timestamp = raw.start_time
            values: Mapping[str, Any] = {
                "index": raw.sequence_id or position,
                "open": raw.open_p,
                "high": raw.high_p,
                "low": raw.low_p,
                "close": raw.close_p,
                "volume": raw.volume,
            }
        else:
            is_closed = bool(raw.get("is_closed", raw.get("closed", True)))
            timestamp = raw.get("timestamp", raw.get("time", datetime.fromtimestamp(position)))
            values = raw
        if not is_closed:
            continue
        if not isinstance(timestamp, datetime):
            timestamp = datetime.fromisoformat(str(timestamp)) if isinstance(timestamp, str) else datetime.fromtimestamp(float(timestamp))
        normalized.append(
            _Candle(
                position=len(normalized),
                index=int(values.get("index", position)),
                timestamp=timestamp,
                open=float(values["open"]),
                high=float(values["high"]),
                low=float(values["low"]),
                close=float(values["close"]),
                volume=float(values.get("volume", 0.0)),
            )
        )
    return normalized


def _atr_values(candles: Sequence[_Candle], period: int) -> list[float]:
    true_ranges: list[float] = []
    values: list[float] = []
    for position, candle in enumerate(candles):
        previous_close = candles[position - 1].close if position > 0 else candle.close
        true_range = max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close), 1e-9)
        true_ranges.append(true_range)
        window = true_ranges[max(0, len(true_ranges) - period):]
        values.append(sum(window) / len(window))
    return values


def _normalize_structure_events(events: Sequence[Mapping[str, Any] | str], event_type: str) -> list[_StructureEvent]:
    normalized: list[_StructureEvent] = []
    for event in events:
        direction_text = _field(event, "direction", default=_text(event)).lower()
        if "bullish" in direction_text:
            direction = OrderBlockDirection.BULLISH
        elif "bearish" in direction_text:
            direction = OrderBlockDirection.BEARISH
        else:
            continue
        confirmation_index = _int_field(event, "confirmation_candle_index", "confirmation_index", "candle_index", "index")
        if confirmation_index is None:
            continue
        normalized.append(
            _StructureEvent(
                event_type=event_type,
                direction=direction,
                confirmation_index=confirmation_index,
                broken_level=_float_field(event, "broken_level"),
                broken_swing_index=_int_field(event, "broken_swing_index"),
                displacement_strength=str(_field(event, "displacement_strength", default="unknown")),
                fvg_created=bool(_field(event, "fvg_created", "fvg_after_mss", default=False)),
                quality_score=_float_field(event, "quality_score", "confidence_score"),
                raw=event,
            )
        )
    return normalized


def _zone_definitions(candle: _Candle, direction: OrderBlockDirection) -> OrderBlockAlternativeZones:
    full = _zone(candle.low, candle.high)
    body = _zone(candle.open, candle.close)
    if direction is OrderBlockDirection.BULLISH:
        refined = _zone(candle.low, candle.open)
    else:
        refined = _zone(candle.open, candle.high)
    return OrderBlockAlternativeZones(full_range=full, body_range=body, refined_range=refined)


def _zone(low: float, high: float) -> OrderBlockPriceZone:
    zone_low = min(float(low), float(high))
    zone_high = max(float(low), float(high))
    return OrderBlockPriceZone(zone_high=zone_high, zone_low=zone_low, zone_mid=(zone_low + zone_high) / 2.0)


def _select_zone(zones: OrderBlockAlternativeZones, mode: OrderBlockZoneMode) -> OrderBlockPriceZone:
    if mode is OrderBlockZoneMode.BODY_RANGE:
        return zones.body_range
    if mode is OrderBlockZoneMode.REFINED_RANGE:
        return zones.refined_range
    return zones.full_range


def _mitigation_and_failure(
    candles: Sequence[_Candle],
    start_position: int,
    zone: OrderBlockPriceZone,
    mean_threshold: float,
    direction: OrderBlockDirection,
) -> tuple[OrderBlockFreshness, OrderBlockFailureStatus]:
    status = OrderBlockFreshStatus.FRESH
    mitigation_depth = "untouched"
    mean_touched = False
    times_tapped = 0
    failed_candle: _Candle | None = None
    for candle in candles[start_position + 1:]:
        if direction is OrderBlockDirection.BULLISH:
            if candle.close < zone.zone_low:
                failed_candle = candle
                status = OrderBlockFreshStatus.FAILED
                mitigation_depth = "closed_below_zone_low"
                break
            if candle.low <= zone.zone_high:
                times_tapped += 1
                status = OrderBlockFreshStatus.TOUCHED
                mitigation_depth = "entered_zone"
            if candle.low <= mean_threshold:
                mean_touched = True
                status = OrderBlockFreshStatus.PARTIALLY_MITIGATED
                mitigation_depth = "reached_mean_threshold"
            if candle.low <= zone.zone_low and candle.close >= zone.zone_low:
                status = OrderBlockFreshStatus.FULLY_MITIGATED_BUT_NOT_FAILED
                mitigation_depth = "swept_zone_low_but_reclaimed"
        else:
            if candle.close > zone.zone_high:
                failed_candle = candle
                status = OrderBlockFreshStatus.FAILED
                mitigation_depth = "closed_above_zone_high"
                break
            if candle.high >= zone.zone_low:
                times_tapped += 1
                status = OrderBlockFreshStatus.TOUCHED
                mitigation_depth = "entered_zone"
            if candle.high >= mean_threshold:
                mean_touched = True
                status = OrderBlockFreshStatus.PARTIALLY_MITIGATED
                mitigation_depth = "reached_mean_threshold"
            if candle.high >= zone.zone_high and candle.close <= zone.zone_high:
                status = OrderBlockFreshStatus.FULLY_MITIGATED_BUT_NOT_FAILED
                mitigation_depth = "swept_zone_high_but_reclaimed"
    if times_tapped >= 3 and status not in {OrderBlockFreshStatus.FAILED, OrderBlockFreshStatus.FRESH}:
        status = OrderBlockFreshStatus.STALE
    possible_breaker = failed_candle is not None
    failure = OrderBlockFailureStatus(
        failed_order_block=failed_candle is not None,
        failed_candle_index=failed_candle.index if failed_candle else None,
        failed_timestamp=failed_candle.timestamp if failed_candle else None,
        failure_close=failed_candle.close if failed_candle else None,
        failure_condition=mitigation_depth if failed_candle else None,
        possible_breaker_created=possible_breaker,
        possible_new_poi_type=(
            "bearish_breaker_block"
            if possible_breaker and direction is OrderBlockDirection.BULLISH
            else "bullish_breaker_block"
            if possible_breaker
            else None
        ),
    )
    freshness = OrderBlockFreshness(
        fresh_status=status,
        mitigation_depth=mitigation_depth,
        mean_threshold_touched=mean_touched,
        times_tapped=times_tapped,
    )
    return freshness, failure


def _fvg_context(
    candles: Sequence[_Candle],
    ob_position: int,
    confirmation_position: int,
    direction: OrderBlockDirection,
    ob_zone: OrderBlockPriceZone,
) -> OrderBlockFVGContext:
    for position in range(max(2, ob_position + 1), min(len(candles), confirmation_position + 3)):
        first = candles[position - 2]
        third = candles[position]
        if direction is OrderBlockDirection.BULLISH and first.high < third.low:
            fvg_zone = _zone(first.high, third.low)
            return OrderBlockFVGContext(True, "bullish", fvg_zone.zone_low, fvg_zone.zone_high, _zones_overlap(ob_zone, fvg_zone))
        if direction is OrderBlockDirection.BEARISH and first.low > third.high:
            fvg_zone = _zone(third.high, first.low)
            return OrderBlockFVGContext(True, "bearish", fvg_zone.zone_low, fvg_zone.zone_high, _zones_overlap(ob_zone, fvg_zone))
    return OrderBlockFVGContext(False, "none", None, None, False)


def _liquidity_context(
    direction: OrderBlockDirection,
    sweeps: Sequence[Mapping[str, Any] | str],
    ob_index: int,
    confirmation_index: int,
) -> OrderBlockLiquidityContext:
    expected = "sell_side_liquidity_sweep" if direction is OrderBlockDirection.BULLISH else "buy_side_liquidity_sweep"
    for sweep in sweeps:
        text = _text(sweep).lower()
        sweep_index = _int_field(sweep, "sweep_candle_index", "candle_index", "index")
        if expected in text and (sweep_index is None or ob_index - 10 <= sweep_index <= confirmation_index):
            return OrderBlockLiquidityContext(
                liquidity_sweep_before_displacement=True,
                sweep_type=expected,
                sweep_candle_index=sweep_index,
                swept_liquidity_id=str(_field(sweep, "swept_liquidity_id", "liquidity_id", default="unknown")),
                sweep_quality_score=_float_field(sweep, "quality_score", "confidence_score"),
            )
    return OrderBlockLiquidityContext(False, "none", None, None, None)


def _displacement_snapshot(
    candle: _Candle,
    atr: float,
    event: _StructureEvent,
    displacement_position: int,
    confirmation_position: int,
    present: bool,
) -> OrderBlockDisplacement:
    if event.direction is OrderBlockDirection.BULLISH:
        close_position = "near_high" if (candle.close - candle.low) / candle.range >= 0.70 else "mid_or_weak"
    else:
        close_position = "near_low" if (candle.high - candle.close) / candle.range >= 0.70 else "mid_or_weak"
    strength = event.displacement_strength
    if strength in {"unknown", "none"} and present:
        strength = "strong" if candle.range / max(atr, 1e-9) >= 1.5 else "moderate"
    return OrderBlockDisplacement(
        displacement_required=True,
        displacement_present=present,
        displacement_strength=strength,
        displacement_start_index=candle.index,
        confirmation_index=confirmation_position,
        body_to_range_ratio=round(candle.body_ratio, 4),
        range_to_atr_ratio=round(candle.range / max(atr, 1e-9), 4),
        close_position=close_position,
    )


def _premium_discount(
    direction: OrderBlockDirection,
    zone: OrderBlockPriceZone,
    pd_context: Mapping[str, Any],
    htf_context: Mapping[str, Any],
) -> OrderBlockPremiumDiscountContext:
    low = _float_field(pd_context, "dealing_range_low", "range_low")
    high = _float_field(pd_context, "dealing_range_high", "range_high")
    equilibrium = _float_field(pd_context, "equilibrium")
    explicit_location = str(_field(pd_context, "poi_location", "premium_discount_position", default="")).lower()
    if not explicit_location and low is not None and high is not None:
        equilibrium = equilibrium if equilibrium is not None else (low + high) / 2.0
        explicit_location = "discount" if zone.zone_mid < equilibrium else "premium"
    if not explicit_location:
        explicit_location = "discount" if direction is OrderBlockDirection.BULLISH else "premium"
    htf_bias = str(_field(htf_context, "htf_trend_state", "higher_timeframe_bias", default="neutral")).lower()
    alignment = (direction is OrderBlockDirection.BULLISH and "discount" in explicit_location) or (
        direction is OrderBlockDirection.BEARISH and "premium" in explicit_location
    )
    if direction.value not in htf_bias and htf_bias not in {"neutral", "unknown", ""}:
        alignment = False
    return OrderBlockPremiumDiscountContext(
        poi_location=explicit_location,
        premium_discount_alignment=alignment,
        dealing_range_low=low,
        dealing_range_high=high,
        equilibrium=equilibrium,
    )


def _created_by_event(event: _StructureEvent, liquidity: OrderBlockLiquidityContext) -> str:
    base = f"{event.direction.value}_{event.event_type}"
    if event.event_type == "none":
        return "none_or_minor_reaction"
    if liquidity.liquidity_sweep_before_displacement:
        return f"{base}_after_{'sell_side_sweep' if event.direction is OrderBlockDirection.BULLISH else 'buy_side_sweep'}"
    return base


def _quality_score(
    event: _StructureEvent,
    displacement: OrderBlockDisplacement,
    liquidity: OrderBlockLiquidityContext,
    fvg: OrderBlockFVGContext,
    premium_discount: OrderBlockPremiumDiscountContext,
    freshness: OrderBlockFreshness,
    failure: OrderBlockFailureStatus,
    zone: OrderBlockPriceZone,
    atr: float,
) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    reasons: list[str] = []
    warnings: list[str] = []
    score = 1.0
    if event.event_type in {"BOS", "MSS"}:
        score += 2.0
        reasons.append("order_block_caused_confirmed_structure_break")
    else:
        warnings.append("no_structure_break_no_valid_ob")
    if event.event_type == "MSS":
        score += 0.75
        reasons.append("mss_reversal_context")
    if displacement.displacement_present:
        score += 1.5
        reasons.append("displacement_present")
    else:
        warnings.append("no_displacement_after_order_block")
    if displacement.displacement_strength in {"strong", "very_strong"}:
        score += 0.75
        reasons.append("strong_displacement")
    if liquidity.liquidity_sweep_before_displacement:
        score += 1.25
        reasons.append("liquidity_sweep_before_displacement")
    else:
        warnings.append("no_liquidity_sweep_context")
    if fvg.fvg_created_after_displacement:
        score += 1.0
        reasons.append("fvg_created_after_displacement")
    if premium_discount.premium_discount_alignment:
        score += 0.75
        reasons.append("premium_discount_alignment")
    score += {
        OrderBlockFreshStatus.FRESH: 1.0,
        OrderBlockFreshStatus.TOUCHED: 0.5,
        OrderBlockFreshStatus.PARTIALLY_MITIGATED: 0.25,
        OrderBlockFreshStatus.FULLY_MITIGATED_BUT_NOT_FAILED: -0.75,
        OrderBlockFreshStatus.STALE: -1.25,
        OrderBlockFreshStatus.FAILED: -5.0,
    }[freshness.fresh_status]
    if freshness.mean_threshold_touched:
        warnings.append("mean_threshold_touched_freshness_reduced")
    if zone.zone_high - zone.zone_low <= max(atr * 2.5, 1e-9):
        score += 0.5
        reasons.append("zone_size_efficient")
    else:
        score -= 1.0
        warnings.append("order_block_zone_too_wide")
    if failure.failed_order_block:
        warnings.append("failed_order_block_do_not_use_for_original_direction")
        score = min(score, 2.9)
    if event.event_type == "none":
        score = min(score, 4.0)
    if not liquidity.liquidity_sweep_before_displacement and event.event_type == "BOS":
        score = min(score, 7.5)
    return max(0.0, min(10.0, round(score, 2))), tuple(reasons), tuple(warnings)


def _quality_grade(score: float, failure: OrderBlockFailureStatus) -> OrderBlockQualityGrade:
    if failure.failed_order_block or score < 2.0:
        return OrderBlockQualityGrade.INVALIDATED
    if score < 4.0:
        return OrderBlockQualityGrade.WEAK
    if score < 6.5:
        return OrderBlockQualityGrade.MODERATE
    if score < 8.5:
        return OrderBlockQualityGrade.STRONG
    return OrderBlockQualityGrade.HIGH_QUALITY


def _reaction_status(freshness: OrderBlockFreshness, failure: OrderBlockFailureStatus) -> OrderBlockReactionStatus:
    if failure.failed_order_block:
        return OrderBlockReactionStatus.FAILED
    if freshness.fresh_status is OrderBlockFreshStatus.FRESH:
        return OrderBlockReactionStatus.WAITING_FOR_RETEST
    if freshness.fresh_status in {OrderBlockFreshStatus.TOUCHED, OrderBlockFreshStatus.PARTIALLY_MITIGATED}:
        return OrderBlockReactionStatus.REACTING
    return OrderBlockReactionStatus.NOT_TRADEABLE


def _dedupe_order_blocks(blocks: Sequence[OrderBlock]) -> list[OrderBlock]:
    best: dict[tuple[int, str, str], OrderBlock] = {}
    for block in blocks:
        key = (block.ob_candle.index, block.direction.value, block.created_by_event)
        current = best.get(key)
        if current is None or block.quality_score > current.quality_score:
            best[key] = block
    return list(best.values())


def _zones_overlap(left: OrderBlockPriceZone, right: OrderBlockPriceZone) -> bool:
    return max(left.zone_low, right.zone_low) <= min(left.zone_high, right.zone_high)


def _field(event: Mapping[str, Any] | str, *keys: str, default: Any = None) -> Any:
    if isinstance(event, str):
        return default
    for key in keys:
        if key in event:
            return event[key]
    return default


def _text(event: Mapping[str, Any] | str) -> str:
    if isinstance(event, str):
        return event
    return " ".join(str(value) for value in event.values() if isinstance(value, str))


def _int_field(event: Mapping[str, Any] | str, *keys: str) -> int | None:
    value = _field(event, *keys)
    return int(value) if value is not None else None


def _float_field(event: Mapping[str, Any] | str, *keys: str) -> float | None:
    value = _field(event, *keys)
    return float(value) if value is not None else None
