"""
Apex Engine - Volatility Regime and Environmental Classifier
Responsibility: Processes rolling arrays incrementally to categorize market structures.
Latency Profile: Highly optimized linear numpy computations avoiding massive history scans.
"""

import numpy as np
from datetime import datetime
from typing import Tuple, List
import structlog
from src.core.domain.market_data import TickNode, CandleNode
from src.core.domain.regime_models import DetailedRegimeType, MarketEnvironmentMetrics

logger = structlog.get_logger()

class QuantitativeRegimeClassificationEngine:
    """Performs dynamic statistical clustering to determine real-time volatility states."""

    def __init__(self, baseline_period: int = 20) -> None:
        self._period = baseline_period
        # Memory bounded data arrays
        self._candle_spreads: List[float] = []
        self._candle_volumes: List[int] = []
        self._tick_counts: List[int] = []
        self._timestamps: List[datetime] = []

        # Instantiation primitives for incremental state updates
        self._last_regime = DetailedRegimeType.ILLIQUID_DEAD_ZONE
        self._confidence = 1.0

    def append_candle_metrics(self, candle: CandleNode) -> None:
        """Appends verified metrics from closed bars to the running lookback window."""
        spread = candle.high_p - candle.low_p
        self._candle_spreads.append(spread)
        self._candle_volumes.append(candle.volume)
        self._tick_counts.append(candle.ticks_count)
        self._timestamps.append(candle.end_time)

        if len(self._candle_spreads) > self._period * 5:
            self._candle_spreads.pop(0)
            self._candle_volumes.pop(0)
            self._tick_counts.pop(0)
            self._timestamps.pop(0)

    def extract_environment_metrics(self, current_tick_velocity: float) -> MarketEnvironmentMetrics:
        """Calculates quantitative variables from the current state matrices."""
        if len(self._candle_spreads) < self._period:
            return MarketEnvironmentMetrics(1.0, current_tick_velocity, 0.0, 0.0, 0.0, 0.0, 50.0, 1.0)

        # 1. Volatility Clustering & Expansion Ratios
        recent_spreads = np.array(self._candle_spreads[-self._period:])
        historical_spreads = np.array(self._candle_spreads[:-self._period]) if len(self._candle_spreads) > self._period else recent_spreads
        
        mean_recent = np.mean(recent_spreads)
        mean_hist = np.mean(historical_spreads) if len(historical_spreads) > 0 else mean_recent
        vol_ratio = mean_recent / mean_hist if mean_hist > 0 else 1.0

        # 2. Volume Distribution Modeling
        recent_volumes = np.array(self._candle_volumes[-self._period:])
        vol_mean = np.mean(recent_volumes)
        vol_std = np.std(recent_volumes)
        vol_z = (recent_volumes[-1] - vol_mean) / vol_std if vol_std > 0 else 0.0

        # 3. Simple Trend Dispersion Parsing
        diffs = np.diff(recent_spreads)
        trend_coefficient = float(np.sum(diffs) / len(diffs)) if len(diffs) > 0 else 0.0

        # Confidence framework modeling
        confidence = float(clip_value(100.0 - (vol_ratio * 10.0), 10.0, 100.0))

        return MarketEnvironmentMetrics(
            volatility_ratio=float(vol_ratio),
            tick_velocity_per_second=current_tick_velocity,
            mean_candle_spread_pips=float(mean_recent * 10.0),  # Scale allocation
            wick_expansion_coefficient=trend_coefficient,
            trend_strength_adx=abs(trend_coefficient) * 50,
            volume_z_score=float(vol_z),
            confidence_score=confidence,
            regime_decay_factor=0.95
        )

    def classify_regime(self, metrics: MarketEnvironmentMetrics, is_killzone: bool) -> DetailedRegimeType:
        """Runs a rules-based decision matrix to identify active institutional order flow states."""
        # 1. Illiquid / Structural Dead Zones
        if metrics.tick_velocity_per_second < 0.5 and not is_killzone:
            self._last_regime = DetailedRegimeType.ILLIQUID_DEAD_ZONE
            return self._last_regime

        # 2. Volatility Shock States (CPI / FOMC Profiles)
        if metrics.volatility_ratio > 3.0 or metrics.volume_z_score > 3.5:
            self._last_regime = DetailedRegimeType.POST_NEWS_CHAOS
            return self._last_regime

        # 3. Expansion vs. Compression Boundaries
        if metrics.volatility_ratio > 1.4:
            if metrics.trend_strength_adx > 1.5:
                self._last_regime = DetailedRegimeType.MOMENTUM_ACCELERATION
            else:
                self._last_regime = DetailedRegimeType.VOLATILITY_EXPANSION
        elif metrics.volatility_ratio < 0.75:
            self._last_regime = DetailedRegimeType.VOLATILITY_COMPRESSION
        else:
            if metrics.trend_strength_adx > 2.0:
                self._last_regime = DetailedRegimeType.TRENDING_STRONG
            elif metrics.trend_strength_adx < 0.5:
                self._last_regime = DetailedRegimeType.RANGE_BOUND
            else:
                self._last_regime = DetailedRegimeType.TRENDING_WEAK

        return self._last_regime

def clip_value(val: float, low: float, high: float) -> float:
    return max(min(val, high), low)