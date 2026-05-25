"""Candle factory helpers."""

from datetime import datetime, timezone

from src.core.domain.market_data import CandleNode


class CandlePrimitiveFactory:
    @staticmethod
    def create_candle(open_p: float = 2400.0, high_p: float = 2405.0, low_p: float = 2398.0, close_p: float = 2403.0) -> CandleNode:
        now = datetime.now(timezone.utc)
        return CandleNode(
            symbol="XAUUSD",
            timeframe="1m",
            start_time=now,
            end_time=now,
            open_p=open_p,
            high_p=high_p,
            low_p=low_p,
            close_p=close_p,
            volume=100,
            ticks_count=10,
            is_closed=True,
        )
