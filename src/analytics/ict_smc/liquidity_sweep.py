"""Rule-based ICT/SMC liquidity sweep detection.

This detector consumes closed candles and pre-detected liquidity pools. It
separates sweeps from accepted breakouts and keeps sweep-only entry disabled.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from src.analytics.ict_smc.liquidity import (
    LiquidityDirection,
    LiquidityPool,
    LiquidityPriceZone,
    LiquidityQualityGrade,
    LiquidityStatus,
    LiquidityType,
)
from src.core.domain.market_data import CandleNode


class LiquiditySweepDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"


class LiquiditySweepType(str, Enum):
    SELL_SIDE_SWEEP = "sell_side_liquidity_sweep"
    BUY_SIDE_SWEEP = "buy_side_liquidity_sweep"
    NONE = "none"


class LiquiditySweepClassification(str, Enum):
    SWEEP_NOT_BREAKOUT = "sweep_not_breakout"
    BEARISH_BREAKOUT_OR_CONTINUATION = "bearish_breakout_or_continuation"
    BULLISH_BREAKOUT_OR_CONTINUATION = "bullish_breakout_or_continuation"
    TOUCH_ONLY = "touch_only"


class LiquiditySweepReclaimType(str, Enum):
    WEAK_RECLAIM = "weak_reclaim"
    MID_RECLAIM = "mid_reclaim"
    FULL_RECLAIM = "full_reclaim"
    WEAK_REJECTION = "weak_rejection"
    MID_REJECTION = "mid_rejection"
    FULL_REJECTION = "full_rejection"
    NONE = "none"


class LiquiditySweepSetupStatus(str, Enum):
    CONTEXT_ONLY = "context_only"
    UNCONFIRMED_OR_STALE = "unconfirmed_or_stale"
    CONFIRMED_BY_CHOCH = "confirmed_by_choch"
    CONFIRMED_BY_MSS = "confirmed_by_mss"
    BREAKOUT_CONTINUATION = "breakout_continuation"
    FAILED = "failed"


class LiquiditySweepRejectionStrength(str, Enum):
    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class LiquiditySweepDisplacementStrength(str, Enum):
    NONE = "none"
    MODERATE = "moderate"
    STRONG = "strong"


class LiquiditySweepConfidenceGrade(str, Enum):
    INVALID = "invalid"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True, slots=True)
class LiquiditySweepDetectionConfig:
    minimum_liquidity_quality: float = 3.0
    sweep_buffer_atr_multiplier: float = 0.02
    break_buffer_atr_multiplier: float = 0.05
    min_wick_ratio: float = 0.25
    strong_wick_ratio: float = 0.40
    displacement_body_ratio: float = 0.55
    displacement_range_atr: float = 0.85
    max_confirmation_bars: int = 10
    atr_period: int = 14
    include_breakout_events: bool = True
    timeframe: str | None = None

    def __post_init__(self) -> None:
        if self.minimum_liquidity_quality < 0:
            raise ValueError("minimum_liquidity_quality cannot be negative.")
        if self.sweep_buffer_atr_multiplier < 0 or self.break_buffer_atr_multiplier < 0:
            raise ValueError("ATR multipliers cannot be negative.")
        if self.max_confirmation_bars < 0:
            raise ValueError("max_confirmation_bars cannot be negative.")
        if self.atr_period < 1:
            raise ValueError("atr_period must be positive.")


@dataclass(frozen=True, slots=True)
class SweptLiquidityReference:
    liquidity_id: str
    liquidity_type: str
    direction: str
    timeframe: str
    quality_score: float
    price_zone: LiquidityPriceZone
    touched_count: int
    swept_status_before: str


@dataclass(frozen=True, slots=True)
class LiquiditySweepCandle:
    index: int
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class LiquiditySweepValidation:
    traded_beyond_liquidity: bool
    sweep_extreme_price: float
    penetration_distance: float
    close_reclaimed: bool
    reclaim_type: LiquiditySweepReclaimType
    classified_as: LiquiditySweepClassification


@dataclass(frozen=True, slots=True)
class LiquiditySweepRejectionQuality:
    wick_present: bool
    wick_ratio: float
    close_position: str
    rejection_strength: LiquiditySweepRejectionStrength


@dataclass(frozen=True, slots=True)
class LiquiditySweepPostConfirmation:
    mss_after_sweep: bool = False
    mss_direction: str = "none"
    mss_candle_index: int | None = None
    bars_from_sweep_to_mss: int | None = None
    choch_after_sweep: bool = False
    choch_candle_index: int | None = None
    displacement_after_sweep: bool = False
    displacement_strength: LiquiditySweepDisplacementStrength = LiquiditySweepDisplacementStrength.NONE
    fvg_after_sweep: bool = False
    order_block_after_sweep: bool = False


@dataclass(frozen=True, slots=True)
class LiquiditySweepEntryLogic:
    entry_allowed_from_sweep_alone: bool = False
    entry_allowed_after_confirmation: bool = False
    recommended_entry_style: str = "wait_for_mss_then_retracement_to_fvg_or_order_block"
    invalidation_reference: str = "sweep_extreme"
    target_reference: str = "opposite_side_liquidity"


@dataclass(frozen=True, slots=True)
class LiquiditySweepEvent:
    concept_name: str
    symbol: str
    timeframe: str
    detected: bool
    direction: LiquiditySweepDirection
    sweep_type: LiquiditySweepType
    swept_liquidity: SweptLiquidityReference
    sweep_candle: LiquiditySweepCandle
    sweep_validation: LiquiditySweepValidation
    rejection_quality: LiquiditySweepRejectionQuality
    post_sweep_confirmation: LiquiditySweepPostConfirmation
    entry_logic: LiquiditySweepEntryLogic
    quality_score: float
    confidence_grade: LiquiditySweepConfidenceGrade
    setup_status: LiquiditySweepSetupStatus
    reasons: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["direction"] = self.direction.value
        payload["sweep_type"] = self.sweep_type.value
        payload["sweep_validation"]["reclaim_type"] = self.sweep_validation.reclaim_type.value
        payload["sweep_validation"]["classified_as"] = self.sweep_validation.classified_as.value
        payload["rejection_quality"]["rejection_strength"] = self.rejection_quality.rejection_strength.value
        payload["post_sweep_confirmation"][
            "displacement_strength"
        ] = self.post_sweep_confirmation.displacement_strength.value
        payload["confidence_grade"] = self.confidence_grade.value
        payload["setup_status"] = self.setup_status.value
        return payload


@dataclass(frozen=True, slots=True)
class _SweepCandle:
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
    def upper_wick(self) -> float:
        return max(0.0, self.high_p - max(self.open_p, self.close_p))

    @property
    def lower_wick(self) -> float:
        return max(0.0, min(self.open_p, self.close_p) - self.low_p)


class ICTLiquiditySweepDetector:
    """Detects liquidity sweeps and rejects accepted breakouts."""

    def __init__(self, config: LiquiditySweepDetectionConfig | None = None) -> None:
        self.config = config or LiquiditySweepDetectionConfig()

    def detect(
        self,
        candles: Sequence[CandleNode | Mapping[str, Any]],
        liquidity_pools: Sequence[LiquidityPool | Mapping[str, Any]],
        mss_events: Sequence[Mapping[str, Any]] | None = None,
        choch_events: Sequence[Mapping[str, Any]] | None = None,
    ) -> tuple[LiquiditySweepEvent, ...]:
        closed = tuple(candle for candle in self._normalize_candles(candles) if candle.is_closed)
        pools = tuple(self._normalize_pools(liquidity_pools))
        if not closed or not pools:
            return tuple()
        atr_values = _calculate_atr(closed, self.config.atr_period)
        events: list[LiquiditySweepEvent] = []
        seen: set[tuple[str, int, str]] = set()
        consumed_pool_ids: set[str] = set()

        for position, candle in enumerate(closed):
            atr = atr_values[position] if position < len(atr_values) else _average_range(closed)
            sweep_buffer = atr * self.config.sweep_buffer_atr_multiplier
            break_buffer = atr * self.config.break_buffer_atr_multiplier
            for pool in pools:
                if pool.liquidity_id in consumed_pool_ids:
                    continue
                if not self._pool_is_eligible(pool, candle.index):
                    continue
                event = self._evaluate_pool(
                    closed, position, candle, pool, sweep_buffer, break_buffer, atr, mss_events or (), choch_events or ()
                )
                if event is None:
                    continue
                key = (pool.liquidity_id, candle.index, event.sweep_validation.classified_as.value)
                if key in seen:
                    continue
                seen.add(key)
                consumed_pool_ids.add(pool.liquidity_id)
                events.append(event)
        return tuple(events)

    def _evaluate_pool(
        self,
        candles: Sequence[_SweepCandle],
        position: int,
        candle: _SweepCandle,
        pool: LiquidityPool,
        sweep_buffer: float,
        break_buffer: float,
        atr: float,
        mss_events: Sequence[Mapping[str, Any]],
        choch_events: Sequence[Mapping[str, Any]],
    ) -> LiquiditySweepEvent | None:
        if pool.direction == LiquidityDirection.SELL_SIDE:
            traded_beyond = candle.low_p < pool.zone_low - sweep_buffer
            if not traded_beyond:
                return None
            if candle.close_p < pool.zone_low - break_buffer:
                if not self.config.include_breakout_events:
                    return None
                return self._build_event(
                    candles,
                    position,
                    candle,
                    pool,
                    LiquiditySweepDirection.NONE,
                    LiquiditySweepType.NONE,
                    LiquiditySweepClassification.BEARISH_BREAKOUT_OR_CONTINUATION,
                    LiquiditySweepReclaimType.NONE,
                    candle.low_p,
                    pool.zone_low - candle.low_p,
                    False,
                    atr,
                    mss_events,
                    choch_events,
                )
            if candle.close_p > pool.zone_low:
                reclaim = _bullish_reclaim_type(candle.close_p, pool)
                return self._build_event(
                    candles,
                    position,
                    candle,
                    pool,
                    LiquiditySweepDirection.BULLISH,
                    LiquiditySweepType.SELL_SIDE_SWEEP,
                    LiquiditySweepClassification.SWEEP_NOT_BREAKOUT,
                    reclaim,
                    candle.low_p,
                    pool.zone_low - candle.low_p,
                    True,
                    atr,
                    mss_events,
                    choch_events,
                )
            return None

        traded_beyond = candle.high_p > pool.zone_high + sweep_buffer
        if not traded_beyond:
            return None
        if candle.close_p > pool.zone_high + break_buffer:
            if not self.config.include_breakout_events:
                return None
            return self._build_event(
                candles,
                position,
                candle,
                pool,
                LiquiditySweepDirection.NONE,
                LiquiditySweepType.NONE,
                LiquiditySweepClassification.BULLISH_BREAKOUT_OR_CONTINUATION,
                LiquiditySweepReclaimType.NONE,
                candle.high_p,
                candle.high_p - pool.zone_high,
                False,
                atr,
                mss_events,
                choch_events,
            )
        if candle.close_p < pool.zone_high:
            reclaim = _bearish_reclaim_type(candle.close_p, pool)
            return self._build_event(
                candles,
                position,
                candle,
                pool,
                LiquiditySweepDirection.BEARISH,
                LiquiditySweepType.BUY_SIDE_SWEEP,
                LiquiditySweepClassification.SWEEP_NOT_BREAKOUT,
                reclaim,
                candle.high_p,
                candle.high_p - pool.zone_high,
                True,
                atr,
                mss_events,
                choch_events,
            )
        return None

    def _build_event(
        self,
        candles: Sequence[_SweepCandle],
        position: int,
        candle: _SweepCandle,
        pool: LiquidityPool,
        direction: LiquiditySweepDirection,
        sweep_type: LiquiditySweepType,
        classification: LiquiditySweepClassification,
        reclaim_type: LiquiditySweepReclaimType,
        extreme_price: float,
        penetration_distance: float,
        close_reclaimed: bool,
        atr: float,
        mss_events: Sequence[Mapping[str, Any]],
        choch_events: Sequence[Mapping[str, Any]],
    ) -> LiquiditySweepEvent:
        rejection = _rejection_quality(candle, direction, self.config)
        confirmation = self._post_confirmation(candles, position, direction, atr, mss_events, choch_events)
        reasons, warnings = self._reasons_warnings(pool, classification, reclaim_type, rejection, confirmation)
        score = self._quality_score(pool, classification, reclaim_type, rejection, confirmation, candle, atr)
        setup_status = _setup_status(classification, confirmation, direction)
        entry_logic = _entry_logic(direction, confirmation, classification)
        detected = classification == LiquiditySweepClassification.SWEEP_NOT_BREAKOUT

        return LiquiditySweepEvent(
            concept_name="Liquidity Sweep",
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            detected=detected,
            direction=direction,
            sweep_type=sweep_type,
            swept_liquidity=SweptLiquidityReference(
                liquidity_id=pool.liquidity_id,
                liquidity_type=pool.liquidity_type.value,
                direction=pool.direction.value,
                timeframe=pool.timeframe,
                quality_score=pool.quality_score,
                price_zone=pool.price_zone,
                touched_count=pool.touched_count,
                swept_status_before=pool.swept_status.value,
            ),
            sweep_candle=LiquiditySweepCandle(
                index=candle.index,
                timestamp=candle.timestamp,
                open=candle.open_p,
                high=candle.high_p,
                low=candle.low_p,
                close=candle.close_p,
                volume=candle.volume,
            ),
            sweep_validation=LiquiditySweepValidation(
                traded_beyond_liquidity=True,
                sweep_extreme_price=extreme_price,
                penetration_distance=round(max(0.0, penetration_distance), 6),
                close_reclaimed=close_reclaimed,
                reclaim_type=reclaim_type,
                classified_as=classification,
            ),
            rejection_quality=rejection,
            post_sweep_confirmation=confirmation,
            entry_logic=entry_logic,
            quality_score=round(score, 2),
            confidence_grade=_confidence_grade(score, detected),
            setup_status=setup_status,
            reasons=tuple(reasons),
            warnings=tuple(warnings),
        )

    def _post_confirmation(
        self,
        candles: Sequence[_SweepCandle],
        position: int,
        direction: LiquiditySweepDirection,
        atr: float,
        mss_events: Sequence[Mapping[str, Any]],
        choch_events: Sequence[Mapping[str, Any]],
    ) -> LiquiditySweepPostConfirmation:
        if direction == LiquiditySweepDirection.NONE:
            return LiquiditySweepPostConfirmation()
        window = candles[position + 1 : position + 1 + self.config.max_confirmation_bars]
        mss = _first_confirmation_event(mss_events, direction.value, candles[position].index, self.config.max_confirmation_bars)
        choch = _first_confirmation_event(
            choch_events, direction.value, candles[position].index, self.config.max_confirmation_bars
        )
        displacement_strength = self._displacement_strength(window, direction, atr)
        fvg_after_sweep = _fvg_after_sweep(candles, position, direction)
        return LiquiditySweepPostConfirmation(
            mss_after_sweep=mss is not None,
            mss_direction=direction.value if mss else "none",
            mss_candle_index=_event_index(mss),
            bars_from_sweep_to_mss=(_event_index(mss) - candles[position].index) if mss and _event_index(mss) else None,
            choch_after_sweep=choch is not None,
            choch_candle_index=_event_index(choch),
            displacement_after_sweep=displacement_strength != LiquiditySweepDisplacementStrength.NONE,
            displacement_strength=displacement_strength,
            fvg_after_sweep=fvg_after_sweep,
            order_block_after_sweep=False,
        )

    def _displacement_strength(
        self, window: Sequence[_SweepCandle], direction: LiquiditySweepDirection, atr: float
    ) -> LiquiditySweepDisplacementStrength:
        if not window:
            return LiquiditySweepDisplacementStrength.NONE
        atr_base = max(atr, 1e-9)
        for candle in window:
            directional = (
                candle.close_p > candle.open_p
                if direction == LiquiditySweepDirection.BULLISH
                else candle.close_p < candle.open_p
            )
            body_ratio = 0.0 if candle.range <= 0 else candle.body / candle.range
            range_ratio = candle.range / atr_base
            if directional and body_ratio >= self.config.displacement_body_ratio and range_ratio >= self.config.displacement_range_atr:
                return LiquiditySweepDisplacementStrength.STRONG
            if directional and body_ratio >= self.config.displacement_body_ratio * 0.8:
                return LiquiditySweepDisplacementStrength.MODERATE
        return LiquiditySweepDisplacementStrength.NONE

    def _quality_score(
        self,
        pool: LiquidityPool,
        classification: LiquiditySweepClassification,
        reclaim_type: LiquiditySweepReclaimType,
        rejection: LiquiditySweepRejectionQuality,
        confirmation: LiquiditySweepPostConfirmation,
        candle: _SweepCandle,
        atr: float,
    ) -> float:
        if classification != LiquiditySweepClassification.SWEEP_NOT_BREAKOUT:
            return 1.5
        score = min(2.0, pool.quality_score / 5.0)
        score += 1.0 if rejection.wick_present else 0.0
        score += min(1.0, rejection.wick_ratio / max(self.config.strong_wick_ratio, 1e-9))
        if reclaim_type in (LiquiditySweepReclaimType.FULL_RECLAIM, LiquiditySweepReclaimType.FULL_REJECTION):
            score += 2.0
        elif reclaim_type in (LiquiditySweepReclaimType.MID_RECLAIM, LiquiditySweepReclaimType.MID_REJECTION):
            score += 1.5
        elif reclaim_type in (LiquiditySweepReclaimType.WEAK_RECLAIM, LiquiditySweepReclaimType.WEAK_REJECTION):
            score += 1.0
        if confirmation.mss_after_sweep:
            score += 1.5
        if confirmation.choch_after_sweep:
            score += 0.75
        if confirmation.displacement_strength == LiquiditySweepDisplacementStrength.STRONG:
            score += 1.0
        elif confirmation.displacement_strength == LiquiditySweepDisplacementStrength.MODERATE:
            score += 0.5
        if confirmation.fvg_after_sweep:
            score += 0.5
        if candle.range > max(atr, 1e-9) * 3.0:
            score -= 1.0
        if not confirmation.mss_after_sweep and not confirmation.choch_after_sweep:
            score = min(score, 6.0)
        return _clamp(score, 0.0, 10.0)

    def _reasons_warnings(
        self,
        pool: LiquidityPool,
        classification: LiquiditySweepClassification,
        reclaim_type: LiquiditySweepReclaimType,
        rejection: LiquiditySweepRejectionQuality,
        confirmation: LiquiditySweepPostConfirmation,
    ) -> tuple[list[str], list[str]]:
        reasons = ["liquidity_level_was_traded_beyond"]
        warnings = ["liquidity_sweep_alone_is_not_entry_signal"]
        if classification != LiquiditySweepClassification.SWEEP_NOT_BREAKOUT:
            warnings.extend(["close_accepted_beyond_liquidity", "classified_as_breakout_not_sweep"])
            return reasons, warnings
        reasons.append("candle_close_reclaimed_or_rejected_liquidity_zone")
        if pool.quality_score >= 7:
            reasons.append("high_quality_liquidity_was_swept")
        if reclaim_type in (LiquiditySweepReclaimType.FULL_RECLAIM, LiquiditySweepReclaimType.FULL_REJECTION):
            reasons.append("full_zone_reclaim_or_rejection")
        if rejection.rejection_strength == LiquiditySweepRejectionStrength.STRONG:
            reasons.append("strong_wick_rejection")
        else:
            warnings.append("wick_rejection_not_strong")
        if confirmation.mss_after_sweep:
            reasons.append("mss_after_sweep_confirmed_context")
        elif confirmation.choch_after_sweep:
            reasons.append("choch_after_sweep_warned_context")
        else:
            warnings.append("no_mss_or_choch_after_sweep")
        if confirmation.displacement_after_sweep:
            reasons.append("post_sweep_displacement_detected")
        else:
            warnings.append("no_post_sweep_displacement")
        if confirmation.fvg_after_sweep:
            reasons.append("fvg_after_sweep_detected")
        return reasons, warnings

    def _pool_is_eligible(self, pool: LiquidityPool, candle_index: int) -> bool:
        if pool.swept_status in {LiquidityStatus.BROKEN, LiquidityStatus.INVALID}:
            return False
        if pool.quality_score < self.config.minimum_liquidity_quality:
            return False
        return pool.first_created_index <= candle_index

    def _normalize_candles(self, candles: Sequence[CandleNode | Mapping[str, Any]]) -> list[_SweepCandle]:
        normalized: list[_SweepCandle] = []
        for fallback_index, candle in enumerate(candles):
            if isinstance(candle, CandleNode):
                normalized.append(
                    _SweepCandle(
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
                _SweepCandle(
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

    def _normalize_pools(self, pools: Sequence[LiquidityPool | Mapping[str, Any]]) -> list[LiquidityPool]:
        normalized: list[LiquidityPool] = []
        for pool in pools:
            if isinstance(pool, LiquidityPool):
                normalized.append(pool)
            else:
                normalized.append(_pool_from_mapping(pool))
        return normalized


def detect_liquidity_sweep(
    df: Sequence[CandleNode | Mapping[str, Any]],
    liquidity_pools: Sequence[LiquidityPool | Mapping[str, Any]],
    mss_events: Sequence[Mapping[str, Any]] | None = None,
    choch_events: Sequence[Mapping[str, Any]] | None = None,
    **config_overrides: Any,
) -> list[dict[str, Any]]:
    """Convenience helper returning JSON-friendly sweep event dictionaries."""

    config = LiquiditySweepDetectionConfig(**config_overrides) if config_overrides else LiquiditySweepDetectionConfig()
    detector = ICTLiquiditySweepDetector(config)
    return [event.as_dict() for event in detector.detect(df, liquidity_pools, mss_events, choch_events)]


def _pool_from_mapping(pool: Mapping[str, Any]) -> LiquidityPool:
    zone_data = pool.get("price_zone") if isinstance(pool.get("price_zone"), Mapping) else pool
    zone = LiquidityPriceZone(
        zone_low=float(_first_present(zone_data, "zone_low")),
        zone_mid=float(_first_present(zone_data, "zone_mid")),
        zone_high=float(_first_present(zone_data, "zone_high")),
    )
    quality_grade = str(_first_present(pool, "quality_grade", default=LiquidityQualityGrade.WEAK.value))
    if quality_grade not in {item.value for item in LiquidityQualityGrade}:
        quality_grade = LiquidityQualityGrade.WEAK.value
    liquidity_type = str(_first_present(pool, "liquidity_type", default=LiquidityType.SWING_HIGH.value))
    if liquidity_type not in {item.value for item in LiquidityType}:
        liquidity_type = LiquidityType.SWING_HIGH.value
    return LiquidityPool(
        liquidity_id=str(_first_present(pool, "liquidity_id", "id")),
        concept_name=str(_first_present(pool, "concept_name", default="Liquidity")),
        symbol=str(_first_present(pool, "symbol", default="unknown")),
        liquidity_type=LiquidityType(liquidity_type),
        source=str(_first_present(pool, "source", default="unknown")),
        direction=LiquidityDirection(str(_first_present(pool, "direction"))),
        timeframe=str(_first_present(pool, "timeframe", default="unknown")),
        price_zone=zone,
        touched_count=int(_first_present(pool, "touched_count", default=1)),
        member_indexes=tuple(_first_present(pool, "member_indexes", default=())),
        member_prices=tuple(_first_present(pool, "member_prices", default=())),
        first_created_index=int(_first_present(pool, "first_created_index", "created_index", default=0)),
        last_touched_index=_optional_int(pool.get("last_touched_index")),
        swept_status=LiquidityStatus(str(_first_present(pool, "swept_status", default=LiquidityStatus.UNSWEPT.value))),
        sweep_candle_index=_optional_int(pool.get("sweep_candle_index")),
        broken_candle_index=_optional_int(pool.get("broken_candle_index")),
        quality_score=float(_first_present(pool, "quality_score", default=0.0)),
        quality_grade=LiquidityQualityGrade(quality_grade),
        role=tuple(_first_present(pool, "role", default=())),
        confluence=_empty_confluence(),
        tolerance=_empty_tolerance(),
        sweep_details=_empty_sweep_details(),
        reasons=tuple(_first_present(pool, "reasons", default=())),
        warnings=tuple(_first_present(pool, "warnings", default=())),
    )


def _rejection_quality(
    candle: _SweepCandle, direction: LiquiditySweepDirection, config: LiquiditySweepDetectionConfig
) -> LiquiditySweepRejectionQuality:
    if candle.range <= 0 or direction == LiquiditySweepDirection.NONE:
        return LiquiditySweepRejectionQuality(False, 0.0, "none", LiquiditySweepRejectionStrength.NONE)
    if direction == LiquiditySweepDirection.BULLISH:
        wick_ratio = candle.lower_wick / candle.range
        close_position = "near_high" if (candle.high_p - candle.close_p) / candle.range <= 0.25 else "mid"
    else:
        wick_ratio = candle.upper_wick / candle.range
        close_position = "near_low" if (candle.close_p - candle.low_p) / candle.range <= 0.25 else "mid"
    if wick_ratio >= config.strong_wick_ratio:
        strength = LiquiditySweepRejectionStrength.STRONG
    elif wick_ratio >= config.min_wick_ratio:
        strength = LiquiditySweepRejectionStrength.MODERATE
    elif wick_ratio > 0:
        strength = LiquiditySweepRejectionStrength.WEAK
    else:
        strength = LiquiditySweepRejectionStrength.NONE
    return LiquiditySweepRejectionQuality(
        wick_present=wick_ratio > 0,
        wick_ratio=round(wick_ratio, 4),
        close_position=close_position,
        rejection_strength=strength,
    )


def _bullish_reclaim_type(close: float, pool: LiquidityPool) -> LiquiditySweepReclaimType:
    if close > pool.zone_high:
        return LiquiditySweepReclaimType.FULL_RECLAIM
    if close > pool.zone_mid:
        return LiquiditySweepReclaimType.MID_RECLAIM
    return LiquiditySweepReclaimType.WEAK_RECLAIM


def _bearish_reclaim_type(close: float, pool: LiquidityPool) -> LiquiditySweepReclaimType:
    if close < pool.zone_low:
        return LiquiditySweepReclaimType.FULL_REJECTION
    if close < pool.zone_mid:
        return LiquiditySweepReclaimType.MID_REJECTION
    return LiquiditySweepReclaimType.WEAK_REJECTION


def _first_confirmation_event(
    events: Sequence[Mapping[str, Any]], direction: str, sweep_index: int, max_bars: int
) -> Mapping[str, Any] | None:
    matches = []
    for event in events:
        event_direction = str(_first_present(event, "direction", default="")).lower()
        event_index = _event_index(event)
        if event_direction == direction and event_index is not None and 0 < event_index - sweep_index <= max_bars:
            matches.append(event)
    return min(matches, key=lambda item: _event_index(item) or 0) if matches else None


def _event_index(event: Mapping[str, Any] | None) -> int | None:
    if event is None:
        return None
    for key in ("mss_candle_index", "choch_candle_index", "candle_index", "confirmation_index", "index"):
        if key in event and event[key] is not None:
            return int(event[key])
    candle = event.get("confirmation_candle")
    if isinstance(candle, Mapping) and candle.get("index") is not None:
        return int(candle["index"])
    return None


def _fvg_after_sweep(candles: Sequence[_SweepCandle], position: int, direction: LiquiditySweepDirection) -> bool:
    end = min(len(candles), position + 1 + 4)
    for third_position in range(position + 2, end):
        first = candles[third_position - 2]
        third = candles[third_position]
        if direction == LiquiditySweepDirection.BULLISH and first.high_p < third.low_p:
            return True
        if direction == LiquiditySweepDirection.BEARISH and first.low_p > third.high_p:
            return True
    return False


def _entry_logic(
    direction: LiquiditySweepDirection,
    confirmation: LiquiditySweepPostConfirmation,
    classification: LiquiditySweepClassification,
) -> LiquiditySweepEntryLogic:
    if classification != LiquiditySweepClassification.SWEEP_NOT_BREAKOUT or direction == LiquiditySweepDirection.NONE:
        return LiquiditySweepEntryLogic(
            False,
            False,
            "no_entry_from_breakout_classification_in_sweep_module",
            "none",
            "none",
        )
    confirmed = confirmation.mss_after_sweep and confirmation.displacement_after_sweep
    if direction == LiquiditySweepDirection.BULLISH:
        return LiquiditySweepEntryLogic(
            False,
            confirmed,
            "wait_for_retracement_to_bullish_fvg_or_order_block_after_mss",
            "below_sweep_low_or_bullish_order_block_low",
            "nearest_buy_side_liquidity_above",
        )
    return LiquiditySweepEntryLogic(
        False,
        confirmed,
        "wait_for_retracement_to_bearish_fvg_or_order_block_after_mss",
        "above_sweep_high_or_bearish_order_block_high",
        "nearest_sell_side_liquidity_below",
    )


def _setup_status(
    classification: LiquiditySweepClassification,
    confirmation: LiquiditySweepPostConfirmation,
    direction: LiquiditySweepDirection,
) -> LiquiditySweepSetupStatus:
    if classification != LiquiditySweepClassification.SWEEP_NOT_BREAKOUT:
        return LiquiditySweepSetupStatus.BREAKOUT_CONTINUATION
    if direction == LiquiditySweepDirection.NONE:
        return LiquiditySweepSetupStatus.CONTEXT_ONLY
    if confirmation.mss_after_sweep:
        return LiquiditySweepSetupStatus.CONFIRMED_BY_MSS
    if confirmation.choch_after_sweep:
        return LiquiditySweepSetupStatus.CONFIRMED_BY_CHOCH
    return LiquiditySweepSetupStatus.UNCONFIRMED_OR_STALE


def _confidence_grade(score: float, detected: bool) -> LiquiditySweepConfidenceGrade:
    if not detected:
        return LiquiditySweepConfidenceGrade.INVALID
    if score >= 9.0:
        return LiquiditySweepConfidenceGrade.HIGH_QUALITY
    if score >= 7.0:
        return LiquiditySweepConfidenceGrade.STRONG
    if score >= 5.0:
        return LiquiditySweepConfidenceGrade.MODERATE
    return LiquiditySweepConfidenceGrade.WEAK


def _calculate_atr(candles: Sequence[_SweepCandle], period: int) -> list[float]:
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


def _average_range(candles: Sequence[_SweepCandle]) -> float:
    if not candles:
        return 0.0
    return sum(candle.range for candle in candles) / len(candles)


def _empty_confluence():
    from src.analytics.ict_smc.liquidity import LiquidityConfluence

    return LiquidityConfluence(False)


def _empty_tolerance():
    from src.analytics.ict_smc.liquidity import LiquidityTolerance

    return LiquidityTolerance("unknown", 0.0, 0.0, 0.0)


def _empty_sweep_details():
    from src.analytics.ict_smc.liquidity import LiquiditySweepDetails

    return LiquiditySweepDetails()


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


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
