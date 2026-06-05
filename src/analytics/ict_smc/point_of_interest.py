"""Rule-based ICT/SMC point-of-interest zone detection.

A point of interest is context, not an entry signal. These rules identify
candidate zones from closed candles and explicitly keep entry disabled until
lower-timeframe confirmation is supplied.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from src.core.domain.market_data import CandleNode


class POIType(str, Enum):
    ORDER_BLOCK = "order_block"
    ORDER_BLOCK_CANDIDATE = "order_block_candidate"
    FAIR_VALUE_GAP = "FVG"
    BREAKER_BLOCK = "breaker_block"
    MITIGATION_BLOCK = "mitigation_block"
    DEMAND_ZONE = "demand_zone"
    SUPPLY_ZONE = "supply_zone"
    SUPPORT_RESISTANCE_FLIP = "support_resistance_flip"


class POIDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class POIFreshStatus(str, Enum):
    FRESH = "fresh"
    TOUCHED = "touched"
    PARTIALLY_MITIGATED = "partially_mitigated"
    FULLY_MITIGATED = "fully_mitigated"
    INVALIDATED = "invalidated"
    STALE = "stale"


class POIQualityGrade(str, Enum):
    INVALID = "invalid"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


class POIReactionStatus(str, Enum):
    WAITING_FOR_RETEST = "waiting_for_retest"
    CONFIRMED_REACTION = "confirmed_reaction"
    NOT_TRADEABLE = "not_tradeable"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True)
class POIDetectionConfig:
    displacement_body_ratio: float = 0.55
    displacement_range_atr: float = 1.0
    close_near_extreme_ratio: float = 0.30
    atr_period: int = 14
    max_order_block_lookback: int = 5
    max_zone_size_atr: float = 2.5
    include_order_blocks: bool = True
    include_fair_value_gaps: bool = True
    include_demand_supply: bool = True
    include_weak_candidates: bool = True
    minimum_quality_to_return: float = 0.0
    timeframe: str | None = None

    def __post_init__(self) -> None:
        if self.displacement_body_ratio <= 0:
            raise ValueError("displacement_body_ratio must be positive.")
        if self.displacement_range_atr <= 0:
            raise ValueError("displacement_range_atr must be positive.")
        if self.atr_period < 1:
            raise ValueError("atr_period must be positive.")
        if self.max_order_block_lookback < 1:
            raise ValueError("max_order_block_lookback must be positive.")


@dataclass(frozen=True, slots=True)
class POIPriceZone:
    zone_low: float
    zone_mid: float
    zone_high: float


@dataclass(frozen=True, slots=True)
class POIContextSnapshot:
    created_by_event: str
    structure_event: str
    liquidity_sweep_context: str
    displacement_strength: str
    fvg_confluence: bool
    premium_discount_location: str
    higher_timeframe_alignment: str
    session_name: str
    zone_size_atr: float


@dataclass(frozen=True, slots=True)
class POIEntryLogic:
    entry_allowed_from_poi_alone: bool = False
    entry_allowed_after_confirmation: bool = False
    required_confirmation: str = "wait_for_ltf_choch_or_mss_with_displacement"
    stop_loss_reference: str = "poi_invalidation_level"
    target_reference: str = "opposite_side_liquidity_or_draw_on_liquidity"


@dataclass(frozen=True, slots=True)
class PointOfInterestZone:
    poi_id: str
    concept_name: str
    symbol: str
    timeframe: str
    poi_type: POIType
    direction: POIDirection
    zone: POIPriceZone
    created_index: int
    created_timestamp: datetime
    invalidation_level: float
    created_by_event: str
    fresh_status: POIFreshStatus
    quality_score: float
    quality_grade: POIQualityGrade
    reaction_status: POIReactionStatus
    context: POIContextSnapshot
    entry_logic: POIEntryLogic
    reasons: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def zone_low(self) -> float:
        return self.zone.zone_low

    @property
    def zone_mid(self) -> float:
        return self.zone.zone_mid

    @property
    def zone_high(self) -> float:
        return self.zone.zone_high

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["poi_type"] = self.poi_type.value
        payload["direction"] = self.direction.value
        payload["fresh_status"] = self.fresh_status.value
        payload["quality_grade"] = self.quality_grade.value
        payload["reaction_status"] = self.reaction_status.value
        payload["zone_low"] = self.zone.zone_low
        payload["zone_mid"] = self.zone.zone_mid
        payload["zone_high"] = self.zone.zone_high
        payload["entry_allowed_from_poi_alone"] = False
        payload["entry_allowed_after_confirmation"] = self.entry_logic.entry_allowed_after_confirmation
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
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_ratio(self) -> float:
        return self.body / self.range


class ICTPointOfInterestDetector:
    """Detects deterministic ICT/SMC point-of-interest zones."""

    def __init__(self, config: POIDetectionConfig | None = None) -> None:
        self.config = config or POIDetectionConfig()

    def detect(
        self,
        candles: Sequence[CandleNode | Mapping[str, Any]],
        *,
        timeframe: str | None = None,
        symbol: str = "unknown",
        structure_events: Sequence[Mapping[str, Any] | str] | None = None,
        liquidity_events: Sequence[Mapping[str, Any] | str] | None = None,
        htf_context: Mapping[str, Any] | None = None,
        session_name: str = "unknown",
    ) -> tuple[PointOfInterestZone, ...]:
        normalized = _normalize_candles(candles)
        if len(normalized) < 3:
            return ()

        atr_values = _atr_values(normalized, self.config.atr_period)
        structure = tuple(structure_events or ())
        liquidity = tuple(liquidity_events or ())
        active_timeframe = timeframe or self.config.timeframe or "unknown"
        detected: list[PointOfInterestZone] = []

        for position, candle in enumerate(normalized):
            atr = atr_values[position]
            if self.config.include_fair_value_gaps and position >= 2:
                fvg = self._detect_fvg(
                    normalized,
                    position,
                    atr,
                    active_timeframe,
                    symbol,
                    structure,
                    liquidity,
                    htf_context or {},
                    session_name,
                )
                if fvg is not None:
                    detected.append(fvg)

            if self.config.include_order_blocks and self._is_displacement(candle, atr):
                ob = self._detect_order_block(
                    normalized,
                    position,
                    atr,
                    active_timeframe,
                    symbol,
                    structure,
                    liquidity,
                    htf_context or {},
                    session_name,
                )
                if ob is not None:
                    detected.append(ob)
                if self.config.include_demand_supply:
                    detected.append(
                        self._demand_supply_from_displacement(
                            normalized,
                            position,
                            atr,
                            active_timeframe,
                            symbol,
                            structure,
                            liquidity,
                            htf_context or {},
                            session_name,
                        )
                    )

            if self.config.include_weak_candidates:
                weak = self._detect_weak_candidate(
                    normalized,
                    position,
                    atr,
                    active_timeframe,
                    symbol,
                    structure,
                    liquidity,
                    htf_context or {},
                    session_name,
                )
                if weak is not None:
                    detected.append(weak)

        unique = _dedupe_zones(detected)
        return tuple(
            zone
            for zone in sorted(unique, key=lambda item: (item.created_index, item.quality_score), reverse=True)
            if zone.quality_score >= self.config.minimum_quality_to_return
        )

    def _detect_fvg(
        self,
        candles: Sequence[_Candle],
        position: int,
        atr: float,
        timeframe: str,
        symbol: str,
        structure_events: Sequence[Mapping[str, Any] | str],
        liquidity_events: Sequence[Mapping[str, Any] | str],
        htf_context: Mapping[str, Any],
        session_name: str,
    ) -> PointOfInterestZone | None:
        first = candles[position - 2]
        current = candles[position]
        if first.high < current.low:
            direction = POIDirection.BULLISH
            zone = _zone(first.high, current.low)
        elif first.low > current.high:
            direction = POIDirection.BEARISH
            zone = _zone(current.high, first.low)
        else:
            return None

        structure_event = _structure_event_for(direction, structure_events, current.index, position)
        created_by_event = _created_by_event(direction, structure_event, liquidity_events, current.index, position, "FVG")
        return self._build_zone(
            candles,
            current,
            POIType.FAIR_VALUE_GAP,
            direction,
            zone,
            atr,
            timeframe,
            symbol,
            created_by_event,
            structure_event,
            liquidity_events,
            htf_context,
            session_name,
            fvg_confluence=True,
            reasons=("three_candle_imbalance_detected",),
            created_position=position,
        )

    def _detect_order_block(
        self,
        candles: Sequence[_Candle],
        position: int,
        atr: float,
        timeframe: str,
        symbol: str,
        structure_events: Sequence[Mapping[str, Any] | str],
        liquidity_events: Sequence[Mapping[str, Any] | str],
        htf_context: Mapping[str, Any],
        session_name: str,
    ) -> PointOfInterestZone | None:
        displacement = candles[position]
        direction = POIDirection.BULLISH if displacement.is_bullish else POIDirection.BEARISH
        target_is_opposite = (lambda c: c.is_bearish) if direction is POIDirection.BULLISH else (lambda c: c.is_bullish)
        start = max(0, position - self.config.max_order_block_lookback)
        candidates = [candle for candle in candles[start:position] if target_is_opposite(candle)]
        if not candidates:
            return None
        source = candidates[-1]
        structure_event = _structure_event_for(direction, structure_events, displacement.index, position)
        created_by_event = _created_by_event(direction, structure_event, liquidity_events, displacement.index, position, "order_block")
        return self._build_zone(
            candles,
            source,
            POIType.ORDER_BLOCK,
            direction,
            _zone(source.low, source.high),
            atr,
            timeframe,
            symbol,
            created_by_event,
            structure_event,
            liquidity_events,
            htf_context,
            session_name,
            fvg_confluence=_has_near_fvg(candles, position, direction),
            reasons=("last_opposite_candle_before_displacement",),
            created_index=displacement.index,
            created_timestamp=displacement.timestamp,
            created_position=position,
        )

    def _demand_supply_from_displacement(
        self,
        candles: Sequence[_Candle],
        position: int,
        atr: float,
        timeframe: str,
        symbol: str,
        structure_events: Sequence[Mapping[str, Any] | str],
        liquidity_events: Sequence[Mapping[str, Any] | str],
        htf_context: Mapping[str, Any],
        session_name: str,
    ) -> PointOfInterestZone:
        displacement = candles[position]
        direction = POIDirection.BULLISH if displacement.is_bullish else POIDirection.BEARISH
        poi_type = POIType.DEMAND_ZONE if direction is POIDirection.BULLISH else POIType.SUPPLY_ZONE
        base = candles[max(0, position - 2):position] or [displacement]
        zone = _zone(min(c.low for c in base), max(c.high for c in base))
        structure_event = _structure_event_for(direction, structure_events, displacement.index, position)
        created_by_event = _created_by_event(direction, structure_event, liquidity_events, displacement.index, position, poi_type.value)
        return self._build_zone(
            candles,
            displacement,
            poi_type,
            direction,
            zone,
            atr,
            timeframe,
            symbol,
            created_by_event,
            structure_event,
            liquidity_events,
            htf_context,
            session_name,
            fvg_confluence=_has_near_fvg(candles, position, direction),
            reasons=("base_before_strong_directional_move",),
            created_position=position,
        )

    def _detect_weak_candidate(
        self,
        candles: Sequence[_Candle],
        position: int,
        atr: float,
        timeframe: str,
        symbol: str,
        structure_events: Sequence[Mapping[str, Any] | str],
        liquidity_events: Sequence[Mapping[str, Any] | str],
        htf_context: Mapping[str, Any],
        session_name: str,
    ) -> PointOfInterestZone | None:
        if position < 1 or position % 5 != 0:
            return None
        current = candles[position]
        previous = candles[position - 1]
        if self._is_displacement(current, atr):
            return None
        if previous.is_bearish and current.is_bullish:
            direction = POIDirection.BULLISH
        elif previous.is_bullish and current.is_bearish:
            direction = POIDirection.BEARISH
        else:
            return None
        structure_event = _structure_event_for(direction, structure_events, current.index, position)
        if structure_event != "none":
            return None
        return self._build_zone(
            candles,
            previous,
            POIType.ORDER_BLOCK_CANDIDATE,
            direction,
            _zone(previous.low, previous.high),
            atr,
            timeframe,
            symbol,
            "none_or_minor_reaction",
            structure_event,
            liquidity_events,
            htf_context,
            session_name,
            fvg_confluence=False,
            reasons=("minor_reaction_without_structure_confirmation",),
            warnings=("weak_random_ob_not_tradeable",),
            created_index=current.index,
            created_timestamp=current.timestamp,
            created_position=position,
        )

    def _build_zone(
        self,
        candles: Sequence[_Candle],
        source_candle: _Candle,
        poi_type: POIType,
        direction: POIDirection,
        zone: POIPriceZone,
        atr: float,
        timeframe: str,
        symbol: str,
        created_by_event: str,
        structure_event: str,
        liquidity_events: Sequence[Mapping[str, Any] | str],
        htf_context: Mapping[str, Any],
        session_name: str,
        *,
        fvg_confluence: bool,
        reasons: tuple[str, ...],
        warnings: tuple[str, ...] = (),
        created_index: int | None = None,
        created_timestamp: datetime | None = None,
        created_position: int | None = None,
    ) -> PointOfInterestZone:
        active_position = source_candle.position if created_position is None else created_position
        fresh_status = _fresh_status(candles, active_position, zone, direction)
        liquidity_context = _liquidity_context_for(direction, liquidity_events, source_candle.index, active_position)
        zone_size_atr = (zone.zone_high - zone.zone_low) / max(atr, 1e-9)
        context = POIContextSnapshot(
            created_by_event=created_by_event,
            structure_event=structure_event,
            liquidity_sweep_context=liquidity_context,
            displacement_strength="strong" if _is_strong_context(created_by_event) else "moderate",
            fvg_confluence=fvg_confluence,
            premium_discount_location=_premium_discount_location(direction, htf_context),
            higher_timeframe_alignment=_htf_alignment(direction, htf_context),
            session_name=session_name,
            zone_size_atr=round(zone_size_atr, 4),
        )
        score, score_reasons, score_warnings = _score_zone(
            poi_type,
            direction,
            context,
            fresh_status,
            zone_size_atr,
            warnings,
        )
        reaction_status = POIReactionStatus.INVALIDATED if fresh_status is POIFreshStatus.INVALIDATED else POIReactionStatus.WAITING_FOR_RETEST
        if score <= 3.0 or poi_type is POIType.ORDER_BLOCK_CANDIDATE:
            reaction_status = POIReactionStatus.NOT_TRADEABLE
        invalidation = zone.zone_low if direction is POIDirection.BULLISH else zone.zone_high
        created_at_index = source_candle.index if created_index is None else created_index
        return PointOfInterestZone(
            poi_id=f"POI_{timeframe}_{poi_type.value}_{direction.value}_{created_at_index}",
            concept_name="ICT_SMC_POINT_OF_INTEREST",
            symbol=symbol,
            timeframe=timeframe,
            poi_type=poi_type,
            direction=direction,
            zone=zone,
            created_index=created_at_index,
            created_timestamp=source_candle.timestamp if created_timestamp is None else created_timestamp,
            invalidation_level=invalidation,
            created_by_event=created_by_event,
            fresh_status=fresh_status,
            quality_score=score,
            quality_grade=_quality_grade(score, fresh_status),
            reaction_status=reaction_status,
            context=context,
            entry_logic=POIEntryLogic(),
            reasons=tuple(dict.fromkeys(reasons + score_reasons)),
            warnings=tuple(dict.fromkeys(warnings + score_warnings)),
        )

    def _is_displacement(self, candle: _Candle, atr: float) -> bool:
        if candle.body_ratio < self.config.displacement_body_ratio:
            return False
        if candle.range < atr * self.config.displacement_range_atr:
            return False
        if candle.is_bullish:
            return (candle.high - candle.close) / candle.range <= self.config.close_near_extreme_ratio
        if candle.is_bearish:
            return (candle.close - candle.low) / candle.range <= self.config.close_near_extreme_ratio
        return False


def detect_poi_zones(
    candles: Sequence[CandleNode | Mapping[str, Any]],
    timeframe: str,
    *,
    symbol: str = "unknown",
    structure_events: Sequence[Mapping[str, Any] | str] | None = None,
    liquidity_events: Sequence[Mapping[str, Any] | str] | None = None,
    htf_context: Mapping[str, Any] | None = None,
    session_name: str = "unknown",
    config: POIDetectionConfig | None = None,
) -> list[dict[str, Any]]:
    detector = ICTPointOfInterestDetector(config)
    return [
        zone.as_dict()
        for zone in detector.detect(
            candles,
            timeframe=timeframe,
            symbol=symbol,
            structure_events=structure_events,
            liquidity_events=liquidity_events,
            htf_context=htf_context,
            session_name=session_name,
        )
    ]


def confirm_poi_reaction(
    poi_zone: PointOfInterestZone | Mapping[str, Any],
    ltf_confirmation_events: Sequence[Mapping[str, Any] | str],
    *,
    target_liquidity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Marks a POI as actionable only after LTF confirmation is present."""
    payload = poi_zone.as_dict() if isinstance(poi_zone, PointOfInterestZone) else dict(poi_zone)
    direction = str(payload.get("direction", "")).lower()
    event_text = " ".join(_event_text(event).lower() for event in ltf_confirmation_events)
    has_sweep = "sell_side" in event_text if direction == POIDirection.BULLISH.value else "buy_side" in event_text
    has_choch = "choch" in event_text
    has_mss = "mss" in event_text
    has_displacement = "displacement" in event_text
    has_fvg = "fvg" in event_text
    confirmed = has_sweep and (has_choch or has_mss) and has_displacement

    payload["ltf_confirmation"] = f"{direction}_MSS" if has_mss else (f"{direction}_CHoCH" if has_choch else "none")
    payload["entry_allowed_from_poi_alone"] = False
    payload["entry_allowed_after_confirmation"] = confirmed
    payload["reaction_status"] = POIReactionStatus.CONFIRMED_REACTION.value if confirmed else POIReactionStatus.WAITING_FOR_RETEST.value
    payload["required_confirmation"] = "satisfied" if confirmed else "wait_for_ltf_choch_or_mss_with_displacement"
    payload["stop_loss_reference"] = "below_poi_or_sweep_extreme" if direction == POIDirection.BULLISH.value else "above_poi_or_sweep_extreme"
    payload["target_reference"] = str((target_liquidity or {}).get("liquidity_id", "opposite_side_liquidity"))
    if confirmed:
        payload["quality_score"] = min(10.0, float(payload.get("quality_score", 0.0)) + (1.0 if has_fvg else 0.75))
        payload["quality_grade"] = _quality_grade(float(payload["quality_score"]), POIFreshStatus.FRESH).value
        payload["reasons"] = tuple(payload.get("reasons", ())) + ("ltf_confirmation_validated_inside_poi",)
    return payload


def _normalize_candles(candles: Sequence[CandleNode | Mapping[str, Any]]) -> list[_Candle]:
    normalized: list[_Candle] = []
    for position, raw in enumerate(candles):
        if isinstance(raw, CandleNode):
            is_closed = True
            timestamp = raw.timestamp
            values: Mapping[str, Any] = {
                "index": getattr(raw, "index", position),
                "open": raw.open,
                "high": raw.high,
                "low": raw.low,
                "close": raw.close,
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
    values: list[float] = []
    true_ranges: list[float] = []
    for position, candle in enumerate(candles):
        previous_close = candles[position - 1].close if position > 0 else candle.close
        true_range = max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close), 1e-9)
        true_ranges.append(true_range)
        window = true_ranges[max(0, len(true_ranges) - period):]
        values.append(sum(window) / len(window))
    return values


def _zone(low: float, high: float) -> POIPriceZone:
    zone_low = min(float(low), float(high))
    zone_high = max(float(low), float(high))
    return POIPriceZone(zone_low=zone_low, zone_mid=(zone_low + zone_high) / 2.0, zone_high=zone_high)


def _event_text(event: Mapping[str, Any] | str) -> str:
    if isinstance(event, str):
        return event
    return " ".join(str(value) for value in event.values() if isinstance(value, str))


def _event_position(event: Mapping[str, Any] | str) -> int | None:
    if isinstance(event, str):
        return None
    for key in ("created_index", "candle_index", "confirmation_index", "index", "position"):
        if key in event and event[key] is not None:
            return int(event[key])
    return None


def _near_event(event: Mapping[str, Any] | str, candle_index: int, position: int, lookback: int = 8) -> bool:
    event_pos = _event_position(event)
    if event_pos is None:
        return True
    return 0 <= candle_index - event_pos <= lookback or 0 <= position - event_pos <= lookback


def _structure_event_for(direction: POIDirection, events: Sequence[Mapping[str, Any] | str], candle_index: int, position: int) -> str:
    for event in events:
        text = _event_text(event).lower()
        if direction.value in text and ("mss" in text or "market_structure_shift" in text) and _near_event(event, candle_index, position):
            return f"{direction.value}_MSS"
        if direction.value in text and ("bos" in text or "break_of_structure" in text) and _near_event(event, candle_index, position):
            return f"{direction.value}_BOS"
    return "none"


def _liquidity_context_for(direction: POIDirection, events: Sequence[Mapping[str, Any] | str], candle_index: int, position: int) -> str:
    expected = "sell_side" if direction is POIDirection.BULLISH else "buy_side"
    for event in events:
        text = _event_text(event).lower()
        if expected in text and "sweep" in text and _near_event(event, candle_index, position, lookback=12):
            return f"{expected}_liquidity_sweep"
    return "none"


def _created_by_event(
    direction: POIDirection,
    structure_event: str,
    liquidity_events: Sequence[Mapping[str, Any] | str],
    candle_index: int,
    position: int,
    source: str,
) -> str:
    liquidity_context = _liquidity_context_for(direction, liquidity_events, candle_index, position)
    if structure_event.endswith("MSS") and liquidity_context != "none":
        return f"{structure_event}_after_{liquidity_context}"
    if structure_event.endswith("BOS"):
        return f"{structure_event}_displacement"
    if structure_event.endswith("MSS"):
        return f"{structure_event}_displacement"
    if source == "FVG":
        return f"{direction.value}_displacement_imbalance"
    return f"{direction.value}_displacement"


def _has_near_fvg(candles: Sequence[_Candle], position: int, direction: POIDirection) -> bool:
    for idx in range(max(2, position - 1), min(len(candles), position + 3)):
        first = candles[idx - 2]
        third = candles[idx]
        if direction is POIDirection.BULLISH and first.high < third.low:
            return True
        if direction is POIDirection.BEARISH and first.low > third.high:
            return True
    return False


def _fresh_status(candles: Sequence[_Candle], created_position: int, zone: POIPriceZone, direction: POIDirection) -> POIFreshStatus:
    touches = 0
    deepest = POIFreshStatus.FRESH
    for candle in candles[created_position + 1:]:
        if direction is POIDirection.BULLISH:
            if candle.close < zone.zone_low:
                return POIFreshStatus.INVALIDATED
            if candle.low <= zone.zone_low:
                deepest = POIFreshStatus.FULLY_MITIGATED
            elif candle.low <= zone.zone_mid and deepest is not POIFreshStatus.FULLY_MITIGATED:
                deepest = POIFreshStatus.PARTIALLY_MITIGATED
            elif candle.low <= zone.zone_high and deepest is POIFreshStatus.FRESH:
                deepest = POIFreshStatus.TOUCHED
            if candle.low <= zone.zone_high:
                touches += 1
        else:
            if candle.close > zone.zone_high:
                return POIFreshStatus.INVALIDATED
            if candle.high >= zone.zone_high:
                deepest = POIFreshStatus.FULLY_MITIGATED
            elif candle.high >= zone.zone_mid and deepest is not POIFreshStatus.FULLY_MITIGATED:
                deepest = POIFreshStatus.PARTIALLY_MITIGATED
            elif candle.high >= zone.zone_low and deepest is POIFreshStatus.FRESH:
                deepest = POIFreshStatus.TOUCHED
            if candle.high >= zone.zone_low:
                touches += 1
    if touches >= 3 and deepest is not POIFreshStatus.FRESH:
        return POIFreshStatus.STALE
    return deepest


def _premium_discount_location(direction: POIDirection, context: Mapping[str, Any]) -> str:
    explicit = str(context.get("premium_discount_position", context.get("premium_discount_location", "unknown"))).lower()
    if explicit != "unknown":
        return explicit
    return "discount" if direction is POIDirection.BULLISH else "premium"


def _htf_alignment(direction: POIDirection, context: Mapping[str, Any]) -> str:
    raw = str(context.get("htf_trend_state", context.get("higher_timeframe_bias", "unknown"))).lower()
    if direction.value in raw:
        return "aligned"
    if raw in {"unknown", "neutral", "balanced"}:
        return "neutral"
    return "against"


def _is_strong_context(created_by_event: str) -> bool:
    text = created_by_event.lower()
    return "bos" in text or "mss" in text or "sweep" in text


def _score_zone(
    poi_type: POIType,
    direction: POIDirection,
    context: POIContextSnapshot,
    fresh_status: POIFreshStatus,
    zone_size_atr: float,
    initial_warnings: tuple[str, ...],
) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    reasons: list[str] = [f"poi_type_strength_{poi_type.value}"]
    warnings: list[str] = list(initial_warnings)
    score = {
        POIType.ORDER_BLOCK: 2.0,
        POIType.FAIR_VALUE_GAP: 1.6,
        POIType.BREAKER_BLOCK: 1.8,
        POIType.MITIGATION_BLOCK: 1.5,
        POIType.DEMAND_ZONE: 1.2,
        POIType.SUPPLY_ZONE: 1.2,
        POIType.SUPPORT_RESISTANCE_FLIP: 1.1,
        POIType.ORDER_BLOCK_CANDIDATE: 0.6,
    }[poi_type]

    if context.structure_event != "none":
        score += 2.0
        reasons.append("created_by_confirmed_structure_event")
    else:
        warnings.append("poi_without_bos_or_mss_is_context_only")
    if context.liquidity_sweep_context != "none":
        score += 1.25
        reasons.append("liquidity_sweep_context_present")
    else:
        warnings.append("no_liquidity_sweep_context")
    score += 1.5 if context.displacement_strength == "strong" else 0.5
    if context.fvg_confluence:
        score += 1.0
        reasons.append("fvg_confluence")
    pd = context.premium_discount_location
    if (direction is POIDirection.BULLISH and "discount" in pd) or (direction is POIDirection.BEARISH and "premium" in pd):
        score += 0.75
        reasons.append("premium_discount_aligned")
    if context.higher_timeframe_alignment == "aligned":
        score += 1.0
        reasons.append("higher_timeframe_aligned")
    elif context.higher_timeframe_alignment == "against":
        score -= 1.5
        warnings.append("against_higher_timeframe_bias")

    score += {
        POIFreshStatus.FRESH: 1.0,
        POIFreshStatus.TOUCHED: 0.5,
        POIFreshStatus.PARTIALLY_MITIGATED: 0.25,
        POIFreshStatus.FULLY_MITIGATED: -1.0,
        POIFreshStatus.STALE: -1.5,
        POIFreshStatus.INVALIDATED: -4.0,
    }[fresh_status]
    reasons.append(f"freshness_{fresh_status.value}")
    score += 0.5
    reasons.append("clear_invalidation_level")
    if zone_size_atr <= 2.5:
        score += 0.5
        reasons.append("zone_size_reasonable")
    else:
        score -= 1.25
        warnings.append("poi_zone_too_wide")
    if any("weak_random" in warning for warning in warnings):
        score -= 1.0

    if context.structure_event == "none":
        score = min(score, 5.0)
    if context.liquidity_sweep_context == "none":
        score = min(score, 8.0 if poi_type is POIType.FAIR_VALUE_GAP else 6.0)
    if fresh_status is POIFreshStatus.FULLY_MITIGATED:
        score = min(score, 4.0)
    if fresh_status is POIFreshStatus.INVALIDATED:
        score = min(score, 3.0)
    if poi_type is POIType.ORDER_BLOCK_CANDIDATE:
        score = min(score, 4.0)
    return max(0.0, min(10.0, round(score, 2))), tuple(reasons), tuple(warnings)


def _quality_grade(score: float, fresh_status: POIFreshStatus) -> POIQualityGrade:
    if fresh_status is POIFreshStatus.INVALIDATED or score < 2.0:
        return POIQualityGrade.INVALID
    if score < 4.0:
        return POIQualityGrade.WEAK
    if score < 6.5:
        return POIQualityGrade.MODERATE
    if score < 8.5:
        return POIQualityGrade.STRONG
    return POIQualityGrade.HIGH_QUALITY


def _dedupe_zones(zones: Sequence[PointOfInterestZone]) -> list[PointOfInterestZone]:
    best: dict[tuple[int, str, str, int, int], PointOfInterestZone] = {}
    for zone in zones:
        key = (
            zone.created_index,
            zone.poi_type.value,
            zone.direction.value,
            round(zone.zone_low, 2),
            round(zone.zone_high, 2),
        )
        existing = best.get(key)
        if existing is None or zone.quality_score > existing.quality_score:
            best[key] = zone
    return list(best.values())
