"""Volatility and market-environment models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique


@unique
class DetailedRegimeType(str, Enum):
    ILLIQUID_DEAD_ZONE = "ILLIQUID_DEAD_ZONE"
    POST_NEWS_CHAOS = "POST_NEWS_CHAOS"
    MOMENTUM_ACCELERATION = "MOMENTUM_ACCELERATION"
    VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
    VOLATILITY_COMPRESSION = "VOLATILITY_COMPRESSION"
    TRENDING_STRONG = "TRENDING_STRONG"
    TRENDING_WEAK = "TRENDING_WEAK"
    RANGE_BOUND = "RANGE_BOUND"


@dataclass(frozen=True, slots=True)
class MarketEnvironmentMetrics:
    volatility_ratio: float
    tick_velocity_per_second: float
    mean_candle_spread_pips: float
    wick_expansion_coefficient: float
    trend_strength_adx: float
    volume_z_score: float
    confidence_score: float
    regime_decay_factor: float
