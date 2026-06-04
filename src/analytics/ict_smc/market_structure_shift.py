"""Rule-based ICT/SMC Market Structure Shift detection.

MSS is treated as a reversal or directional-shift concept, not a simple
continuation breakout. The detector is observer-only and consumes confirmed
swing points plus optional liquidity sweep evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from src.analytics.ict_smc.swing_points import (
    DetectedSwingPoint,
    SwingLiquidityType,
    SwingPointStatus,
    SwingPointType,
    SwingStrengthLabel,
)
from src.core.domain.market_data import CandleNode


class MSSDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class PreviousMovement(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    UNCLEAR = "unclear"


class MSSStatus(str, Enum):
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNCONFIRMED_WICK_BREAK = "unconfirmed_wick_break"
    INVALIDATED = "invalidated"


class MSSDisplacementStrength(str, Enum):
    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    VERY_STRONG = "very_strong"


class MSSConfidenceGrade(str, Enum):
    INVALID = "invalid"
    WEAK_CHOCH_STYLE = "weak_CHoCH_style"
    MODERATE = "moderate"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True, slots=True)
class MSSDetectionConfig:
    minimum_swing_strength: float = 4.0
    normal_mss_strength: float = 5.5
    high_quality_swing_strength: float = 7.0
    break_buffer_atr_multiplier: float = 0.10
    close_required: bool = True
    max_sweep_to_mss_bars: int = 20
    failed_mss_lookahead: int = 3
    displacement_body_ratio: float = 0.55
    displacement_range_atr: float = 1.0
    chop_window: int = 8
    chop_overlap_ratio: float = 0.60
    atr_period: int = 14
    previous_movement: str | None = None
    timeframe: str | None = None

    def __post_init__(self) -> None:
        if self.minimum_swing_strength < 0:
            raise ValueError("minimum_swing_strength cannot be negative.")
        if self.break_buffer_atr_multiplier < 0:
            raise ValueError("break_buffer_atr_multiplier cannot be negative.")
        if self.max_sweep_to_mss_bars < 0:
            raise ValueError("max_sweep_to_mss_bars cannot be negative.")
        if self.atr_period < 1:
            raise ValueError("atr_period must be positive.")


@dataclass(frozen=True, slots=True)
class MSSLiquidityEvent:
    event_index: int
    timestamp: datetime
    type: str
    swept_level: float
    swept_swing_index: int | None = None
    direction: str = "unknown"
    valid: bool = True
    strength_score: float = 0.0
    sweep_candle_high: float | None = None
    sweep_candle_low: float | None = None
    sweep_candle_close: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MSSBrokenSwing:
    type: str
    index: int
    timestamp: datetime
    confirmation_index: int
    confirmation_timestamp: datetime
    price: float
    swing_label: str
    strength_score: float
    timeframe: str


@dataclass(frozen=True, slots=True)
class MSSConfirmationCandle:
    index: int
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class MSSBreakValidation:
    close_required: bool
    break_buffer: float
    required_level: float
    actual_close: float
    candle_close_confirmed: bool
    wick_only_break: bool


@dataclass(frozen=True, slots=True)
class MSSLiquidityContext:
    sweep_before_mss: bool
    sweep_type: str = "none"
    sweep_event_index: int | None = None
    swept_level: float | None = None
    bars_between_sweep_and_mss: int | None = None
    sweep_strength_score: float = 0.0


@dataclass(frozen=True, slots=True)
class MSSDisplacement:
    displacement_strength: MSSDisplacementStrength
    displacement_score: float
    body_to_range_ratio: float
    range_to_atr_ratio: float
    close_position: str
    volume_confirmation: bool = False


@dataclass(frozen=True, slots=True)
class MSSFVGContext:
    fvg_after_mss: bool = False
    fvg_direction: str = "none"
    fvg_low: float | None = None
    fvg_high: float | None = None
    used_as_possible_entry_zone: bool = False


@dataclass(frozen=True, slots=True)
class MSSEntryConfirmationUse:
    can_confirm_long_setup: bool = False
    can_confirm_short_setup: bool = False
    recommended_entry_style: str = "wait_for_retracement_to_fvg_or_order_block"
    invalidation_reference: str = "broken_level_or_sweep_extreme"
    target_reference: str = "opposite_side_liquidity"
    execute_trade_now: bool = False


@dataclass(frozen=True, slots=True)
class MSSEvent:
    concept_name: str
    symbol: str
    timeframe: str
    detected: bool
    direction: MSSDirection
    previous_movement: PreviousMovement
    status: MSSStatus
    broken_level: float
    broken_swing: MSSBrokenSwing
    confirmation_candle: MSSConfirmationCandle
    break_validation: MSSBreakValidation
    liquidity_context: MSSLiquidityContext
    displacement: MSSDisplacement
    fvg_context: MSSFVGContext
    entry_confirmation_use: MSSEntryConfirmationUse
    confidence_score: float
    confidence_grade: MSSConfidenceGrade
    reasons: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["direction"] = self.direction.value
        payload["previous_movement"] = self.previous_movement.value
        payload["status"] = self.status.value
        payload["confidence_grade"] = self.confidence_grade.value
        payload["displacement"]["displacement_strength"] = self.displacement.displacement_strength.value
        return payload


@dataclass(frozen=True, slots=True)
class _MSSCandle:
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
    trend_state: str | None = None
    htf_context: str | None = None
    premium_discount_zone: str | None = None

    @property
    def range(self) -> float:
        return max(0.0, self.high_p - self.low_p)

    @property
    def body(self) -> float:
        return abs(self.close_p - self.open_p)

    @property
    def body_to_range_ratio(self) -> float:
        return 0.0 if self.range <= 0 else self.body / self.range


class ICTMSSDetector:
    """Detects Market Structure Shift events from closed candles."""

    def __init__(self, config: MSSDetectionConfig | None = None) -> None:
        self.config = config or MSSDetectionConfig()

    def detect(
        self,
        candles: Sequence[CandleNode | Mapping[str, Any]],
        swings: Sequence[DetectedSwingPoint | Mapping[str, Any]],
        liquidity_events: Sequence[MSSLiquidityEvent | Mapping[str, Any]] | None = None,
    ) -> tuple[MSSEvent, ...]:
        closed = tuple(candle for candle in self._normalize_candles(candles) if candle.is_closed)
        normalized_swings = tuple(self._normalize_swings(swings))
        normalized_liquidity = tuple(self._normalize_liquidity_events(liquidity_events or ()))
        if not closed or not normalized_swings:
            return tuple()

        atr_values = _calculate_atr(closed, self.config.atr_period)
        events: list[MSSEvent] = []
        used_swing_keys: set[tuple[str, int, float]] = set()

        for candle_position, candle in enumerate(closed):
            previous = self._previous_movement(closed, normalized_swings, candle_position)
            if previous in {PreviousMovement.BEARISH, PreviousMovement.UNCLEAR}:
                high = self._latest_valid_swing(
                    normalized_swings, candle.index, SwingPointType.SWING_HIGH, used_swing_keys
                )
                if high is not None:
                    event = self._evaluate_shift(
                        closed, atr_values, normalized_liquidity, candle_position, high, MSSDirection.BULLISH, previous
                    )
                    if event is not None:
                        events.append(event)
                        if event.detected and not event.break_validation.wick_only_break:
                            used_swing_keys.add(_swing_key(high))
            if previous in {PreviousMovement.BULLISH, PreviousMovement.UNCLEAR}:
                low = self._latest_valid_swing(
                    normalized_swings, candle.index, SwingPointType.SWING_LOW, used_swing_keys
                )
                if low is not None:
                    event = self._evaluate_shift(
                        closed, atr_values, normalized_liquidity, candle_position, low, MSSDirection.BEARISH, previous
                    )
                    if event is not None:
                        events.append(event)
                        if event.detected and not event.break_validation.wick_only_break:
                            used_swing_keys.add(_swing_key(low))

        return tuple(self._apply_failure_status(closed, events))

    def _normalize_candles(self, candles: Sequence[CandleNode | Mapping[str, Any]]) -> list[_MSSCandle]:
        normalized: list[_MSSCandle] = []
        for fallback_index, candle in enumerate(candles):
            if isinstance(candle, CandleNode):
                normalized.append(
                    _MSSCandle(
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
                _MSSCandle(
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
                    trend_state=_optional_string(candle.get("trend_state")),
                    htf_context=_optional_string(candle.get("htf_context")),
                    premium_discount_zone=_optional_string(candle.get("premium_discount_zone")),
                )
            )
        return normalized

    def _normalize_swings(
        self, swings: Sequence[DetectedSwingPoint | Mapping[str, Any]]
    ) -> list[DetectedSwingPoint]:
        normalized: list[DetectedSwingPoint] = []
        for swing in swings:
            if isinstance(swing, DetectedSwingPoint):
                normalized.append(swing)
                continue
            normalized.append(
                DetectedSwingPoint(
                    index=int(_first_present(swing, "index")),
                    timestamp=_coerce_datetime(_first_present(swing, "timestamp")),
                    confirmation_index=int(_first_present(swing, "confirmation_index")),
                    confirmation_timestamp=_coerce_datetime(_first_present(swing, "confirmation_timestamp")),
                    price=float(_first_present(swing, "price")),
                    type=SwingPointType(_first_present(swing, "type")),
                    strength_score=float(_first_present(swing, "strength_score", default=0.0)),
                    strength_label=_strength_label_from_text(swing.get("strength_label")),
                    timeframe=str(_first_present(swing, "timeframe", default="unknown")),
                    timeframe_weight=float(_first_present(swing, "timeframe_weight", default=1.0)),
                    liquidity_type=_liquidity_type_from_text(_first_present(swing, "liquidity_type", default="")),
                    status=SwingPointStatus(_first_present(swing, "status", default=SwingPointStatus.UNSWEPT.value)),
                    used_for=tuple(_first_present(swing, "used_for", default=())),
                    atr_reaction=float(_first_present(swing, "atr_reaction", default=0.0)),
                    reasons=tuple(_first_present(swing, "reasons", default=())),
                    warnings=tuple(_first_present(swing, "warnings", default=())),
                )
            )
        return normalized

    def _normalize_liquidity_events(
        self, events: Sequence[MSSLiquidityEvent | Mapping[str, Any]]
    ) -> list[MSSLiquidityEvent]:
        normalized: list[MSSLiquidityEvent] = []
        for event in events:
            if isinstance(event, MSSLiquidityEvent):
                normalized.append(event)
                continue
            normalized.append(
                MSSLiquidityEvent(
                    event_index=int(_first_present(event, "event_index", "index")),
                    timestamp=_coerce_datetime(_first_present(event, "timestamp")),
                    type=str(_first_present(event, "type")),
                    swept_level=float(_first_present(event, "swept_level")),
                    swept_swing_index=_optional_int(event.get("swept_swing_index")),
                    direction=str(_first_present(event, "direction", default="unknown")),
                    valid=bool(_first_present(event, "valid", default=True)),
                    strength_score=float(_first_present(event, "strength_score", default=0.0)),
                    sweep_candle_high=_optional_float(event.get("sweep_candle_high")),
                    sweep_candle_low=_optional_float(event.get("sweep_candle_low")),
                    sweep_candle_close=_optional_float(event.get("sweep_candle_close")),
                )
            )
        return normalized

    def _previous_movement(
        self, candles: Sequence[_MSSCandle], swings: Sequence[DetectedSwingPoint], candle_position: int
    ) -> PreviousMovement:
        if self.config.previous_movement:
            return _movement_from_text(self.config.previous_movement)
        candle = candles[candle_position]
        if candle.trend_state:
            return _movement_from_text(candle.trend_state)
        recent = [swing for swing in swings if swing.confirmation_index < candle.index]
        recent = sorted(recent, key=lambda swing: swing.confirmation_index)[-4:]
        if len(recent) >= 4:
            highs = [swing for swing in recent if swing.type == SwingPointType.SWING_HIGH]
            lows = [swing for swing in recent if swing.type == SwingPointType.SWING_LOW]
            if len(highs) >= 2 and len(lows) >= 2:
                if highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price:
                    return PreviousMovement.BEARISH
                if highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price:
                    return PreviousMovement.BULLISH
        recent_closes = [candle.close_p for candle in candles[max(0, candle_position - 4) : candle_position + 1]]
        if len(recent_closes) >= 4:
            if recent_closes[-1] < recent_closes[0]:
                return PreviousMovement.BEARISH
            if recent_closes[-1] > recent_closes[0]:
                return PreviousMovement.BULLISH
        return PreviousMovement.UNCLEAR

    def _latest_valid_swing(
        self,
        swings: Sequence[DetectedSwingPoint],
        candle_index: int,
        swing_type: SwingPointType,
        used_swing_keys: set[tuple[str, int, float]],
    ) -> DetectedSwingPoint | None:
        candidates = [
            swing
            for swing in swings
            if swing.type == swing_type
            and swing.confirmation_index < candle_index
            and swing.index < candle_index
            and swing.strength_score >= self.config.minimum_swing_strength
            and swing.status != SwingPointStatus.BROKEN
            and _swing_key(swing) not in used_swing_keys
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda swing: (swing.confirmation_index, swing.strength_score))

    def _evaluate_shift(
        self,
        candles: Sequence[_MSSCandle],
        atr_values: Sequence[float],
        liquidity_events: Sequence[MSSLiquidityEvent],
        candle_position: int,
        swing: DetectedSwingPoint,
        direction: MSSDirection,
        previous_movement: PreviousMovement,
    ) -> MSSEvent | None:
        candle = candles[candle_position]
        buffer = atr_values[candle_position] * self.config.break_buffer_atr_multiplier
        if direction == MSSDirection.BULLISH:
            required_level = swing.price + buffer
            close_break = candle.close_p > required_level
            wick_break = candle.high_p > required_level
        else:
            required_level = swing.price - buffer
            close_break = candle.close_p < required_level
            wick_break = candle.low_p < required_level

        if not close_break and not wick_break:
            return None
        if not close_break and self.config.close_required:
            return self._build_event(
                candles, atr_values, liquidity_events, candle_position, swing, direction, previous_movement,
                required_level, buffer, detected=False, wick_only=True
            )
        if not close_break and not self.config.close_required:
            return self._build_event(
                candles, atr_values, liquidity_events, candle_position, swing, direction, previous_movement,
                required_level, buffer, detected=True, wick_only=True
            )
        return self._build_event(
            candles, atr_values, liquidity_events, candle_position, swing, direction, previous_movement,
            required_level, buffer, detected=True, wick_only=False
        )

    def _build_event(
        self,
        candles: Sequence[_MSSCandle],
        atr_values: Sequence[float],
        liquidity_events: Sequence[MSSLiquidityEvent],
        candle_position: int,
        swing: DetectedSwingPoint,
        direction: MSSDirection,
        previous_movement: PreviousMovement,
        required_level: float,
        buffer: float,
        *,
        detected: bool,
        wick_only: bool,
    ) -> MSSEvent:
        candle = candles[candle_position]
        liquidity = _liquidity_context(liquidity_events, candle.index, direction, self.config)
        displacement = _displacement(candle, atr_values[candle_position], direction, self.config)
        fvg = _fvg_context(candles, candle_position, direction)
        entry_use = _entry_use(direction, fvg, liquidity)
        score, reasons, warnings = self._confidence_score(
            candles, candle_position, swing, direction, previous_movement, detected, wick_only, liquidity,
            displacement, fvg, entry_use
        )
        status = MSSStatus.UNCONFIRMED_WICK_BREAK if wick_only else MSSStatus.CONFIRMED
        if not detected:
            status = MSSStatus.INVALIDATED

        return MSSEvent(
            concept_name="Market Structure Shift",
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            detected=detected,
            direction=direction,
            previous_movement=previous_movement,
            status=status,
            broken_level=swing.price,
            broken_swing=MSSBrokenSwing(
                type=swing.type.value,
                index=swing.index,
                timestamp=swing.timestamp,
                confirmation_index=swing.confirmation_index,
                confirmation_timestamp=swing.confirmation_timestamp,
                price=swing.price,
                swing_label=_swing_label_from_reasons(swing),
                strength_score=swing.strength_score,
                timeframe=swing.timeframe,
            ),
            confirmation_candle=MSSConfirmationCandle(
                index=candle.index,
                timestamp=candle.timestamp,
                open=candle.open_p,
                high=candle.high_p,
                low=candle.low_p,
                close=candle.close_p,
                volume=candle.volume,
            ),
            break_validation=MSSBreakValidation(
                close_required=self.config.close_required,
                break_buffer=round(buffer, 5),
                required_level=round(required_level, 5),
                actual_close=candle.close_p,
                candle_close_confirmed=detected and not wick_only,
                wick_only_break=wick_only,
            ),
            liquidity_context=liquidity,
            displacement=displacement,
            fvg_context=fvg,
            entry_confirmation_use=entry_use,
            confidence_score=score,
            confidence_grade=_confidence_grade(score, detected, wick_only),
            reasons=tuple(reasons),
            warnings=tuple(warnings),
        )

    def _confidence_score(
        self,
        candles: Sequence[_MSSCandle],
        candle_position: int,
        swing: DetectedSwingPoint,
        direction: MSSDirection,
        previous_movement: PreviousMovement,
        detected: bool,
        wick_only: bool,
        liquidity: MSSLiquidityContext,
        displacement: MSSDisplacement,
        fvg: MSSFVGContext,
        entry_use: MSSEntryConfirmationUse,
    ) -> tuple[float, list[str], list[str]]:
        score = 0.0
        reasons: list[str] = []
        warnings: list[str] = []

        if _opposite_movement_confirmed(previous_movement, direction):
            score += 1.5
            reasons.append(f"Previous movement was {previous_movement.value}")
        elif previous_movement == PreviousMovement.UNCLEAR:
            score += 0.75
            warnings.append("previous_movement_unclear")
        else:
            warnings.append("Break direction matches previous movement; this may be BOS, not MSS")

        if swing.strength_score >= self.config.high_quality_swing_strength:
            score += 2.0
            reasons.append("Broken structure level is strong")
        elif swing.strength_score >= self.config.normal_mss_strength:
            score += 1.5
            reasons.append("Broken structure level is normal MSS quality")
        elif swing.strength_score >= self.config.minimum_swing_strength:
            score += 1.0
            warnings.append("minor_internal_swing_reference")

        if detected and not wick_only:
            score += 1.5
            reasons.append("Candle close confirmed the shift beyond the broken level")
        elif wick_only:
            warnings.append("wick_only_break_not_confirmed_mss")

        if liquidity.sweep_before_mss:
            score += 1.5
            reasons.append(f"{liquidity.sweep_type} occurred before MSS")
            if liquidity.bars_between_sweep_and_mss and liquidity.bars_between_sweep_and_mss > 0:
                decay = min(1.0, liquidity.bars_between_sweep_and_mss / max(1, self.config.max_sweep_to_mss_bars))
                if decay > 0.75:
                    score -= 0.5
                    warnings.append("delayed_sweep_to_mss")
        else:
            warnings.append("no_liquidity_sweep_before_mss")

        score += displacement.displacement_score
        if displacement.displacement_strength in {MSSDisplacementStrength.STRONG, MSSDisplacementStrength.VERY_STRONG}:
            reasons.append("Displacement confirmed the shift")
        elif displacement.displacement_strength == MSSDisplacementStrength.MODERATE:
            reasons.append("Moderate displacement supports the shift")
        else:
            warnings.append("weak_or_missing_displacement")

        if fvg.fvg_after_mss:
            score += 0.75
            reasons.append("FVG formed after MSS")

        if _htf_or_location_supports(candles[candle_position], direction):
            score += 0.75
            reasons.append("HTF or premium/discount context supports MSS direction")

        if entry_use.can_confirm_long_setup or entry_use.can_confirm_short_setup:
            score += 0.5
            reasons.append("MSS can be used as setup confirmation after retracement")

        chop_penalty = _chop_penalty(candles, candle_position, self.config)
        if chop_penalty:
            score -= chop_penalty
            warnings.append("choppy_market")

        if wick_only:
            score = min(score, 4.0)
            warnings.append("aggressive_mss_candidate_low_confidence")
        if not liquidity.sweep_before_mss and not _htf_or_location_supports(candles[candle_position], direction):
            score = min(score, 7.0)
        if not detected:
            score = min(score, 2.0)

        return round(_clamp(score, 0.0, 10.0), 2), reasons, warnings

    def _apply_failure_status(self, candles: Sequence[_MSSCandle], events: Sequence[MSSEvent]) -> tuple[MSSEvent, ...]:
        if self.config.failed_mss_lookahead <= 0:
            return tuple(events)
        position_by_index = {candle.index: position for position, candle in enumerate(candles)}
        updated: list[MSSEvent] = []
        for event in events:
            if not event.detected or event.break_validation.wick_only_break:
                updated.append(event)
                continue
            position = position_by_index.get(event.confirmation_candle.index)
            if position is None:
                updated.append(event)
                continue
            future = candles[position + 1 : position + self.config.failed_mss_lookahead + 1]
            if event.direction == MSSDirection.BULLISH:
                failed = any(candle.close_p < event.broken_level for candle in future)
            else:
                failed = any(candle.close_p > event.broken_level for candle in future)
            updated.append(_replace_status(event, MSSStatus.FAILED) if failed else event)
        return tuple(updated)


def detect_mss(
    df: Any,
    swings: Sequence[DetectedSwingPoint | Mapping[str, Any]],
    liquidity_events: Sequence[MSSLiquidityEvent | Mapping[str, Any]] | None = None,
    *,
    config: MSSDetectionConfig | None = None,
) -> list[dict[str, Any]]:
    """Detect Market Structure Shift events from dataframe-style candles."""

    detector = ICTMSSDetector(config)
    return [event.as_dict() for event in detector.detect(_to_rows(df), swings, liquidity_events or ())]


def _to_rows(df: Any) -> Sequence[Mapping[str, Any]]:
    if hasattr(df, "to_dict"):
        return df.to_dict("records")
    return df


def _first_present(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    if default is not None:
        return default
    raise ValueError(f"Missing required field. Tried: {', '.join(keys)}")


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if value is None:
        raise ValueError("timestamp is required")
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _calculate_atr(candles: Sequence[_MSSCandle], period: int) -> tuple[float, ...]:
    ranges: list[float] = []
    atr_values: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        true_range = candle.range
        if previous_close is not None:
            true_range = max(candle.range, abs(candle.high_p - previous_close), abs(candle.low_p - previous_close))
        ranges.append(max(true_range, 1e-9))
        window = ranges[max(0, len(ranges) - period) :]
        atr_values.append(sum(window) / len(window))
        previous_close = candle.close_p
    return tuple(atr_values)


def _movement_from_text(value: str) -> PreviousMovement:
    text = value.lower()
    if "bearish" in text or "down" in text:
        return PreviousMovement.BEARISH
    if "bullish" in text or "up" in text:
        return PreviousMovement.BULLISH
    return PreviousMovement.UNCLEAR


def _swing_key(swing: DetectedSwingPoint) -> tuple[str, int, float]:
    return swing.type.value, swing.index, swing.price


def _opposite_movement_confirmed(previous: PreviousMovement, direction: MSSDirection) -> bool:
    return (direction == MSSDirection.BULLISH and previous == PreviousMovement.BEARISH) or (
        direction == MSSDirection.BEARISH and previous == PreviousMovement.BULLISH
    )


def _liquidity_context(
    liquidity_events: Sequence[MSSLiquidityEvent],
    candle_index: int,
    direction: MSSDirection,
    config: MSSDetectionConfig,
) -> MSSLiquidityContext:
    desired = "sell_side_sweep" if direction == MSSDirection.BULLISH else "buy_side_sweep"
    candidates = [
        event
        for event in liquidity_events
        if event.valid
        and event.type == desired
        and event.event_index < candle_index
        and candle_index - event.event_index <= config.max_sweep_to_mss_bars
    ]
    if not candidates:
        return MSSLiquidityContext(False)
    event = max(candidates, key=lambda item: (item.event_index, item.strength_score))
    return MSSLiquidityContext(
        sweep_before_mss=True,
        sweep_type=event.type,
        sweep_event_index=event.event_index,
        swept_level=event.swept_level,
        bars_between_sweep_and_mss=candle_index - event.event_index,
        sweep_strength_score=event.strength_score,
    )


def _displacement(
    candle: _MSSCandle, atr: float, direction: MSSDirection, config: MSSDetectionConfig
) -> MSSDisplacement:
    body_ratio = candle.body_to_range_ratio
    range_ratio = candle.range / max(atr, 1e-9)
    if candle.range <= 0:
        close_position_ratio = 0.5
    elif direction == MSSDirection.BULLISH:
        close_position_ratio = (candle.close_p - candle.low_p) / candle.range
    else:
        close_position_ratio = (candle.high_p - candle.close_p) / candle.range

    score = 0.0
    if body_ratio >= config.displacement_body_ratio:
        score += 0.5
    if range_ratio >= config.displacement_range_atr:
        score += 0.5
    if close_position_ratio >= 0.70:
        score += 0.5

    if score >= 1.5 and range_ratio >= 1.5:
        strength = MSSDisplacementStrength.VERY_STRONG
    elif score >= 1.0:
        strength = MSSDisplacementStrength.STRONG
    elif score >= 0.75:
        strength = MSSDisplacementStrength.MODERATE
    elif score > 0:
        strength = MSSDisplacementStrength.WEAK
    else:
        strength = MSSDisplacementStrength.NONE

    close_position = "near_high" if direction == MSSDirection.BULLISH else "near_low"
    if close_position_ratio < 0.70:
        close_position = "middle_or_weak_close"

    return MSSDisplacement(
        displacement_strength=strength,
        displacement_score=round(score, 2),
        body_to_range_ratio=round(body_ratio, 4),
        range_to_atr_ratio=round(range_ratio, 4),
        close_position=close_position,
        volume_confirmation=False,
    )


def _fvg_context(candles: Sequence[_MSSCandle], candle_position: int, direction: MSSDirection) -> MSSFVGContext:
    if candle_position < 2:
        return MSSFVGContext()
    first = candles[candle_position - 2]
    third = candles[candle_position]
    if direction == MSSDirection.BULLISH and first.high_p < third.low_p:
        return MSSFVGContext(
            True, "bullish", fvg_low=first.high_p, fvg_high=third.low_p, used_as_possible_entry_zone=True
        )
    if direction == MSSDirection.BEARISH and first.low_p > third.high_p:
        return MSSFVGContext(
            True, "bearish", fvg_low=third.high_p, fvg_high=first.low_p, used_as_possible_entry_zone=True
        )
    return MSSFVGContext()


def _entry_use(direction: MSSDirection, fvg: MSSFVGContext, liquidity: MSSLiquidityContext) -> MSSEntryConfirmationUse:
    can_confirm = fvg.fvg_after_mss or liquidity.sweep_before_mss
    if direction == MSSDirection.BULLISH:
        return MSSEntryConfirmationUse(
            can_confirm_long_setup=can_confirm,
            recommended_entry_style="wait_for_retracement_to_bullish_fvg_or_order_block",
            invalidation_reference="sweep_low_or_bullish_order_block_low",
            target_reference="buy_side_liquidity_above_recent_swing_high",
            execute_trade_now=False,
        )
    return MSSEntryConfirmationUse(
        can_confirm_short_setup=can_confirm,
        recommended_entry_style="wait_for_retracement_to_bearish_fvg_or_order_block",
        invalidation_reference="sweep_high_or_bearish_order_block_high",
        target_reference="sell_side_liquidity_below_recent_swing_low",
        execute_trade_now=False,
    )


def _htf_or_location_supports(candle: _MSSCandle, direction: MSSDirection) -> bool:
    contexts = " ".join(
        part.lower()
        for part in [candle.htf_context or "", candle.premium_discount_zone or ""]
        if part
    )
    if direction == MSSDirection.BULLISH:
        return "bullish" in contexts or "discount" in contexts or "demand" in contexts
    return "bearish" in contexts or "premium" in contexts or "supply" in contexts


def _chop_penalty(candles: Sequence[_MSSCandle], candle_position: int, config: MSSDetectionConfig) -> float:
    window = candles[max(1, candle_position - config.chop_window + 1) : candle_position + 1]
    if len(window) < 4:
        return 0.0
    overlaps = 0
    for previous, current in zip(window, window[1:]):
        if current.high_p >= previous.low_p and current.low_p <= previous.high_p:
            overlaps += 1
    ratio = overlaps / max(1, len(window) - 1)
    if ratio >= config.chop_overlap_ratio + 0.2:
        return 1.5
    if ratio >= config.chop_overlap_ratio:
        return 0.75
    return 0.0


def _confidence_grade(score: float, detected: bool, wick_only: bool) -> MSSConfidenceGrade:
    if not detected or score <= 2.0:
        return MSSConfidenceGrade.INVALID
    if wick_only or score <= 4.0:
        return MSSConfidenceGrade.WEAK_CHOCH_STYLE
    if score <= 6.5:
        return MSSConfidenceGrade.MODERATE
    if score <= 8.5:
        return MSSConfidenceGrade.STRONG
    return MSSConfidenceGrade.HIGH_QUALITY


def _replace_status(event: MSSEvent, status: MSSStatus) -> MSSEvent:
    warnings = tuple(dict.fromkeys((*event.warnings, "mss_failed_after_reclaim_loss")))
    return MSSEvent(
        concept_name=event.concept_name,
        symbol=event.symbol,
        timeframe=event.timeframe,
        detected=event.detected,
        direction=event.direction,
        previous_movement=event.previous_movement,
        status=status,
        broken_level=event.broken_level,
        broken_swing=event.broken_swing,
        confirmation_candle=event.confirmation_candle,
        break_validation=event.break_validation,
        liquidity_context=event.liquidity_context,
        displacement=event.displacement,
        fvg_context=event.fvg_context,
        entry_confirmation_use=event.entry_confirmation_use,
        confidence_score=min(event.confidence_score, 5.0),
        confidence_grade=_confidence_grade(min(event.confidence_score, 5.0), event.detected, False),
        reasons=event.reasons,
        warnings=warnings,
    )


def _swing_label_from_reasons(swing: DetectedSwingPoint) -> str:
    for reason in swing.reasons:
        for label in ("HH", "HL", "LH", "LL", "EQH", "EQL"):
            if label in reason:
                return label
    return "unknown"


def _liquidity_type_from_text(value: Any) -> SwingLiquidityType:
    text = str(value or "").strip()
    if text == SwingLiquidityType.SELL_SIDE.value:
        return SwingLiquidityType.SELL_SIDE
    return SwingLiquidityType.BUY_SIDE


def _strength_label_from_text(value: Any) -> SwingStrengthLabel:
    text = str(value or SwingStrengthLabel.MINOR.value).strip()
    try:
        return SwingStrengthLabel(text)
    except ValueError:
        return SwingStrengthLabel.MINOR


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))
