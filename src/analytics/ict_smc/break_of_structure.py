"""Rule-based ICT/SMC Break of Structure detection.

The detector consumes closed candles and pre-confirmed swing points. It does
not create structure from raw highs/lows, which keeps BOS detection
non-repainting and separate from live execution until reviewed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from src.analytics.ict_smc.swing_points import DetectedSwingPoint, SwingPointStatus, SwingPointType
from src.core.domain.market_data import CandleNode


class BOSDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class BOSBreakType(str, Enum):
    BULLISH_BOS = "bullish_BOS"
    BEARISH_BOS = "bearish_BOS"
    WEAK_BULLISH_BOS = "weak_bullish_BOS"
    WEAK_BEARISH_BOS = "weak_bearish_BOS"
    AGGRESSIVE_BULLISH_CANDIDATE = "aggressive_bullish_BOS_candidate"
    AGGRESSIVE_BEARISH_CANDIDATE = "aggressive_bearish_BOS_candidate"
    BULLISH_UNCLASSIFIED = "bullish_structure_break_unclassified"
    BEARISH_UNCLASSIFIED = "bearish_structure_break_unclassified"
    POSSIBLE_BULLISH_MSS_OR_CHOCH = "possible_bullish_MSS_or_CHoCH"
    POSSIBLE_BEARISH_MSS_OR_CHOCH = "possible_bearish_MSS_or_CHoCH"
    BUY_SIDE_WICK_BREAK_ONLY = "buy_side_wick_break_only"
    SELL_SIDE_WICK_BREAK_ONLY = "sell_side_wick_break_only"


class BOSScope(str, Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class BOSStatus(str, Enum):
    CONFIRMED = "confirmed"
    UNCONFIRMED_WICK_BREAK = "unconfirmed_wick_break"
    PENDING_RETEST = "pending_retest"
    CONTINUED = "continued"
    FAILED = "failed"
    INVALIDATED = "invalidated"


class BOSConfidenceGrade(str, Enum):
    INVALID = "invalid"
    LOW = "low"
    MODERATE = "moderate"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True, slots=True)
class BOSDetectionConfig:
    minimum_swing_strength: float = 4.0
    external_swing_strength: float = 7.0
    break_buffer_atr_multiplier: float = 0.10
    close_required: bool = True
    displacement_body_ratio: float = 0.55
    displacement_range_atr: float = 1.0
    failed_bos_lookahead: int = 3
    chop_window: int = 8
    chop_overlap_ratio: float = 0.60
    atr_period: int = 14
    trend_state: str | None = None
    timeframe: str | None = None

    def __post_init__(self) -> None:
        if self.minimum_swing_strength < 0:
            raise ValueError("minimum_swing_strength cannot be negative.")
        if self.break_buffer_atr_multiplier < 0:
            raise ValueError("break_buffer_atr_multiplier cannot be negative.")
        if self.failed_bos_lookahead < 0:
            raise ValueError("failed_bos_lookahead cannot be negative.")
        if self.atr_period < 1:
            raise ValueError("atr_period must be positive.")


@dataclass(frozen=True, slots=True)
class BOSDisplacement:
    present: bool
    body_to_range_ratio: float
    range_to_atr_ratio: float
    close_position: str
    score: float


@dataclass(frozen=True, slots=True)
class BOSFVGContext:
    fvg_created: bool = False
    fvg_direction: str = "none"
    fvg_low: float | None = None
    fvg_high: float | None = None


@dataclass(frozen=True, slots=True)
class BOSOrderBlockContext:
    order_block_validated: bool = False
    order_block_direction: str = "none"
    source_candle_type: str = "none"
    order_block_low: float | None = None
    order_block_high: float | None = None
    order_block_candle_index: int | None = None
    order_block_caused_bos: bool = False


@dataclass(frozen=True, slots=True)
class BOSLiquidityContext:
    broken_level_liquidity_type: str
    liquidity_taken: bool
    prior_opposite_sweep: bool = False
    swept_level: float | None = None


@dataclass(frozen=True, slots=True)
class BOSBrokenSwing:
    type: str
    index: int
    timestamp: datetime
    confirmation_index: int
    confirmation_timestamp: datetime
    price: float
    strength_score: float
    timeframe: str


@dataclass(frozen=True, slots=True)
class BOSConfirmationCandle:
    index: int
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class BOSBreakValidation:
    required_level: float
    break_buffer: float
    actual_close: float
    actual_high: float
    actual_low: float
    close_beyond_required_level: bool
    wick_beyond_required_level: bool


@dataclass(frozen=True, slots=True)
class BOSEvent:
    concept_name: str
    symbol: str
    timeframe: str
    detected: bool
    direction: BOSDirection
    break_type: BOSBreakType
    bos_scope: BOSScope
    status: BOSStatus
    close_required: bool
    aggressive_mode: bool
    wick_break_only: bool
    broken_swing: BOSBrokenSwing
    confirmation_candle: BOSConfirmationCandle
    break_validation: BOSBreakValidation
    previous_trend_state: str
    bos_as_trend_continuation: bool
    displacement: BOSDisplacement
    fvg_context: BOSFVGContext
    order_block_context: BOSOrderBlockContext
    liquidity_context: BOSLiquidityContext
    quality_score: float
    confidence_grade: BOSConfidenceGrade
    reasons: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["direction"] = self.direction.value
        payload["break_type"] = self.break_type.value
        payload["bos_scope"] = self.bos_scope.value
        payload["status"] = self.status.value
        payload["confidence_grade"] = self.confidence_grade.value
        return payload


@dataclass(frozen=True, slots=True)
class _BOSCandle:
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

    @property
    def range(self) -> float:
        return max(0.0, self.high_p - self.low_p)

    @property
    def body(self) -> float:
        return abs(self.close_p - self.open_p)

    @property
    def body_to_range_ratio(self) -> float:
        return 0.0 if self.range <= 0 else self.body / self.range


class ICTBOSDetector:
    """Detects BOS events from closed candles and confirmed swing points."""

    def __init__(self, config: BOSDetectionConfig | None = None) -> None:
        self.config = config or BOSDetectionConfig()

    def detect(
        self,
        candles: Sequence[CandleNode | Mapping[str, Any]],
        swings: Sequence[DetectedSwingPoint | Mapping[str, Any]],
    ) -> tuple[BOSEvent, ...]:
        normalized_candles = tuple(candle for candle in self._normalize_candles(candles) if candle.is_closed)
        normalized_swings = tuple(self._normalize_swings(swings))
        if not normalized_candles or not normalized_swings:
            return tuple()

        atr_values = _calculate_atr(normalized_candles, self.config.atr_period)
        events: list[BOSEvent] = []
        broken_swing_keys: set[tuple[str, int, float]] = set()

        for candle_position, candle in enumerate(normalized_candles):
            high = self._latest_valid_swing(
                normalized_swings, candle.index, SwingPointType.SWING_HIGH, broken_swing_keys
            )
            low = self._latest_valid_swing(
                normalized_swings, candle.index, SwingPointType.SWING_LOW, broken_swing_keys
            )
            atr = atr_values[candle_position]
            if high is not None:
                event = self._evaluate_break(
                    normalized_candles, atr_values, candle_position, high, BOSDirection.BULLISH, atr
                )
                if event is not None:
                    events.append(event)
                    if event.detected and not event.wick_break_only:
                        broken_swing_keys.add(_swing_key(high))
            if low is not None:
                event = self._evaluate_break(
                    normalized_candles, atr_values, candle_position, low, BOSDirection.BEARISH, atr
                )
                if event is not None:
                    events.append(event)
                    if event.detected and not event.wick_break_only:
                        broken_swing_keys.add(_swing_key(low))

        return tuple(self._apply_failed_bos_status(normalized_candles, events))

    def _normalize_candles(self, candles: Sequence[CandleNode | Mapping[str, Any]]) -> list[_BOSCandle]:
        normalized: list[_BOSCandle] = []
        for fallback_index, candle in enumerate(candles):
            if isinstance(candle, CandleNode):
                normalized.append(
                    _BOSCandle(
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
                _BOSCandle(
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

    def _latest_valid_swing(
        self,
        swings: Sequence[DetectedSwingPoint],
        candle_index: int,
        swing_type: SwingPointType,
        broken_keys: set[tuple[str, int, float]],
    ) -> DetectedSwingPoint | None:
        candidates = [
            swing
            for swing in swings
            if swing.type == swing_type
            and swing.confirmation_index < candle_index
            and swing.index < candle_index
            and swing.strength_score >= self.config.minimum_swing_strength
            and swing.status != SwingPointStatus.BROKEN
            and _swing_key(swing) not in broken_keys
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda swing: (swing.confirmation_index, swing.strength_score))

    def _evaluate_break(
        self,
        candles: Sequence[_BOSCandle],
        atr_values: Sequence[float],
        candle_position: int,
        swing: DetectedSwingPoint,
        direction: BOSDirection,
        atr: float,
    ) -> BOSEvent | None:
        candle = candles[candle_position]
        buffer = max(0.0, atr * self.config.break_buffer_atr_multiplier)
        if direction == BOSDirection.BULLISH:
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
                candles,
                atr_values,
                candle_position,
                swing,
                direction,
                buffer,
                required_level,
                detected=False,
                wick_break_only=True,
            )
        if not close_break and not self.config.close_required:
            return self._build_event(
                candles,
                atr_values,
                candle_position,
                swing,
                direction,
                buffer,
                required_level,
                detected=True,
                wick_break_only=True,
            )
        return self._build_event(
            candles,
            atr_values,
            candle_position,
            swing,
            direction,
            buffer,
            required_level,
            detected=True,
            wick_break_only=False,
        )

    def _build_event(
        self,
        candles: Sequence[_BOSCandle],
        atr_values: Sequence[float],
        candle_position: int,
        swing: DetectedSwingPoint,
        direction: BOSDirection,
        buffer: float,
        required_level: float,
        *,
        detected: bool,
        wick_break_only: bool,
    ) -> BOSEvent:
        candle = candles[candle_position]
        previous_trend = self.config.trend_state or candle.trend_state or "unknown"
        continuation = _is_trend_continuation(previous_trend, direction)
        displacement = _displacement(candle, atr_values[candle_position], direction, self.config)
        fvg = _fvg_context(candles, candle_position, direction)
        order_block = _order_block_context(candles, candle_position, direction, displacement.present)
        liquidity = BOSLiquidityContext(
            broken_level_liquidity_type=swing.liquidity_type.value,
            liquidity_taken=detected or wick_break_only,
            prior_opposite_sweep=_has_prior_opposite_sweep(candles, candle_position, swing, direction),
        )
        score, reasons, warnings = self._quality_score(
            candles, atr_values, candle_position, swing, direction, detected, wick_break_only, continuation,
            displacement, fvg, order_block, liquidity, previous_trend
        )
        break_type = _break_type(direction, detected, wick_break_only, continuation, score, self.config.close_required)
        status = _status(detected, wick_break_only, self.config.close_required)

        return BOSEvent(
            concept_name="Break of Structure",
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            detected=detected,
            direction=direction,
            break_type=break_type,
            bos_scope=(
                BOSScope.EXTERNAL
                if swing.strength_score >= self.config.external_swing_strength
                else BOSScope.INTERNAL
            ),
            status=status,
            close_required=self.config.close_required,
            aggressive_mode=not self.config.close_required,
            wick_break_only=wick_break_only,
            broken_swing=BOSBrokenSwing(
                type=swing.type.value,
                index=swing.index,
                timestamp=swing.timestamp,
                confirmation_index=swing.confirmation_index,
                confirmation_timestamp=swing.confirmation_timestamp,
                price=swing.price,
                strength_score=swing.strength_score,
                timeframe=swing.timeframe,
            ),
            confirmation_candle=BOSConfirmationCandle(
                index=candle.index,
                timestamp=candle.timestamp,
                open=candle.open_p,
                high=candle.high_p,
                low=candle.low_p,
                close=candle.close_p,
                volume=candle.volume,
            ),
            break_validation=BOSBreakValidation(
                required_level=round(required_level, 5),
                break_buffer=round(buffer, 5),
                actual_close=candle.close_p,
                actual_high=candle.high_p,
                actual_low=candle.low_p,
                close_beyond_required_level=detected and not wick_break_only,
                wick_beyond_required_level=wick_break_only or detected,
            ),
            previous_trend_state=previous_trend,
            bos_as_trend_continuation=continuation,
            displacement=displacement,
            fvg_context=fvg,
            order_block_context=order_block,
            liquidity_context=liquidity,
            quality_score=score,
            confidence_grade=_confidence_grade(score, detected),
            reasons=tuple(reasons),
            warnings=tuple(warnings),
        )

    def _quality_score(
        self,
        candles: Sequence[_BOSCandle],
        atr_values: Sequence[float],
        candle_position: int,
        swing: DetectedSwingPoint,
        direction: BOSDirection,
        detected: bool,
        wick_break_only: bool,
        continuation: bool,
        displacement: BOSDisplacement,
        fvg: BOSFVGContext,
        order_block: BOSOrderBlockContext,
        liquidity: BOSLiquidityContext,
        previous_trend: str,
    ) -> tuple[float, list[str], list[str]]:
        reasons: list[str] = []
        warnings: list[str] = []
        score = 0.0

        if swing.strength_score >= self.config.external_swing_strength:
            score += 2.0
            reasons.append("Broken swing is strong enough for external BOS")
        elif swing.strength_score >= self.config.minimum_swing_strength:
            score += 1.0
            reasons.append("Broken swing is confirmed but internal/minor")
            warnings.append("weak_swing_reference")

        if detected and not wick_break_only:
            score += 2.0
            reasons.append("Confirmed candle close beyond previous valid swing level")
        elif wick_break_only:
            score += 0.5
            warnings.append("wick_only_break_not_bos")
            warnings.append("Could be liquidity sweep rather than confirmed BOS")

        if continuation:
            score += 1.0
            reasons.append("Break aligns with existing trend continuation")
        elif previous_trend == "unknown":
            score += 0.5
            warnings.append("Trend context unknown; BOS is less reliable")
        else:
            warnings.append("Break is against prior trend and may be MSS or CHoCH")

        score += displacement.score
        if displacement.present:
            reasons.append("Break candle shows displacement")
        else:
            warnings.append("low_displacement")

        if fvg.fvg_created:
            score += 1.0
            reasons.append("FVG formed during the break")

        if order_block.order_block_validated:
            score += 1.0
            reasons.append("Last opposite candle before displacement can validate an order block")
        elif order_block.source_candle_type != "none":
            score += 0.5
            reasons.append("Possible order block source candle found")

        if liquidity.prior_opposite_sweep:
            score += 0.75
            reasons.append("Prior opposite-side sweep improves BOS context")
        elif liquidity.liquidity_taken:
            score += 0.5
            reasons.append("Broken swing level liquidity was taken")

        if _htf_aligned(candles[candle_position], direction):
            score += 0.75
            reasons.append("Higher-timeframe context supports the break")

        chop_penalty = _chop_penalty(candles, candle_position, self.config)
        if chop_penalty:
            score -= chop_penalty
            warnings.append("choppy_market")
            warnings.append("false_bos_risk")

        if wick_break_only:
            score = min(score, 4.5)
            warnings.append("aggressive_wick_based_bos_low_confidence")
        if not detected:
            score = min(score, 2.0)

        return round(_clamp(score, 0.0, 10.0), 2), reasons, warnings

    def _apply_failed_bos_status(
        self, candles: Sequence[_BOSCandle], events: Sequence[BOSEvent]
    ) -> tuple[BOSEvent, ...]:
        if self.config.failed_bos_lookahead <= 0:
            return tuple(events)
        by_index = {candle.index: position for position, candle in enumerate(candles)}
        updated: list[BOSEvent] = []
        for event in events:
            if not event.detected or event.wick_break_only:
                updated.append(event)
                continue
            position = by_index.get(event.confirmation_candle.index)
            if position is None:
                updated.append(event)
                continue
            future = candles[position + 1 : position + self.config.failed_bos_lookahead + 1]
            failed = False
            if event.direction == BOSDirection.BULLISH:
                failed = any(candle.close_p < event.broken_swing.price for candle in future)
            else:
                failed = any(candle.close_p > event.broken_swing.price for candle in future)
            if failed:
                updated.append(_replace_status(event, BOSStatus.FAILED, "Price closed back inside broken structure"))
            else:
                updated.append(event)
        return tuple(updated)


def detect_bos(
    df: Any,
    swings: Sequence[DetectedSwingPoint | Mapping[str, Any]],
    close_required: bool = True,
    *,
    config: BOSDetectionConfig | None = None,
) -> list[dict[str, Any]]:
    """Detect BOS events from dataframe-style candles and confirmed swings."""

    rows = _to_rows(df)
    base = config or BOSDetectionConfig(close_required=close_required)
    if config is not None:
        base = BOSDetectionConfig(
            minimum_swing_strength=config.minimum_swing_strength,
            external_swing_strength=config.external_swing_strength,
            break_buffer_atr_multiplier=config.break_buffer_atr_multiplier,
            close_required=close_required,
            displacement_body_ratio=config.displacement_body_ratio,
            displacement_range_atr=config.displacement_range_atr,
            failed_bos_lookahead=config.failed_bos_lookahead,
            chop_window=config.chop_window,
            chop_overlap_ratio=config.chop_overlap_ratio,
            atr_period=config.atr_period,
            trend_state=config.trend_state,
            timeframe=config.timeframe,
        )
    detector = ICTBOSDetector(base)
    return [event.as_dict() for event in detector.detect(rows, swings)]


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


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if value is None:
        raise ValueError("timestamp is required")
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _calculate_atr(candles: Sequence[_BOSCandle], period: int) -> tuple[float, ...]:
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


def _swing_key(swing: DetectedSwingPoint) -> tuple[str, int, float]:
    return swing.type.value, swing.index, swing.price


def _is_trend_continuation(trend_state: str, direction: BOSDirection) -> bool:
    normalized = trend_state.lower()
    return (direction == BOSDirection.BULLISH and "bullish" in normalized) or (
        direction == BOSDirection.BEARISH and "bearish" in normalized
    )


def _break_type(
    direction: BOSDirection,
    detected: bool,
    wick_break_only: bool,
    continuation: bool,
    score: float,
    close_required: bool,
) -> BOSBreakType:
    if wick_break_only and not close_required:
        return (
            BOSBreakType.AGGRESSIVE_BULLISH_CANDIDATE
            if direction == BOSDirection.BULLISH
            else BOSBreakType.AGGRESSIVE_BEARISH_CANDIDATE
        )
    if wick_break_only:
        return (
            BOSBreakType.BUY_SIDE_WICK_BREAK_ONLY
            if direction == BOSDirection.BULLISH
            else BOSBreakType.SELL_SIDE_WICK_BREAK_ONLY
        )
    if not continuation and detected:
        return (
            BOSBreakType.POSSIBLE_BULLISH_MSS_OR_CHOCH
            if direction == BOSDirection.BULLISH
            else BOSBreakType.POSSIBLE_BEARISH_MSS_OR_CHOCH
        )
    if score < 5.0:
        return BOSBreakType.WEAK_BULLISH_BOS if direction == BOSDirection.BULLISH else BOSBreakType.WEAK_BEARISH_BOS
    return BOSBreakType.BULLISH_BOS if direction == BOSDirection.BULLISH else BOSBreakType.BEARISH_BOS


def _status(detected: bool, wick_break_only: bool, close_required: bool) -> BOSStatus:
    if wick_break_only:
        return BOSStatus.UNCONFIRMED_WICK_BREAK
    if detected and close_required:
        return BOSStatus.CONFIRMED
    if detected:
        return BOSStatus.PENDING_RETEST
    return BOSStatus.INVALIDATED


def _displacement(
    candle: _BOSCandle, atr: float, direction: BOSDirection, config: BOSDetectionConfig
) -> BOSDisplacement:
    body_ratio = candle.body_to_range_ratio
    range_ratio = candle.range / max(atr, 1e-9)
    if candle.range <= 0:
        close_position_ratio = 0.5
    elif direction == BOSDirection.BULLISH:
        close_position_ratio = (candle.close_p - candle.low_p) / candle.range
    else:
        close_position_ratio = (candle.high_p - candle.close_p) / candle.range
    close_position = "near_high" if direction == BOSDirection.BULLISH else "near_low"
    if close_position_ratio < 0.70:
        close_position = "middle_or_weak_close"
    score = 0.0
    if body_ratio >= config.displacement_body_ratio:
        score += 0.5
    if range_ratio >= config.displacement_range_atr:
        score += 0.5
    if close_position_ratio >= 0.70:
        score += 0.5
    return BOSDisplacement(
        present=score >= 1.0,
        body_to_range_ratio=round(body_ratio, 4),
        range_to_atr_ratio=round(range_ratio, 4),
        close_position=close_position,
        score=round(score, 2),
    )


def _fvg_context(candles: Sequence[_BOSCandle], candle_position: int, direction: BOSDirection) -> BOSFVGContext:
    if candle_position < 2:
        return BOSFVGContext()
    first = candles[candle_position - 2]
    third = candles[candle_position]
    if direction == BOSDirection.BULLISH and first.high_p < third.low_p:
        return BOSFVGContext(True, "bullish", fvg_low=first.high_p, fvg_high=third.low_p)
    if direction == BOSDirection.BEARISH and first.low_p > third.high_p:
        return BOSFVGContext(True, "bearish", fvg_low=third.high_p, fvg_high=first.low_p)
    return BOSFVGContext()


def _order_block_context(
    candles: Sequence[_BOSCandle], candle_position: int, direction: BOSDirection, displacement_present: bool
) -> BOSOrderBlockContext:
    lookback = candles[max(0, candle_position - 6) : candle_position]
    for candle in reversed(lookback):
        bearish_candle = candle.close_p < candle.open_p
        bullish_candle = candle.close_p > candle.open_p
        if direction == BOSDirection.BULLISH and bearish_candle:
            return BOSOrderBlockContext(
                order_block_validated=displacement_present,
                order_block_direction="bullish",
                source_candle_type="last_bearish_candle_before_displacement",
                order_block_low=candle.low_p,
                order_block_high=candle.high_p,
                order_block_candle_index=candle.index,
                order_block_caused_bos=displacement_present,
            )
        if direction == BOSDirection.BEARISH and bullish_candle:
            return BOSOrderBlockContext(
                order_block_validated=displacement_present,
                order_block_direction="bearish",
                source_candle_type="last_bullish_candle_before_displacement",
                order_block_low=candle.low_p,
                order_block_high=candle.high_p,
                order_block_candle_index=candle.index,
                order_block_caused_bos=displacement_present,
            )
    return BOSOrderBlockContext()


def _has_prior_opposite_sweep(
    candles: Sequence[_BOSCandle], candle_position: int, swing: DetectedSwingPoint, direction: BOSDirection
) -> bool:
    lookback = candles[max(0, candle_position - 6) : candle_position]
    if direction == BOSDirection.BULLISH:
        return any(candle.low_p < swing.price and candle.close_p > swing.price for candle in lookback)
    return any(candle.high_p > swing.price and candle.close_p < swing.price for candle in lookback)


def _htf_aligned(candle: _BOSCandle, direction: BOSDirection) -> bool:
    if not candle.htf_context:
        return False
    normalized = candle.htf_context.lower()
    return (direction == BOSDirection.BULLISH and "bullish" in normalized) or (
        direction == BOSDirection.BEARISH and "bearish" in normalized
    )


def _chop_penalty(candles: Sequence[_BOSCandle], candle_position: int, config: BOSDetectionConfig) -> float:
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


def _confidence_grade(score: float, detected: bool) -> BOSConfidenceGrade:
    if not detected or score <= 2.0:
        return BOSConfidenceGrade.INVALID
    if score <= 4.5:
        return BOSConfidenceGrade.LOW
    if score <= 6.5:
        return BOSConfidenceGrade.MODERATE
    if score <= 8.5:
        return BOSConfidenceGrade.STRONG
    return BOSConfidenceGrade.HIGH_QUALITY


def _replace_status(event: BOSEvent, status: BOSStatus, warning: str) -> BOSEvent:
    warnings = tuple(dict.fromkeys((*event.warnings, warning, "failed_bos_risk")))
    return BOSEvent(
        concept_name=event.concept_name,
        symbol=event.symbol,
        timeframe=event.timeframe,
        detected=event.detected,
        direction=event.direction,
        break_type=event.break_type,
        bos_scope=event.bos_scope,
        status=status,
        close_required=event.close_required,
        aggressive_mode=event.aggressive_mode,
        wick_break_only=event.wick_break_only,
        broken_swing=event.broken_swing,
        confirmation_candle=event.confirmation_candle,
        break_validation=event.break_validation,
        previous_trend_state=event.previous_trend_state,
        bos_as_trend_continuation=event.bos_as_trend_continuation,
        displacement=event.displacement,
        fvg_context=event.fvg_context,
        order_block_context=event.order_block_context,
        liquidity_context=event.liquidity_context,
        quality_score=min(event.quality_score, 5.0),
        confidence_grade=_confidence_grade(min(event.quality_score, 5.0), event.detected),
        reasons=event.reasons,
        warnings=warnings,
    )


def _liquidity_type_from_text(value: Any):
    from src.analytics.ict_smc.swing_points import SwingLiquidityType

    text = str(value or "").strip()
    if text == SwingLiquidityType.SELL_SIDE.value:
        return SwingLiquidityType.SELL_SIDE
    return SwingLiquidityType.BUY_SIDE


def _strength_label_from_text(value: Any):
    from src.analytics.ict_smc.swing_points import SwingStrengthLabel

    text = str(value or SwingStrengthLabel.MINOR.value).strip()
    try:
        return SwingStrengthLabel(text)
    except ValueError:
        return SwingStrengthLabel.MINOR


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))
