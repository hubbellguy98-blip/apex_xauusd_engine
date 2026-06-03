"""Rule-based ICT/SMC market structure analysis.

The analyzer follows the user's market-structure specification:
- use confirmed closed candles only;
- confirm swings only after right-side candles have closed;
- classify HH/HL/LH/LL/EQH/EQL from objective swing points;
- treat wick-only breaks as liquidity sweeps, not BOS/MSS;
- separate bullish, bearish, ranging, transitional, and unclear states.

This module is observer-only for now. It can be connected to live scoring after
the concept output is compared against VPS reports and chart examples.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from statistics import mean
from typing import Iterable, Sequence

from src.core.domain.market_data import CandleNode
from src.core.domain.constants import OrderDirection


class SwingKind(str, Enum):
    HIGH = "high"
    LOW = "low"


class SwingLabel(str, Enum):
    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"
    EQH = "EQH"
    EQL = "EQL"
    FIRST_HIGH = "FIRST_HIGH"
    FIRST_LOW = "FIRST_LOW"


class StructureTrend(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGING = "ranging"
    TRANSITIONAL_BULLISH = "transitional_bullish"
    TRANSITIONAL_BEARISH = "transitional_bearish"
    UNCLEAR = "unclear"


class StructureBreakKind(str, Enum):
    NONE = "none"
    BULLISH_BOS = "bullish_BOS"
    BEARISH_BOS = "bearish_BOS"
    BULLISH_MSS = "bullish_MSS"
    BEARISH_MSS = "bearish_MSS"
    BULLISH_CHOCH = "bullish_CHoCH"
    BEARISH_CHOCH = "bearish_CHoCH"
    WICK_SWEEP_ONLY = "wick_sweep_only"


@dataclass(frozen=True, slots=True)
class MarketStructureConfig:
    left_bars: int = 3
    right_bars: int = 3
    atr_period: int = 14
    min_swing_atr_distance: float = 0.5
    min_candle_gap: int = 3
    break_buffer_atr: float = 0.05
    equal_level_tolerance_atr: float = 0.15
    minimum_range_atr: float = 1.0
    displacement_body_ratio: float = 0.55
    displacement_range_atr: float = 1.0
    volume_confirmation_multiplier: float = 1.1
    use_volume_filter: bool = False

    def __post_init__(self) -> None:
        if self.left_bars < 1 or self.right_bars < 1:
            raise ValueError("left_bars and right_bars must be positive.")
        if self.min_candle_gap < 0:
            raise ValueError("min_candle_gap cannot be negative.")


@dataclass(frozen=True, slots=True)
class StructuralSwing:
    index: int
    timestamp: datetime
    kind: SwingKind
    price: float
    label: SwingLabel
    strength_score: float
    is_structural: bool = True


@dataclass(frozen=True, slots=True)
class LiquiditySweepContext:
    detected: bool = False
    direction: str = "none"
    swept_level: float | None = None
    candle_index: int | None = None


@dataclass(frozen=True, slots=True)
class DisplacementContext:
    present: bool = False
    body_to_range_ratio: float = 0.0
    range_to_atr_ratio: float = 0.0
    close_position_quality: str = "unknown"


@dataclass(frozen=True, slots=True)
class PremiumDiscountContext:
    dealing_range_high: float | None = None
    dealing_range_low: float | None = None
    equilibrium: float | None = None
    signal_location: str = "unknown"


@dataclass(frozen=True, slots=True)
class OrderBlockContext:
    candidate_exists: bool = False
    direction: str = "none"
    caused_structure_break: bool = False
    low: float | None = None
    high: float | None = None
    candle_index: int | None = None


@dataclass(frozen=True, slots=True)
class StructureBreak:
    detected: bool
    kind: StructureBreakKind
    direction: OrderDirection | None = None
    broken_level: float | None = None
    candle_index: int | None = None
    confirmation_close: float | None = None
    wick_only: bool = False


@dataclass(frozen=True, slots=True)
class MarketStructureAnalysis:
    concept_name: str
    symbol: str
    timeframe: str
    trend_state: StructureTrend
    structure_label: str
    swings: tuple[StructuralSwing, ...]
    latest_swing_high: StructuralSwing | None
    latest_swing_low: StructuralSwing | None
    previous_swing_high: StructuralSwing | None
    previous_swing_low: StructuralSwing | None
    structure_break: StructureBreak
    liquidity_context: LiquiditySweepContext
    premium_discount_context: PremiumDiscountContext
    order_block_context: OrderBlockContext
    displacement: DisplacementContext
    confidence_score: float
    quality_grade: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "concept_name": self.concept_name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "trend_state": self.trend_state.value,
            "structure_label": self.structure_label,
            "latest_swing_high": _swing_to_dict(self.latest_swing_high),
            "latest_swing_low": _swing_to_dict(self.latest_swing_low),
            "previous_swing_high": _swing_to_dict(self.previous_swing_high),
            "previous_swing_low": _swing_to_dict(self.previous_swing_low),
            "break_detected": self.structure_break.detected,
            "break_type": self.structure_break.kind.value,
            "broken_level": self.structure_break.broken_level,
            "confirmation_candle_index": self.structure_break.candle_index,
            "confirmation_close": self.structure_break.confirmation_close,
            "wick_only_break": self.structure_break.wick_only,
            "liquidity_context": asdict(self.liquidity_context),
            "premium_discount_context": asdict(self.premium_discount_context),
            "order_block_context": asdict(self.order_block_context),
            "displacement": asdict(self.displacement),
            "confidence_score": self.confidence_score,
            "quality_grade": self.quality_grade,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
        }


class ICTMarketStructureAnalyzer:
    """Converts closed candles into deterministic ICT/SMC structure labels."""

    def __init__(self, config: MarketStructureConfig | None = None) -> None:
        self.config = config or MarketStructureConfig()

    def analyze(self, candles: Sequence[CandleNode], timeframe: str | None = None) -> MarketStructureAnalysis:
        closed = tuple(c for c in candles if c.is_closed)
        symbol = closed[-1].symbol if closed else "unknown"
        tf = timeframe or (closed[-1].timeframe if closed else "unknown")

        if len(closed) < self.config.left_bars + self.config.right_bars + 3:
            return self._empty_analysis(symbol, tf, "insufficient_closed_candles")

        atr_values = self._calculate_atr(closed)
        raw_swings = self.detect_swing_highs_lows(closed)
        structural_swings = self.filter_market_structure_swings(closed, raw_swings, atr_values)
        labeled_swings = self.classify_swing_points(structural_swings, atr_values)

        latest_high, previous_high = _latest_two(labeled_swings, SwingKind.HIGH)
        latest_low, previous_low = _latest_two(labeled_swings, SwingKind.LOW)
        trend = self.classify_market_structure(labeled_swings)
        break_reference_high = previous_high or latest_high
        break_reference_low = previous_low or latest_low
        structure_break = self.detect_structure_break(
            closed, break_reference_high, break_reference_low, trend, atr_values
        )
        liquidity = self.detect_liquidity_sweep(closed, break_reference_high, break_reference_low, atr_values)
        displacement = self._detect_displacement(closed, structure_break, atr_values)
        premium_discount = self._evaluate_premium_discount(latest_high, latest_low, structure_break)
        order_block = self._find_order_block_context(closed, structure_break, displacement)
        score, grade, reasons, warnings = self.score_market_structure_quality(
            trend,
            labeled_swings,
            structure_break,
            liquidity,
            displacement,
            premium_discount,
            order_block,
        )
        structure_label = self._structure_label(trend, structure_break, liquidity, latest_high, latest_low)

        return MarketStructureAnalysis(
            concept_name="Market Structure",
            symbol=symbol,
            timeframe=tf,
            trend_state=trend,
            structure_label=structure_label,
            swings=tuple(labeled_swings),
            latest_swing_high=latest_high,
            latest_swing_low=latest_low,
            previous_swing_high=previous_high,
            previous_swing_low=previous_low,
            structure_break=structure_break,
            liquidity_context=liquidity,
            premium_discount_context=premium_discount,
            order_block_context=order_block,
            displacement=displacement,
            confidence_score=round(score, 2),
            quality_grade=grade,
            reasons=tuple(reasons),
            warnings=tuple(warnings),
        )

    def detect_swing_highs_lows(self, candles: Sequence[CandleNode]) -> list[StructuralSwing]:
        raw: list[StructuralSwing] = []
        left = self.config.left_bars
        right = self.config.right_bars
        last_confirmable = len(candles) - right
        for idx in range(left, last_confirmable):
            candle = candles[idx]
            left_slice = candles[idx - left : idx]
            right_slice = candles[idx + 1 : idx + right + 1]
            if all(candle.high_p > item.high_p for item in left_slice) and all(
                candle.high_p > item.high_p for item in right_slice
            ):
                raw.append(
                    StructuralSwing(
                        index=idx,
                        timestamp=candle.end_time,
                        kind=SwingKind.HIGH,
                        price=candle.high_p,
                        label=SwingLabel.FIRST_HIGH,
                        strength_score=50.0,
                    )
                )
            if all(candle.low_p < item.low_p for item in left_slice) and all(
                candle.low_p < item.low_p for item in right_slice
            ):
                raw.append(
                    StructuralSwing(
                        index=idx,
                        timestamp=candle.end_time,
                        kind=SwingKind.LOW,
                        price=candle.low_p,
                        label=SwingLabel.FIRST_LOW,
                        strength_score=50.0,
                    )
                )
        return sorted(raw, key=lambda swing: swing.index)

    def filter_market_structure_swings(
        self,
        candles: Sequence[CandleNode],
        swings: Sequence[StructuralSwing],
        atr_values: Sequence[float],
    ) -> list[StructuralSwing]:
        accepted: list[StructuralSwing] = []
        for swing in swings:
            atr = max(atr_values[swing.index], 1e-9)
            candle = candles[swing.index]
            body_ratio = abs(candle.close_p - candle.open_p) / max(candle.high_p - candle.low_p, 1e-9)
            strength = 50.0 + min(body_ratio * 25.0, 25.0)

            if accepted:
                previous = accepted[-1]
                distance = abs(swing.price - previous.price)
                gap = swing.index - previous.index
                if gap < self.config.min_candle_gap:
                    if swing.kind == previous.kind and self._is_more_extreme(swing, previous):
                        accepted[-1] = swing
                    continue
                if swing.kind != previous.kind and distance < self.config.min_swing_atr_distance * atr:
                    continue

                if swing.kind == previous.kind:
                    if self._is_more_extreme(swing, previous):
                        accepted[-1] = swing
                    continue

            accepted.append(
                StructuralSwing(
                    index=swing.index,
                    timestamp=swing.timestamp,
                    kind=swing.kind,
                    price=swing.price,
                    label=swing.label,
                    strength_score=min(100.0, strength),
                )
            )
        return accepted

    def classify_swing_points(
        self, swings: Sequence[StructuralSwing], atr_values: Sequence[float]
    ) -> list[StructuralSwing]:
        labeled: list[StructuralSwing] = []
        last_high: StructuralSwing | None = None
        last_low: StructuralSwing | None = None
        for swing in swings:
            tolerance = max(atr_values[swing.index] * self.config.equal_level_tolerance_atr, 1e-9)
            if swing.kind == SwingKind.HIGH:
                if last_high is None:
                    label = SwingLabel.FIRST_HIGH
                elif abs(swing.price - last_high.price) <= tolerance:
                    label = SwingLabel.EQH
                elif swing.price > last_high.price + tolerance:
                    label = SwingLabel.HH
                else:
                    label = SwingLabel.LH
                updated = _replace_label(swing, label)
                last_high = updated
            else:
                if last_low is None:
                    label = SwingLabel.FIRST_LOW
                elif abs(swing.price - last_low.price) <= tolerance:
                    label = SwingLabel.EQL
                elif swing.price > last_low.price + tolerance:
                    label = SwingLabel.HL
                else:
                    label = SwingLabel.LL
                updated = _replace_label(swing, label)
                last_low = updated
            labeled.append(updated)
        return labeled

    def classify_market_structure(self, swings: Sequence[StructuralSwing]) -> StructureTrend:
        if len(swings) < 4:
            return StructureTrend.UNCLEAR

        recent = swings[-6:]
        labels = {s.label for s in recent}
        last = recent[-1]

        if SwingLabel.EQH in labels and SwingLabel.EQL in labels:
            return StructureTrend.RANGING
        if SwingLabel.HH in labels and SwingLabel.HL in labels:
            return StructureTrend.BULLISH
        if SwingLabel.LL in labels and SwingLabel.LH in labels:
            return StructureTrend.BEARISH
        if last.label == SwingLabel.HH and SwingLabel.LL in labels:
            return StructureTrend.TRANSITIONAL_BULLISH
        if last.label == SwingLabel.LL and SwingLabel.HH in labels:
            return StructureTrend.TRANSITIONAL_BEARISH
        return StructureTrend.UNCLEAR

    def detect_structure_break(
        self,
        candles: Sequence[CandleNode],
        latest_high: StructuralSwing | None,
        latest_low: StructuralSwing | None,
        previous_trend: StructureTrend,
        atr_values: Sequence[float],
    ) -> StructureBreak:
        candidates: list[StructureBreak] = []
        if latest_high is not None:
            candidates.append(
                self._detect_bullish_break(candles, latest_high, previous_trend, atr_values)
            )
        if latest_low is not None:
            candidates.append(
                self._detect_bearish_break(candles, latest_low, previous_trend, atr_values)
            )
        detected = [item for item in candidates if item.detected or item.wick_only]
        if not detected:
            return StructureBreak(False, StructureBreakKind.NONE)
        return max(detected, key=lambda item: item.candle_index or -1)

    def detect_liquidity_sweep(
        self,
        candles: Sequence[CandleNode],
        latest_high: StructuralSwing | None,
        latest_low: StructuralSwing | None,
        atr_values: Sequence[float],
    ) -> LiquiditySweepContext:
        latest: LiquiditySweepContext | None = None
        if latest_high is not None:
            for idx in range(latest_high.index + 1, len(candles)):
                candle = candles[idx]
                if candle.high_p > latest_high.price and candle.close_p <= latest_high.price:
                    latest = LiquiditySweepContext(True, "buy_side_liquidity_sweep", latest_high.price, idx)
        if latest_low is not None:
            for idx in range(latest_low.index + 1, len(candles)):
                candle = candles[idx]
                if candle.low_p < latest_low.price and candle.close_p >= latest_low.price:
                    candidate = LiquiditySweepContext(True, "sell_side_liquidity_sweep", latest_low.price, idx)
                    if latest is None or (candidate.candle_index or -1) > (latest.candle_index or -1):
                        latest = candidate
        return latest or LiquiditySweepContext()

    def score_market_structure_quality(
        self,
        trend: StructureTrend,
        swings: Sequence[StructuralSwing],
        structure_break: StructureBreak,
        liquidity: LiquiditySweepContext,
        displacement: DisplacementContext,
        premium_discount: PremiumDiscountContext,
        order_block: OrderBlockContext,
    ) -> tuple[float, str, list[str], list[str]]:
        score = 0.0
        reasons: list[str] = []
        warnings: list[str] = []

        if len(swings) >= 4:
            score += 1.5
            reasons.append("Confirmed alternating swing sequence is available.")
        else:
            warnings.append("insufficient_swings")

        if trend in (StructureTrend.BULLISH, StructureTrend.BEARISH):
            score += 1.5
            reasons.append(f"Market structure is classified as {trend.value}.")
        elif trend in (StructureTrend.TRANSITIONAL_BULLISH, StructureTrend.TRANSITIONAL_BEARISH):
            score += 1.0
            reasons.append(f"Market structure is transitioning: {trend.value}.")
        elif trend == StructureTrend.RANGING:
            score += 0.5
            warnings.append("ranging_market")
        else:
            warnings.append("unclear_structure")

        if structure_break.detected:
            score += 2.0
            reasons.append("Structure break is confirmed by candle close beyond the level.")
        elif structure_break.wick_only:
            score += 0.5
            warnings.append("wick_only_break")

        if liquidity.detected:
            score += 1.0
            reasons.append(f"Liquidity context detected: {liquidity.direction}.")

        if displacement.present:
            score += 1.25
            reasons.append("Displacement is present on the confirmation candle.")
        else:
            warnings.append("low_displacement")

        if premium_discount.signal_location in ("discount_to_equilibrium_reclaim", "premium_to_equilibrium_reject"):
            score += 0.75
            reasons.append(f"Signal location supports the direction: {premium_discount.signal_location}.")
        elif premium_discount.signal_location == "range_middle_signal":
            warnings.append("range_middle_signal")

        if order_block.candidate_exists and order_block.caused_structure_break:
            score += 1.0
            reasons.append("Candidate order block caused the structure break.")

        score = min(10.0, max(0.0, score))
        if score >= 8.0:
            grade = "strong"
        elif score >= 6.0:
            grade = "good"
        elif score >= 4.0:
            grade = "weak"
        else:
            grade = "poor"
        return score, grade, reasons, warnings

    def _detect_bullish_break(
        self,
        candles: Sequence[CandleNode],
        swing: StructuralSwing,
        trend: StructureTrend,
        atr_values: Sequence[float],
    ) -> StructureBreak:
        for idx in range(swing.index + 1, len(candles)):
            candle = candles[idx]
            buffer = atr_values[idx] * self.config.break_buffer_atr
            if candle.close_p > swing.price + buffer:
                kind = (
                    StructureBreakKind.BULLISH_BOS
                    if trend in (StructureTrend.BULLISH, StructureTrend.UNCLEAR, StructureTrend.RANGING)
                    else StructureBreakKind.BULLISH_MSS
                )
                return StructureBreak(True, kind, OrderDirection.BUY, swing.price, idx, candle.close_p)
            if candle.high_p > swing.price + buffer and candle.close_p <= swing.price:
                return StructureBreak(False, StructureBreakKind.WICK_SWEEP_ONLY, OrderDirection.BUY, swing.price, idx, candle.close_p, True)
        return StructureBreak(False, StructureBreakKind.NONE)

    def _detect_bearish_break(
        self,
        candles: Sequence[CandleNode],
        swing: StructuralSwing,
        trend: StructureTrend,
        atr_values: Sequence[float],
    ) -> StructureBreak:
        for idx in range(swing.index + 1, len(candles)):
            candle = candles[idx]
            buffer = atr_values[idx] * self.config.break_buffer_atr
            if candle.close_p < swing.price - buffer:
                kind = (
                    StructureBreakKind.BEARISH_BOS
                    if trend in (StructureTrend.BEARISH, StructureTrend.UNCLEAR, StructureTrend.RANGING)
                    else StructureBreakKind.BEARISH_MSS
                )
                return StructureBreak(True, kind, OrderDirection.SELL, swing.price, idx, candle.close_p)
            if candle.low_p < swing.price - buffer and candle.close_p >= swing.price:
                return StructureBreak(False, StructureBreakKind.WICK_SWEEP_ONLY, OrderDirection.SELL, swing.price, idx, candle.close_p, True)
        return StructureBreak(False, StructureBreakKind.NONE)

    def _detect_displacement(
        self,
        candles: Sequence[CandleNode],
        structure_break: StructureBreak,
        atr_values: Sequence[float],
    ) -> DisplacementContext:
        if structure_break.candle_index is None:
            return DisplacementContext()
        idx = structure_break.candle_index
        candle = candles[idx]
        candle_range = max(candle.high_p - candle.low_p, 1e-9)
        body = abs(candle.close_p - candle.open_p)
        body_ratio = body / candle_range
        range_to_atr = candle_range / max(atr_values[idx], 1e-9)
        if candle.close_p >= candle.open_p:
            close_position = (candle.close_p - candle.low_p) / candle_range
            quality = "strong_close_near_high" if close_position >= 0.7 else "weak_close_position"
        else:
            close_position = (candle.high_p - candle.close_p) / candle_range
            quality = "strong_close_near_low" if close_position >= 0.7 else "weak_close_position"
        present = body_ratio >= self.config.displacement_body_ratio and range_to_atr >= self.config.displacement_range_atr
        return DisplacementContext(present, round(body_ratio, 4), round(range_to_atr, 4), quality)

    def _evaluate_premium_discount(
        self,
        latest_high: StructuralSwing | None,
        latest_low: StructuralSwing | None,
        structure_break: StructureBreak,
    ) -> PremiumDiscountContext:
        if latest_high is None or latest_low is None:
            return PremiumDiscountContext()
        high = max(latest_high.price, latest_low.price)
        low = min(latest_high.price, latest_low.price)
        equilibrium = (high + low) / 2.0
        close = structure_break.confirmation_close
        if close is None:
            location = "unknown"
        elif structure_break.direction == OrderDirection.BUY and close >= equilibrium:
            location = "discount_to_equilibrium_reclaim"
        elif structure_break.direction == OrderDirection.SELL and close <= equilibrium:
            location = "premium_to_equilibrium_reject"
        else:
            location = "range_middle_signal"
        return PremiumDiscountContext(high, low, equilibrium, location)

    def _find_order_block_context(
        self,
        candles: Sequence[CandleNode],
        structure_break: StructureBreak,
        displacement: DisplacementContext,
    ) -> OrderBlockContext:
        if not structure_break.detected or not displacement.present or structure_break.candle_index is None:
            return OrderBlockContext()
        start = max(0, structure_break.candle_index - 8)
        search = range(structure_break.candle_index - 1, start - 1, -1)
        if structure_break.direction == OrderDirection.BUY:
            for idx in search:
                candle = candles[idx]
                if candle.close_p < candle.open_p:
                    return OrderBlockContext(True, "bullish", True, candle.low_p, candle.high_p, idx)
        if structure_break.direction == OrderDirection.SELL:
            for idx in search:
                candle = candles[idx]
                if candle.close_p > candle.open_p:
                    return OrderBlockContext(True, "bearish", True, candle.low_p, candle.high_p, idx)
        return OrderBlockContext()

    def _structure_label(
        self,
        trend: StructureTrend,
        structure_break: StructureBreak,
        liquidity: LiquiditySweepContext,
        latest_high: StructuralSwing | None,
        latest_low: StructuralSwing | None,
    ) -> str:
        if structure_break.kind == StructureBreakKind.BULLISH_MSS and liquidity.direction == "sell_side_liquidity_sweep":
            return "bullish_MSS_after_sell_side_sweep"
        if structure_break.kind == StructureBreakKind.BEARISH_MSS and liquidity.direction == "buy_side_liquidity_sweep":
            return "bearish_MSS_after_buy_side_sweep"
        if structure_break.kind != StructureBreakKind.NONE:
            return structure_break.kind.value
        if latest_high and latest_high.label == SwingLabel.EQH and latest_low and latest_low.label == SwingLabel.EQL:
            return "equal_high_equal_low_range"
        if trend == StructureTrend.BULLISH:
            return "HH_HL_bullish_structure"
        if trend == StructureTrend.BEARISH:
            return "LL_LH_bearish_structure"
        if structure_break.wick_only:
            return "wick_sweep_only"
        return "unclear_structure"

    def _empty_analysis(self, symbol: str, timeframe: str, warning: str) -> MarketStructureAnalysis:
        return MarketStructureAnalysis(
            concept_name="Market Structure",
            symbol=symbol,
            timeframe=timeframe,
            trend_state=StructureTrend.UNCLEAR,
            structure_label="unclear_structure",
            swings=tuple(),
            latest_swing_high=None,
            latest_swing_low=None,
            previous_swing_high=None,
            previous_swing_low=None,
            structure_break=StructureBreak(False, StructureBreakKind.NONE),
            liquidity_context=LiquiditySweepContext(),
            premium_discount_context=PremiumDiscountContext(),
            order_block_context=OrderBlockContext(),
            displacement=DisplacementContext(),
            confidence_score=0.0,
            quality_grade="poor",
            warnings=(warning,),
        )

    def _calculate_atr(self, candles: Sequence[CandleNode]) -> list[float]:
        true_ranges: list[float] = []
        for idx, candle in enumerate(candles):
            if idx == 0:
                tr = candle.high_p - candle.low_p
            else:
                prev_close = candles[idx - 1].close_p
                tr = max(
                    candle.high_p - candle.low_p,
                    abs(candle.high_p - prev_close),
                    abs(candle.low_p - prev_close),
                )
            true_ranges.append(max(tr, 1e-9))

        atr: list[float] = []
        for idx in range(len(true_ranges)):
            window = true_ranges[max(0, idx - self.config.atr_period + 1) : idx + 1]
            atr.append(mean(window))
        return atr

    @staticmethod
    def _is_more_extreme(candidate: StructuralSwing, current: StructuralSwing) -> bool:
        if candidate.kind == SwingKind.HIGH:
            return candidate.price > current.price
        return candidate.price < current.price


def _latest_two(swings: Sequence[StructuralSwing], kind: SwingKind) -> tuple[StructuralSwing | None, StructuralSwing | None]:
    filtered = [swing for swing in swings if swing.kind == kind]
    latest = filtered[-1] if filtered else None
    previous = filtered[-2] if len(filtered) >= 2 else None
    return latest, previous


def _replace_label(swing: StructuralSwing, label: SwingLabel) -> StructuralSwing:
    return StructuralSwing(
        index=swing.index,
        timestamp=swing.timestamp,
        kind=swing.kind,
        price=swing.price,
        label=label,
        strength_score=swing.strength_score,
        is_structural=swing.is_structural,
    )


def _swing_to_dict(swing: StructuralSwing | None) -> dict[str, object] | None:
    if swing is None:
        return None
    return {
        "price": swing.price,
        "timestamp": swing.timestamp.isoformat(),
        "candle_index": swing.index,
        "swing_label": swing.label.value,
        "is_structural": swing.is_structural,
        "strength_score": swing.strength_score,
    }


def analyze_market_structure(
    candles: Iterable[CandleNode],
    timeframe: str | None = None,
    config: MarketStructureConfig | None = None,
) -> MarketStructureAnalysis:
    """Convenience function matching the concept-spec function name."""
    return ICTMarketStructureAnalyzer(config).analyze(tuple(candles), timeframe)
