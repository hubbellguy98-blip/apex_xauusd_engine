"""Deterministic ICT/SMC swing high and swing low detection.

This module converts closed OHLCV candles into confirmed swing points. It is
kept observer-only so the concept can be reviewed before influencing live
execution decisions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from src.core.domain.market_data import CandleNode


class SwingPointType(str, Enum):
    SWING_HIGH = "swing_high"
    SWING_LOW = "swing_low"


class SwingStrengthLabel(str, Enum):
    WEAK = "weak"
    MINOR = "minor"
    MODERATE = "moderate"
    STRONG = "strong"
    MAJOR = "major"


class SwingLiquidityType(str, Enum):
    BUY_SIDE = "buy_side_liquidity"
    SELL_SIDE = "sell_side_liquidity"


class SwingPointStatus(str, Enum):
    UNSWEPT = "unswept"
    SWEPT = "swept"
    BROKEN = "broken"
    MITIGATED = "mitigated"
    TARGET_HIT = "target_hit"


@dataclass(frozen=True, slots=True)
class SwingDetectionConfig:
    left_bars: int = 3
    right_bars: int = 3
    atr_period: int = 14
    min_atr_reaction: float = 0.5
    strong_atr_reaction: float = 1.5
    min_candle_gap: int = 3
    min_price_distance_atr: float = 0.25
    equal_level_tolerance_atr: float = 0.10
    news_range_atr: float = 3.0
    volume_confirmation_multiplier: float = 1.1
    use_volume_filter: bool = False
    require_min_atr_reaction: bool = False
    timeframe: str | None = None

    def __post_init__(self) -> None:
        if self.left_bars < 1 or self.right_bars < 1:
            raise ValueError("left_bars and right_bars must be positive.")
        if self.atr_period < 1:
            raise ValueError("atr_period must be positive.")
        if self.min_candle_gap < 0:
            raise ValueError("min_candle_gap cannot be negative.")
        if self.min_atr_reaction < 0 or self.strong_atr_reaction < 0:
            raise ValueError("ATR reaction thresholds cannot be negative.")


@dataclass(frozen=True, slots=True)
class DetectedSwingPoint:
    index: int
    timestamp: datetime
    confirmation_index: int
    confirmation_timestamp: datetime
    price: float
    type: SwingPointType
    strength_score: float
    strength_label: SwingStrengthLabel
    timeframe: str
    timeframe_weight: float
    liquidity_type: SwingLiquidityType
    status: SwingPointStatus = SwingPointStatus.UNSWEPT
    used_for: tuple[str, ...] = field(default_factory=tuple)
    atr_reaction: float = 0.0
    distance_from_previous_swing: float | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["type"] = self.type.value
        payload["strength_label"] = self.strength_label.value
        payload["liquidity_type"] = self.liquidity_type.value
        payload["status"] = self.status.value
        return payload


@dataclass(frozen=True, slots=True)
class _OhlcvCandle:
    index: int
    timestamp: datetime
    open_p: float
    high_p: float
    low_p: float
    close_p: float
    volume: float
    timeframe: str
    is_closed: bool = True
    session_name: str | None = None
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


class ICTSwingPointDetector:
    """Finds confirmed non-repainting swing highs and lows."""

    def __init__(self, config: SwingDetectionConfig | None = None) -> None:
        self.config = config or SwingDetectionConfig()

    def detect(
        self, candles: Sequence[CandleNode | Mapping[str, Any]], timeframe: str | None = None
    ) -> tuple[DetectedSwingPoint, ...]:
        normalized = tuple(self._normalize_candles(candles, timeframe))
        closed = tuple(candle for candle in normalized if candle.is_closed)
        if len(closed) < self.config.left_bars + self.config.right_bars + 1:
            return tuple()

        atr_values = _calculate_atr(closed, self.config.atr_period)
        raw_swings = self._collect_raw_swings(closed, atr_values)
        return self._filter_and_rescore(raw_swings, atr_values)

    def _normalize_candles(
        self, candles: Sequence[CandleNode | Mapping[str, Any]], timeframe: str | None
    ) -> list[_OhlcvCandle]:
        normalized: list[_OhlcvCandle] = []
        for fallback_index, candle in enumerate(candles):
            if isinstance(candle, CandleNode):
                normalized.append(
                    _OhlcvCandle(
                        index=candle.sequence_id if candle.sequence_id else fallback_index,
                        timestamp=candle.end_time,
                        open_p=float(candle.open_p),
                        high_p=float(candle.high_p),
                        low_p=float(candle.low_p),
                        close_p=float(candle.close_p),
                        volume=float(candle.volume),
                        timeframe=timeframe or self.config.timeframe or candle.timeframe,
                        is_closed=candle.is_closed,
                    )
                )
                continue

            tf = str(_first_present(candle, "timeframe", default=timeframe or self.config.timeframe or "unknown"))
            normalized.append(
                _OhlcvCandle(
                    index=int(_first_present(candle, "index", default=fallback_index)),
                    timestamp=_coerce_datetime(_first_present(candle, "timestamp", "time", "end_time")),
                    open_p=float(_first_present(candle, "open", "open_p")),
                    high_p=float(_first_present(candle, "high", "high_p")),
                    low_p=float(_first_present(candle, "low", "low_p")),
                    close_p=float(_first_present(candle, "close", "close_p")),
                    volume=float(_first_present(candle, "volume", default=0.0)),
                    timeframe=tf,
                    is_closed=bool(_first_present(candle, "is_closed", default=True)),
                    session_name=_optional_string(candle.get("session_name")),
                    htf_context=_optional_string(candle.get("htf_context")),
                    premium_discount_zone=_optional_string(candle.get("premium_discount_zone")),
                )
            )
        return normalized

    def _collect_raw_swings(
        self, candles: Sequence[_OhlcvCandle], atr_values: Sequence[float]
    ) -> tuple[DetectedSwingPoint, ...]:
        swings: list[DetectedSwingPoint] = []
        first_index = self.config.left_bars
        last_index = len(candles) - self.config.right_bars - 1

        for position in range(first_index, last_index + 1):
            current = candles[position]
            left_window = candles[position - self.config.left_bars : position]
            right_window = candles[position + 1 : position + self.config.right_bars + 1]
            is_high = all(current.high_p > other.high_p for other in left_window + right_window)
            is_low = all(current.low_p < other.low_p for other in left_window + right_window)

            if not is_high and not is_low:
                if _has_equal_high_or_low(current, left_window + right_window, atr_values[position], self.config):
                    continue

            if is_high:
                swings.append(self._build_swing(candles, atr_values, position, SwingPointType.SWING_HIGH))
            if is_low:
                swings.append(self._build_swing(candles, atr_values, position, SwingPointType.SWING_LOW))

        return tuple(swings)

    def _build_swing(
        self, candles: Sequence[_OhlcvCandle], atr_values: Sequence[float], position: int, swing_type: SwingPointType
    ) -> DetectedSwingPoint:
        current = candles[position]
        confirmation_position = position + self.config.right_bars
        atr = max(atr_values[position], 1e-9)
        reaction = self._reaction_after_swing(candles, position, swing_type)
        reaction_atr = reaction / atr
        timeframe = current.timeframe
        score, reasons, warnings = self._score_raw_swing(candles, atr_values, position, swing_type, reaction_atr)
        liquidity_type = (
            SwingLiquidityType.BUY_SIDE if swing_type == SwingPointType.SWING_HIGH else SwingLiquidityType.SELL_SIDE
        )
        used_for = _used_for(swing_type)
        label = _strength_label(score)

        return DetectedSwingPoint(
            index=current.index,
            timestamp=current.timestamp,
            confirmation_index=candles[confirmation_position].index,
            confirmation_timestamp=candles[confirmation_position].timestamp,
            price=current.high_p if swing_type == SwingPointType.SWING_HIGH else current.low_p,
            type=swing_type,
            strength_score=score,
            strength_label=label,
            timeframe=timeframe,
            timeframe_weight=_timeframe_weight(timeframe),
            liquidity_type=liquidity_type,
            used_for=used_for,
            atr_reaction=round(reaction_atr, 4),
            reasons=tuple(reasons),
            warnings=tuple(warnings),
        )

    def _reaction_after_swing(
        self, candles: Sequence[_OhlcvCandle], position: int, swing_type: SwingPointType
    ) -> float:
        lookahead = candles[position + 1 : position + self.config.right_bars + 1]
        if not lookahead:
            return 0.0
        current = candles[position]
        if swing_type == SwingPointType.SWING_HIGH:
            return max(0.0, current.high_p - min(candle.low_p for candle in lookahead))
        return max(0.0, max(candle.high_p for candle in lookahead) - current.low_p)

    def _score_raw_swing(
        self,
        candles: Sequence[_OhlcvCandle],
        atr_values: Sequence[float],
        position: int,
        swing_type: SwingPointType,
        reaction_atr: float,
    ) -> tuple[float, list[str], list[str]]:
        current = candles[position]
        score = 2.0
        reasons = [
            f"Confirmed with {self.config.left_bars} candles left and {self.config.right_bars} candles right",
            "Swing is confirmed only after right-side candles closed",
        ]
        warnings: list[str] = []

        if reaction_atr >= self.config.strong_atr_reaction:
            score += 2.0
            reasons.append(f"Price reacted {reaction_atr:.2f} ATR from the swing")
        elif reaction_atr >= 0.75:
            score += 1.0
            reasons.append(f"Price reacted {reaction_atr:.2f} ATR from the swing")
        elif reaction_atr >= self.config.min_atr_reaction:
            score += 0.5
            reasons.append(f"Price reacted {reaction_atr:.2f} ATR from the swing")
        else:
            warnings.append("ATR reaction is below preferred swing-strength threshold")

        score += 0.75
        reasons.append("Creates objective buy-side/sell-side liquidity reference")

        active_session_score = _session_score(current.session_name)
        if active_session_score:
            score += active_session_score
            reasons.append("Swing formed during an active trading session")
        elif current.session_name:
            warnings.append("Swing formed outside preferred active session")

        volume_score, volume_warning = _volume_score(candles, position, self.config)
        score += volume_score
        if volume_score > 0.5:
            reasons.append("Swing candle volume is above recent average")
        elif volume_warning:
            warnings.append(volume_warning)

        if current.htf_context:
            score += 1.0
            reasons.append("Swing has higher-timeframe context")
        elif current.premium_discount_zone:
            score += 0.5
            reasons.append("Swing has premium/discount range context")

        chop_penalty = _chop_penalty(candles, atr_values, position)
        if chop_penalty:
            score -= chop_penalty
            warnings.append("Nearby candles are overlapping; swing may be internal chop")

        candle_range_atr = current.range / max(atr_values[position], 1e-9)
        if candle_range_atr >= self.config.news_range_atr:
            warnings.append("Swing candle range is unusually large; possible news spike")

        if swing_type == SwingPointType.SWING_HIGH:
            reasons.append("Swing high marks buy-side liquidity above price")
        else:
            reasons.append("Swing low marks sell-side liquidity below price")

        return round(_clamp(score, 0.0, 10.0), 2), reasons, warnings

    def _filter_and_rescore(
        self, raw_swings: Sequence[DetectedSwingPoint], atr_values: Sequence[float]
    ) -> tuple[DetectedSwingPoint, ...]:
        accepted: list[DetectedSwingPoint] = []

        for swing in sorted(raw_swings, key=lambda item: (item.confirmation_index, item.index)):
            if self.config.require_min_atr_reaction and swing.atr_reaction < self.config.min_atr_reaction:
                continue

            if accepted and swing.index - accepted[-1].index < self.config.min_candle_gap:
                if swing.type == accepted[-1].type:
                    accepted[-1] = _prefer_more_extreme(accepted[-1], swing)
                else:
                    accepted[-1] = _prefer_stronger(accepted[-1], swing)
                continue

            if accepted and swing.type == accepted[-1].type:
                accepted[-1] = _prefer_more_extreme(accepted[-1], swing)
                continue

            distance = abs(swing.price - accepted[-1].price) if accepted else None
            if distance is not None:
                swing = _with_distance_score(swing, distance, atr_values, self.config)
            accepted.append(swing)

        return tuple(accepted)


def detect_swings(
    df: Any,
    left_bars: int,
    right_bars: int,
    *,
    timeframe: str | None = None,
    config: SwingDetectionConfig | None = None,
) -> list[dict[str, Any]]:
    """Detect confirmed swing points from a pandas-style dataframe or row list."""

    rows = _to_rows(df)
    base_config = config or SwingDetectionConfig(left_bars=left_bars, right_bars=right_bars, timeframe=timeframe)
    if config is not None:
        base_config = SwingDetectionConfig(
            left_bars=left_bars,
            right_bars=right_bars,
            atr_period=config.atr_period,
            min_atr_reaction=config.min_atr_reaction,
            strong_atr_reaction=config.strong_atr_reaction,
            min_candle_gap=config.min_candle_gap,
            min_price_distance_atr=config.min_price_distance_atr,
            equal_level_tolerance_atr=config.equal_level_tolerance_atr,
            news_range_atr=config.news_range_atr,
            volume_confirmation_multiplier=config.volume_confirmation_multiplier,
            use_volume_filter=config.use_volume_filter,
            require_min_atr_reaction=config.require_min_atr_reaction,
            timeframe=timeframe or config.timeframe,
        )
    detector = ICTSwingPointDetector(base_config)
    return [swing.as_dict() for swing in detector.detect(rows, timeframe=timeframe)]


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
    raise ValueError(f"Missing required candle field. Tried: {', '.join(keys)}")


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


def _calculate_atr(candles: Sequence[_OhlcvCandle], period: int) -> tuple[float, ...]:
    ranges: list[float] = []
    atr_values: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        true_range = candle.range
        if previous_close is not None:
            true_range = max(
                candle.high_p - candle.low_p,
                abs(candle.high_p - previous_close),
                abs(candle.low_p - previous_close),
            )
        ranges.append(max(true_range, 1e-9))
        window = ranges[max(0, len(ranges) - period) :]
        atr_values.append(sum(window) / len(window))
        previous_close = candle.close_p
    return tuple(atr_values)


def _has_equal_high_or_low(
    current: _OhlcvCandle,
    window: Sequence[_OhlcvCandle],
    atr: float,
    config: SwingDetectionConfig,
) -> bool:
    tolerance = max(atr * config.equal_level_tolerance_atr, 1e-9)
    return any(abs(current.high_p - other.high_p) <= tolerance for other in window) or any(
        abs(current.low_p - other.low_p) <= tolerance for other in window
    )


def _timeframe_weight(timeframe: str) -> float:
    normalized = timeframe.lower().replace(" ", "")
    weights = {
        "1m": 0.5,
        "3m": 0.75,
        "5m": 1.0,
        "15m": 1.5,
        "30m": 1.75,
        "1h": 2.0,
        "h1": 2.0,
        "4h": 3.0,
        "h4": 3.0,
        "1d": 4.0,
        "d1": 4.0,
        "daily": 4.0,
        "1w": 5.0,
        "w1": 5.0,
    }
    return weights.get(normalized, 1.0)


def _used_for(swing_type: SwingPointType) -> tuple[str, ...]:
    if swing_type == SwingPointType.SWING_HIGH:
        return ("liquidity", "bos_reference", "target", "range_boundary")
    return ("liquidity", "mss_reference", "stop_loss_reference", "target", "range_boundary")


def _strength_label(score: float) -> SwingStrengthLabel:
    if score <= 2.0:
        return SwingStrengthLabel.WEAK
    if score <= 4.0:
        return SwingStrengthLabel.MINOR
    if score <= 6.0:
        return SwingStrengthLabel.MODERATE
    if score <= 8.5:
        return SwingStrengthLabel.STRONG
    return SwingStrengthLabel.MAJOR


def _session_score(session_name: str | None) -> float:
    if not session_name:
        return 0.5
    normalized = session_name.lower()
    if "london" in normalized or "newyork" in normalized or "overlap" in normalized:
        return 1.0
    if "asian" in normalized or "asia" in normalized:
        return 0.5
    return 0.0


def _volume_score(
    candles: Sequence[_OhlcvCandle], position: int, config: SwingDetectionConfig
) -> tuple[float, str | None]:
    if position == 0:
        return 0.5, None
    window = candles[max(0, position - 20) : position]
    average_volume = sum(candle.volume for candle in window) / len(window) if window else 0.0
    current_volume = candles[position].volume
    if average_volume <= 0:
        return 0.5, None
    if current_volume >= average_volume * config.volume_confirmation_multiplier:
        return 1.0, None
    if config.use_volume_filter:
        return 0.0, "Swing candle volume is below preferred confirmation threshold"
    return 0.5, None


def _chop_penalty(candles: Sequence[_OhlcvCandle], atr_values: Sequence[float], position: int) -> float:
    local = candles[max(0, position - 2) : min(len(candles), position + 3)]
    if len(local) < 3:
        return 0.0
    atr = max(atr_values[position], 1e-9)
    overlapping = sum(1 for candle in local if candle.range < atr * 0.45 or candle.body_to_range_ratio < 0.20)
    if overlapping >= 4:
        return 1.5
    if overlapping >= 2:
        return 0.5
    return 0.0


def _prefer_more_extreme(left: DetectedSwingPoint, right: DetectedSwingPoint) -> DetectedSwingPoint:
    if left.type == SwingPointType.SWING_HIGH:
        return right if right.price > left.price else left
    return right if right.price < left.price else left


def _prefer_stronger(left: DetectedSwingPoint, right: DetectedSwingPoint) -> DetectedSwingPoint:
    return right if right.strength_score > left.strength_score else left


def _with_distance_score(
    swing: DetectedSwingPoint,
    distance: float,
    atr_values: Sequence[float],
    config: SwingDetectionConfig,
) -> DetectedSwingPoint:
    atr_index = min(max(swing.confirmation_index, 0), len(atr_values) - 1)
    distance_atr = distance / max(atr_values[atr_index], 1e-9)
    bonus = 1.0 if distance_atr >= 1.0 else 0.5 if distance_atr >= 0.25 else 0.0
    warnings = list(swing.warnings)
    reasons = list(swing.reasons)
    if bonus:
        reasons.append(f"Clean distance from previous accepted swing ({distance_atr:.2f} ATR)")
    else:
        warnings.append("Swing is very close to previous accepted opposite swing")
    if distance_atr < config.min_price_distance_atr:
        warnings.append("Swing is below configured minimum ATR distance from previous swing")
    score = round(_clamp(swing.strength_score + bonus, 0.0, 10.0), 2)
    return DetectedSwingPoint(
        index=swing.index,
        timestamp=swing.timestamp,
        confirmation_index=swing.confirmation_index,
        confirmation_timestamp=swing.confirmation_timestamp,
        price=swing.price,
        type=swing.type,
        strength_score=score,
        strength_label=_strength_label(score),
        timeframe=swing.timeframe,
        timeframe_weight=swing.timeframe_weight,
        liquidity_type=swing.liquidity_type,
        status=swing.status,
        used_for=swing.used_for,
        atr_reaction=swing.atr_reaction,
        distance_from_previous_swing=round(distance, 5),
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))
