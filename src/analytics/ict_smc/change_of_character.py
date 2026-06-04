"""Rule-based ICT/SMC Change of Character detection.

CHoCH is modeled as an early warning that the current short-term behavior may
be weakening. It is intentionally weaker than MSS and must not authorize live
entries by itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from src.analytics.ict_smc.swing_points import (
    DetectedSwingPoint,
    SwingPointStatus,
    SwingPointType,
)
from src.core.domain.market_data import CandleNode


class CHoCHDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class CHoCHPreviousMovement(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    UNCLEAR = "unclear"


class CHoCHStatus(str, Enum):
    CONFIRMED = "confirmed"
    WICK_ONLY_CANDIDATE = "wick_only_candidate"
    FAILED = "failed"
    UPGRADED_TO_MSS_CANDIDATE = "upgraded_to_mss_candidate"


class CHoCHDisplacementStrength(str, Enum):
    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class CHoCHConfidenceGrade(str, Enum):
    INVALID = "invalid"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG_WARNING = "strong_warning"
    MSS_CANDIDATE = "mss_candidate"


@dataclass(frozen=True, slots=True)
class CHoCHDetectionConfig:
    minimum_swing_strength: float = 3.5
    strong_swing_strength: float = 5.0
    mss_upgrade_swing_strength: float = 6.5
    break_buffer_atr_multiplier: float = 0.08
    close_required: bool = True
    allow_wick_candidates: bool = True
    max_sweep_to_choch_bars: int = 25
    failed_choch_lookahead: int = 3
    displacement_body_ratio: float = 0.52
    displacement_range_atr: float = 0.85
    chop_window: int = 8
    chop_overlap_ratio: float = 0.65
    atr_period: int = 14
    previous_movement: str | None = None
    timeframe: str | None = None

    def __post_init__(self) -> None:
        if self.minimum_swing_strength < 0:
            raise ValueError("minimum_swing_strength cannot be negative.")
        if self.break_buffer_atr_multiplier < 0:
            raise ValueError("break_buffer_atr_multiplier cannot be negative.")
        if self.max_sweep_to_choch_bars < 0:
            raise ValueError("max_sweep_to_choch_bars cannot be negative.")
        if self.atr_period < 1:
            raise ValueError("atr_period must be positive.")


@dataclass(frozen=True, slots=True)
class CHoCHLiquidityEvent:
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
class CHoCHBrokenSwing:
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
class CHoCHConfirmationCandle:
    index: int
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class CHoCHBreakValidation:
    close_required: bool
    break_buffer: float
    required_level: float
    actual_close: float
    candle_close_confirmed: bool
    wick_only_break: bool


@dataclass(frozen=True, slots=True)
class CHoCHLiquidityContext:
    sweep_before_choch: bool
    sweep_type: str = "none"
    sweep_event_index: int | None = None
    swept_level: float | None = None
    bars_between_sweep_and_choch: int | None = None
    sweep_strength_score: float = 0.0


@dataclass(frozen=True, slots=True)
class CHoCHDisplacement:
    displacement_strength: CHoCHDisplacementStrength
    displacement_score: float
    body_to_range_ratio: float
    range_to_atr_ratio: float
    close_position: str


@dataclass(frozen=True, slots=True)
class CHoCHFVGContext:
    fvg_after_choch: bool = False
    fvg_direction: str = "none"
    fvg_low: float | None = None
    fvg_high: float | None = None


@dataclass(frozen=True, slots=True)
class CHoCHSignalUsage:
    warning_signal: bool = True
    entry_allowed: bool = False
    recommended_action: str = "wait_for_mss_or_entry_model_confirmation"
    possible_upgrade: str = "none"


@dataclass(frozen=True, slots=True)
class CHoCHEvent:
    concept_name: str
    symbol: str
    timeframe: str
    detected: bool
    direction: CHoCHDirection
    previous_movement: CHoCHPreviousMovement
    status: CHoCHStatus
    broken_level: float
    broken_swing: CHoCHBrokenSwing
    confirmation_candle: CHoCHConfirmationCandle
    break_validation: CHoCHBreakValidation
    liquidity_context: CHoCHLiquidityContext
    displacement: CHoCHDisplacement
    fvg_context: CHoCHFVGContext
    signal_usage: CHoCHSignalUsage
    quality_score: float
    confidence_grade: CHoCHConfidenceGrade
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
class _CHoCHCandle:
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


class ICTCHoCHDetector:
    """Detects early Change of Character warnings from closed candles."""

    def __init__(self, config: CHoCHDetectionConfig | None = None) -> None:
        self.config = config or CHoCHDetectionConfig()

    def detect(
        self,
        candles: Sequence[CandleNode | Mapping[str, Any]],
        swings: Sequence[DetectedSwingPoint | Mapping[str, Any]],
        liquidity_events: Sequence[CHoCHLiquidityEvent | Mapping[str, Any]] | None = None,
    ) -> tuple[CHoCHEvent, ...]:
        closed = tuple(candle for candle in self._normalize_candles(candles) if candle.is_closed)
        normalized_swings = tuple(self._normalize_swings(swings))
        normalized_liquidity = tuple(self._normalize_liquidity_events(liquidity_events or ()))
        if not closed or not normalized_swings:
            return tuple()

        atr_values = _calculate_atr(closed, self.config.atr_period)
        used_swing_keys: set[tuple[str, int, float]] = set()
        events: list[CHoCHEvent] = []

        for position, candle in enumerate(closed):
            previous_movement = self._previous_movement(closed, position)

            if previous_movement in (CHoCHPreviousMovement.BEARISH, CHoCHPreviousMovement.UNCLEAR):
                event = self._evaluate_break(
                    closed=closed,
                    swings=normalized_swings,
                    liquidity_events=normalized_liquidity,
                    atr_values=atr_values,
                    position=position,
                    direction=CHoCHDirection.BULLISH,
                    previous_movement=previous_movement,
                    used_swing_keys=used_swing_keys,
                )
                if event:
                    events.append(event)

            if previous_movement in (CHoCHPreviousMovement.BULLISH, CHoCHPreviousMovement.UNCLEAR):
                event = self._evaluate_break(
                    closed=closed,
                    swings=normalized_swings,
                    liquidity_events=normalized_liquidity,
                    atr_values=atr_values,
                    position=position,
                    direction=CHoCHDirection.BEARISH,
                    previous_movement=previous_movement,
                    used_swing_keys=used_swing_keys,
                )
                if event:
                    events.append(event)

        return tuple(events)

    def _evaluate_break(
        self,
        closed: Sequence[_CHoCHCandle],
        swings: Sequence[DetectedSwingPoint],
        liquidity_events: Sequence[CHoCHLiquidityEvent],
        atr_values: Sequence[float],
        position: int,
        direction: CHoCHDirection,
        previous_movement: CHoCHPreviousMovement,
        used_swing_keys: set[tuple[str, int, float]],
    ) -> CHoCHEvent | None:
        candle = closed[position]
        swing_type = SwingPointType.SWING_HIGH if direction == CHoCHDirection.BULLISH else SwingPointType.SWING_LOW
        candidate = _latest_confirmed_swing(swings, swing_type, candle.index, self.config)
        if candidate is None:
            return None

        swing_key = (candidate.type.value, candidate.index, round(candidate.price, 8))
        if swing_key in used_swing_keys:
            return None

        atr = atr_values[position] if position < len(atr_values) else 0.0
        break_buffer = max(0.0, atr * self.config.break_buffer_atr_multiplier)
        if direction == CHoCHDirection.BULLISH:
            required_level = candidate.price + break_buffer
            close_confirmed = candle.close_p > required_level
            wick_only = candle.high_p > required_level and not close_confirmed
        else:
            required_level = candidate.price - break_buffer
            close_confirmed = candle.close_p < required_level
            wick_only = candle.low_p < required_level and not close_confirmed

        if not close_confirmed and not (wick_only and self.config.allow_wick_candidates):
            return None

        used_swing_keys.add(swing_key)
        liquidity_context = self._liquidity_context(closed, position, direction, liquidity_events)
        displacement = self._displacement(candle, atr, direction)
        fvg_context = self._fvg_context(closed, position, direction)
        status = CHoCHStatus.CONFIRMED if close_confirmed else CHoCHStatus.WICK_ONLY_CANDIDATE
        detected = close_confirmed
        reasons, warnings = self._base_reasons_and_warnings(
            candidate, close_confirmed, wick_only, liquidity_context, displacement, fvg_context
        )
        quality_score = self._quality_score(
            candidate, close_confirmed, liquidity_context, displacement, fvg_context, closed, position
        )

        if close_confirmed and self._failed_after_break(closed, position, candidate.price, direction):
            status = CHoCHStatus.FAILED
            warnings.append("choch_failed_after_reclaim_loss")
        possible_upgrade = "none"
        if (
            close_confirmed
            and liquidity_context.sweep_before_choch
            and displacement.displacement_strength
            in (CHoCHDisplacementStrength.STRONG, CHoCHDisplacementStrength.MODERATE)
            and candidate.strength_score >= self.config.mss_upgrade_swing_strength
        ):
            status = CHoCHStatus.UPGRADED_TO_MSS_CANDIDATE
            possible_upgrade = "mss_candidate"
            reasons.append("choch_has_sweep_displacement_and_strong_level")

        grade = _confidence_grade(quality_score, status)
        usage = CHoCHSignalUsage(
            warning_signal=True,
            entry_allowed=False,
            recommended_action="wait_for_mss_or_entry_model_confirmation",
            possible_upgrade=possible_upgrade,
        )

        return CHoCHEvent(
            concept_name="CHoCH",
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            detected=detected,
            direction=direction,
            previous_movement=previous_movement,
            status=status,
            broken_level=candidate.price,
            broken_swing=CHoCHBrokenSwing(
                type=candidate.type.value,
                index=candidate.index,
                timestamp=candidate.timestamp,
                confirmation_index=candidate.confirmation_index,
                confirmation_timestamp=candidate.confirmation_timestamp,
                price=candidate.price,
                swing_label=_swing_label(candidate),
                strength_score=candidate.strength_score,
                timeframe=candidate.timeframe,
            ),
            confirmation_candle=CHoCHConfirmationCandle(
                index=candle.index,
                timestamp=candle.timestamp,
                open=candle.open_p,
                high=candle.high_p,
                low=candle.low_p,
                close=candle.close_p,
                volume=candle.volume,
            ),
            break_validation=CHoCHBreakValidation(
                close_required=self.config.close_required,
                break_buffer=round(break_buffer, 6),
                required_level=round(required_level, 6),
                actual_close=candle.close_p,
                candle_close_confirmed=close_confirmed,
                wick_only_break=wick_only,
            ),
            liquidity_context=liquidity_context,
            displacement=displacement,
            fvg_context=fvg_context,
            signal_usage=usage,
            quality_score=round(quality_score, 2),
            confidence_grade=grade,
            reasons=tuple(reasons),
            warnings=tuple(warnings),
        )

    def _normalize_candles(self, candles: Sequence[CandleNode | Mapping[str, Any]]) -> list[_CHoCHCandle]:
        normalized: list[_CHoCHCandle] = []
        for fallback_index, candle in enumerate(candles):
            if isinstance(candle, CandleNode):
                normalized.append(
                    _CHoCHCandle(
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
                _CHoCHCandle(
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

    def _normalize_swings(self, swings: Sequence[DetectedSwingPoint | Mapping[str, Any]]) -> list[DetectedSwingPoint]:
        normalized: list[DetectedSwingPoint] = []
        for swing in swings:
            if isinstance(swing, DetectedSwingPoint):
                normalized.append(swing)
                continue
            normalized.append(_swing_from_mapping(swing))
        return normalized

    def _normalize_liquidity_events(
        self, events: Sequence[CHoCHLiquidityEvent | Mapping[str, Any]]
    ) -> list[CHoCHLiquidityEvent]:
        normalized: list[CHoCHLiquidityEvent] = []
        for event in events:
            if isinstance(event, CHoCHLiquidityEvent):
                normalized.append(event)
                continue
            normalized.append(
                CHoCHLiquidityEvent(
                    event_index=int(_first_present(event, "event_index", "index")),
                    timestamp=_coerce_datetime(_first_present(event, "timestamp", "time")),
                    type=str(_first_present(event, "type", "event_type", default="unknown")),
                    swept_level=float(_first_present(event, "swept_level", "level", default=0.0)),
                    swept_swing_index=_optional_int(event.get("swept_swing_index")),
                    direction=str(_first_present(event, "direction", default="unknown")),
                    valid=bool(_first_present(event, "valid", default=True)),
                    strength_score=float(_first_present(event, "strength_score", "score", default=0.0)),
                    sweep_candle_high=_optional_float(event.get("sweep_candle_high")),
                    sweep_candle_low=_optional_float(event.get("sweep_candle_low")),
                    sweep_candle_close=_optional_float(event.get("sweep_candle_close")),
                )
            )
        return normalized

    def _previous_movement(self, candles: Sequence[_CHoCHCandle], position: int) -> CHoCHPreviousMovement:
        if self.config.previous_movement:
            return _previous_movement_from_text(self.config.previous_movement)
        trend_state = candles[position].trend_state
        if trend_state:
            return _previous_movement_from_text(trend_state)
        window = candles[max(0, position - 4) : position + 1]
        if len(window) < 3:
            return CHoCHPreviousMovement.UNCLEAR
        if window[-1].close_p > window[0].close_p and window[-1].low_p >= min(c.low_p for c in window[:-1]):
            return CHoCHPreviousMovement.BULLISH
        if window[-1].close_p < window[0].close_p and window[-1].high_p <= max(c.high_p for c in window[:-1]):
            return CHoCHPreviousMovement.BEARISH
        return CHoCHPreviousMovement.UNCLEAR

    def _liquidity_context(
        self,
        candles: Sequence[_CHoCHCandle],
        position: int,
        direction: CHoCHDirection,
        liquidity_events: Sequence[CHoCHLiquidityEvent],
    ) -> CHoCHLiquidityContext:
        expected_type = "sell_side_sweep" if direction == CHoCHDirection.BULLISH else "buy_side_sweep"
        candle = candles[position]
        relevant = [
            event
            for event in liquidity_events
            if event.valid
            and event.type == expected_type
            and 0 <= candle.index - event.event_index <= self.config.max_sweep_to_choch_bars
        ]
        if relevant:
            event = max(relevant, key=lambda item: item.event_index)
            return CHoCHLiquidityContext(
                sweep_before_choch=True,
                sweep_type=event.type,
                sweep_event_index=event.event_index,
                swept_level=event.swept_level,
                bars_between_sweep_and_choch=candle.index - event.event_index,
                sweep_strength_score=event.strength_score,
            )
        return CHoCHLiquidityContext(sweep_before_choch=False)

    def _displacement(self, candle: _CHoCHCandle, atr: float, direction: CHoCHDirection) -> CHoCHDisplacement:
        atr_base = max(atr, 1e-9)
        range_to_atr = candle.range / atr_base
        body_ratio = candle.body_to_range_ratio
        if direction == CHoCHDirection.BULLISH:
            close_position = "near_high" if candle.range and (candle.high_p - candle.close_p) / candle.range <= 0.25 else "mid"
            directional = candle.close_p > candle.open_p
        else:
            close_position = "near_low" if candle.range and (candle.close_p - candle.low_p) / candle.range <= 0.25 else "mid"
            directional = candle.close_p < candle.open_p

        score = 0.0
        if directional:
            score += 2.0
        if body_ratio >= self.config.displacement_body_ratio:
            score += 2.0
        if range_to_atr >= self.config.displacement_range_atr:
            score += 2.0
        if close_position in {"near_high", "near_low"}:
            score += 1.0

        if score >= 6.0:
            strength = CHoCHDisplacementStrength.STRONG
        elif score >= 4.0:
            strength = CHoCHDisplacementStrength.MODERATE
        elif score >= 2.0:
            strength = CHoCHDisplacementStrength.WEAK
        else:
            strength = CHoCHDisplacementStrength.NONE
        return CHoCHDisplacement(
            displacement_strength=strength,
            displacement_score=round(score, 2),
            body_to_range_ratio=round(body_ratio, 4),
            range_to_atr_ratio=round(range_to_atr, 4),
            close_position=close_position,
        )

    def _fvg_context(
        self, candles: Sequence[_CHoCHCandle], position: int, direction: CHoCHDirection
    ) -> CHoCHFVGContext:
        if position < 2:
            return CHoCHFVGContext()
        first = candles[position - 2]
        third = candles[position]
        if direction == CHoCHDirection.BULLISH and first.high_p < third.low_p:
            return CHoCHFVGContext(True, "bullish", first.high_p, third.low_p)
        if direction == CHoCHDirection.BEARISH and first.low_p > third.high_p:
            return CHoCHFVGContext(True, "bearish", third.high_p, first.low_p)
        return CHoCHFVGContext()

    def _base_reasons_and_warnings(
        self,
        swing: DetectedSwingPoint,
        close_confirmed: bool,
        wick_only: bool,
        liquidity: CHoCHLiquidityContext,
        displacement: CHoCHDisplacement,
        fvg_context: CHoCHFVGContext,
    ) -> tuple[list[str], list[str]]:
        reasons = ["confirmed_swing_level_used", "choch_is_warning_not_entry"]
        warnings = ["entry_not_allowed_without_mss_or_entry_model"]
        if close_confirmed:
            reasons.append("candle_close_broke_valid_swing_level")
        if wick_only:
            warnings.append("wick_only_choch_candidate_not_confirmed")
        if swing.strength_score < self.config.strong_swing_strength:
            warnings.append("weak_internal_swing_level")
        if liquidity.sweep_before_choch:
            reasons.append("liquidity_sweep_before_choch")
        else:
            warnings.append("no_liquidity_sweep_before_choch")
        if displacement.displacement_strength in (CHoCHDisplacementStrength.MODERATE, CHoCHDisplacementStrength.STRONG):
            reasons.append("directional_displacement_present")
        else:
            warnings.append("displacement_not_strong_enough")
        if fvg_context.fvg_after_choch:
            reasons.append("fvg_context_present")
        return reasons, warnings

    def _quality_score(
        self,
        swing: DetectedSwingPoint,
        close_confirmed: bool,
        liquidity: CHoCHLiquidityContext,
        displacement: CHoCHDisplacement,
        fvg_context: CHoCHFVGContext,
        candles: Sequence[_CHoCHCandle],
        position: int,
    ) -> float:
        score = 1.0
        score += min(2.0, swing.strength_score / 4.0)
        score += 1.5 if close_confirmed else -1.0
        score += 1.5 if liquidity.sweep_before_choch else -0.5
        score += min(2.0, displacement.displacement_score / 3.0)
        score += 0.8 if fvg_context.fvg_after_choch else 0.0
        if _is_choppy(candles, position, self.config):
            score -= 1.0
        return _clamp(score, 0.0, 10.0)

    def _failed_after_break(
        self, candles: Sequence[_CHoCHCandle], position: int, level: float, direction: CHoCHDirection
    ) -> bool:
        lookahead = candles[position + 1 : position + 1 + self.config.failed_choch_lookahead]
        if direction == CHoCHDirection.BULLISH:
            return any(candle.close_p < level for candle in lookahead)
        return any(candle.close_p > level for candle in lookahead)


def detect_choch(
    df: Sequence[CandleNode | Mapping[str, Any]],
    swings: Sequence[DetectedSwingPoint | Mapping[str, Any]],
    liquidity_events: Sequence[CHoCHLiquidityEvent | Mapping[str, Any]] | None = None,
    **config_overrides: Any,
) -> list[dict[str, Any]]:
    """Convenience helper returning JSON-friendly CHoCH dictionaries."""

    config = CHoCHDetectionConfig(**config_overrides) if config_overrides else CHoCHDetectionConfig()
    detector = ICTCHoCHDetector(config)
    return [event.as_dict() for event in detector.detect(df, swings, liquidity_events)]


def _latest_confirmed_swing(
    swings: Sequence[DetectedSwingPoint],
    swing_type: SwingPointType,
    candle_index: int,
    config: CHoCHDetectionConfig,
) -> DetectedSwingPoint | None:
    candidates = [
        swing
        for swing in swings
        if swing.type == swing_type
        and swing.confirmation_index < candle_index
        and swing.strength_score >= config.minimum_swing_strength
        and swing.status in {SwingPointStatus.UNSWEPT, SwingPointStatus.SWEPT}
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda swing: (swing.confirmation_index, swing.strength_score))


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
        strength_label=_strength_label_from_text(_first_present(swing, "strength_label", default="weak")),
        timeframe=str(_first_present(swing, "timeframe", default="unknown")),
        timeframe_weight=float(_first_present(swing, "timeframe_weight", default=1.0)),
        liquidity_type=_liquidity_type_from_text(_first_present(swing, "liquidity_type", default=liquidity_default)),
        status=SwingPointStatus(str(_first_present(swing, "status", default=SwingPointStatus.UNSWEPT.value))),
        used_for=tuple(_first_present(swing, "used_for", default=())),
        atr_reaction=float(_first_present(swing, "atr_reaction", default=0.0)),
        distance_from_previous_swing=_optional_float(swing.get("distance_from_previous_swing")),
        reasons=tuple(_first_present(swing, "reasons", default=())),
        warnings=tuple(_first_present(swing, "warnings", default=())),
    )


def _calculate_atr(candles: Sequence[_CHoCHCandle], period: int) -> list[float]:
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


def _is_choppy(candles: Sequence[_CHoCHCandle], position: int, config: CHoCHDetectionConfig) -> bool:
    window = candles[max(0, position - config.chop_window + 1) : position + 1]
    if len(window) < 4:
        return False
    total_range = max(c.high_p for c in window) - min(c.low_p for c in window)
    body_sum = sum(c.body for c in window)
    if total_range <= 0:
        return True
    return body_sum / total_range < config.chop_overlap_ratio


def _confidence_grade(score: float, status: CHoCHStatus) -> CHoCHConfidenceGrade:
    if status == CHoCHStatus.WICK_ONLY_CANDIDATE:
        return CHoCHConfidenceGrade.WEAK
    if status == CHoCHStatus.UPGRADED_TO_MSS_CANDIDATE:
        return CHoCHConfidenceGrade.MSS_CANDIDATE
    if status == CHoCHStatus.FAILED:
        return CHoCHConfidenceGrade.INVALID
    if score >= 7.0:
        return CHoCHConfidenceGrade.STRONG_WARNING
    if score >= 5.0:
        return CHoCHConfidenceGrade.MODERATE
    return CHoCHConfidenceGrade.WEAK


def _previous_movement_from_text(value: str) -> CHoCHPreviousMovement:
    lowered = value.lower()
    if "bear" in lowered or "down" in lowered or "sell" in lowered:
        return CHoCHPreviousMovement.BEARISH
    if "bull" in lowered or "up" in lowered or "buy" in lowered:
        return CHoCHPreviousMovement.BULLISH
    return CHoCHPreviousMovement.UNCLEAR


def _swing_label(swing: DetectedSwingPoint) -> str:
    for reason in swing.reasons:
        upper = str(reason).upper()
        for label in ("HH", "HL", "LH", "LL", "EQH", "EQL"):
            if label in upper:
                return label
    return "internal_high" if swing.type == SwingPointType.SWING_HIGH else "internal_low"


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


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _strength_label_from_text(value: Any):
    from src.analytics.ict_smc.swing_points import SwingStrengthLabel

    return SwingStrengthLabel(str(value))


def _liquidity_type_from_text(value: Any):
    from src.analytics.ict_smc.swing_points import SwingLiquidityType

    return SwingLiquidityType(str(value))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
