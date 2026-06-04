"""Execution-layer stop hardening for live demo observations.

The strategy may discover a valid setup, but a broker order can still be too
tight for the instrument's live noise. This module adjusts only the execution
geometry before risk sizing and order submission.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median
from typing import Sequence

from src.core.domain.constants import OrderDirection
from src.core.domain.market_data import CandleNode
from src.core.domain.setup_models import SetupOpportunityNode


@dataclass(frozen=True, slots=True)
class StopHardeningConfig:
    """Bounded protective-stop rules for GOLD.i# demo execution."""

    minimum_stop_distance_price: float = 4.0
    maximum_stop_distance_price: float = 7.5
    spread_multiplier: float = 12.0
    median_range_multiplier: float = 1.25
    minimum_rr: float = 3.0
    range_lookback: int = 20
    price_precision: int = 2


@dataclass(frozen=True, slots=True)
class StopHardeningResult:
    setup: SetupOpportunityNode
    adjusted: bool
    original_stop_distance: float
    hardened_stop_distance: float
    original_rr: float
    hardened_rr: float
    reasons: tuple[str, ...]


class DynamicStructuralStopEngine:
    """Protect demo orders from unrealistically tight SL placement."""

    def __init__(self, config: StopHardeningConfig | None = None) -> None:
        self._config = config or StopHardeningConfig()

    def derive_stop_loss(self, setup: SetupOpportunityNode, state_snapshot) -> float:
        return setup.stop_loss

    def harden_for_demo_execution(
        self,
        setup: SetupOpportunityNode,
        recent_closed_candles: Sequence[CandleNode],
        current_spread_price: float,
    ) -> StopHardeningResult:
        original_distance = abs(setup.entry_price - setup.stop_loss)
        desired_distance, reasons = self._required_stop_distance(
            recent_closed_candles,
            current_spread_price,
        )
        if original_distance >= desired_distance:
            rr = self._risk_reward(setup.entry_price, setup.stop_loss, setup.take_profit)
            return StopHardeningResult(
                setup=setup,
                adjusted=False,
                original_stop_distance=round(original_distance, 4),
                hardened_stop_distance=round(original_distance, 4),
                original_rr=round(rr, 4),
                hardened_rr=round(rr, 4),
                reasons=tuple(reasons),
            )

        hardened = self._rebuild_setup(setup, desired_distance)
        hardened_rr = self._risk_reward(hardened.entry_price, hardened.stop_loss, hardened.take_profit)
        return StopHardeningResult(
            setup=hardened,
            adjusted=True,
            original_stop_distance=round(original_distance, 4),
            hardened_stop_distance=round(desired_distance, 4),
            original_rr=round(self._risk_reward(setup.entry_price, setup.stop_loss, setup.take_profit), 4),
            hardened_rr=round(hardened_rr, 4),
            reasons=tuple(dict.fromkeys((*reasons, "STOP_DISTANCE_HARDENED_FOR_GOLD_NOISE"))),
        )

    def _required_stop_distance(
        self,
        recent_closed_candles: Sequence[CandleNode],
        current_spread_price: float,
    ) -> tuple[float, list[str]]:
        config = self._config
        candidates = [config.minimum_stop_distance_price]
        reasons = ["MINIMUM_GOLD_STOP_DISTANCE"]
        if current_spread_price > 0:
            candidates.append(current_spread_price * config.spread_multiplier)
            reasons.append("SPREAD_BUFFER")

        ranges = [
            max(0.0, candle.high_p - candle.low_p)
            for candle in recent_closed_candles[-config.range_lookback :]
            if candle.is_closed and candle.high_p > candle.low_p
        ]
        if ranges:
            candidates.append(median(ranges) * config.median_range_multiplier)
            reasons.append("RECENT_1M_RANGE_BUFFER")

        desired = max(candidates)
        bounded = min(config.maximum_stop_distance_price, max(config.minimum_stop_distance_price, desired))
        if bounded < desired:
            reasons.append("MAXIMUM_STOP_DISTANCE_CAP")
        return round(bounded, config.price_precision), reasons

    def _rebuild_setup(self, setup: SetupOpportunityNode, stop_distance: float) -> SetupOpportunityNode:
        config = self._config
        rr = max(self._risk_reward(setup.entry_price, setup.stop_loss, setup.take_profit), config.minimum_rr)
        if setup.direction == OrderDirection.BUY:
            stop_loss = setup.entry_price - stop_distance
            take_profit = setup.entry_price + (stop_distance * rr)
        else:
            stop_loss = setup.entry_price + stop_distance
            take_profit = setup.entry_price - (stop_distance * rr)

        stop_loss = round(stop_loss, config.price_precision)
        take_profit = round(take_profit, config.price_precision)
        return replace(
            setup,
            stop_loss=stop_loss,
            take_profit=take_profit,
            estimated_rr=self._risk_reward(setup.entry_price, stop_loss, take_profit),
        )

    @staticmethod
    def _risk_reward(entry: float, stop_loss: float, take_profit: float) -> float:
        risk = abs(entry - stop_loss)
        if risk <= 0:
            return 0.0
        return abs(take_profit - entry) / risk
