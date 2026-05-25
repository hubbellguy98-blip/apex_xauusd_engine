"""Incremental tick-to-candle builder."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional, Tuple

from src.core.domain.market_data import CandleNode, TickNode


class IncrementalCandleBuilder:
    """Builds fixed-interval candles from incoming ticks."""

    def __init__(self, symbol: str, timeframe: str, interval_seconds: int) -> None:
        self._symbol = symbol
        self._timeframe = timeframe
        self._interval = timedelta(seconds=interval_seconds)
        self._active: Optional[CandleNode] = None

    def process_tick(self, tick: TickNode) -> Tuple[Optional[CandleNode], CandleNode]:
        price = tick.mid
        if self._active is None:
            self._active = self._new_candle(tick.timestamp, price, tick.volume)
            return None, self._active

        if tick.timestamp >= self._active.start_time + self._interval:
            closed = replace(self._active, is_closed=True)
            self._active = self._new_candle(tick.timestamp, price, tick.volume)
            return closed, self._active

        self._active = replace(
            self._active,
            high_p=max(self._active.high_p, price),
            low_p=min(self._active.low_p, price),
            close_p=price,
            volume=self._active.volume + tick.volume,
            ticks_count=self._active.ticks_count + 1,
            end_time=tick.timestamp,
        )
        return None, self._active

    def _new_candle(self, timestamp: datetime, price: float, volume: int) -> CandleNode:
        return CandleNode(
            symbol=self._symbol,
            timeframe=self._timeframe,
            start_time=timestamp,
            end_time=timestamp,
            open_p=price,
            high_p=price,
            low_p=price,
            close_p=price,
            volume=volume,
            ticks_count=1,
        )
