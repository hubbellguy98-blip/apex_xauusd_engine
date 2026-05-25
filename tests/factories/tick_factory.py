"""Tick factory helpers."""

from datetime import datetime, timezone

from src.core.domain.market_data import TickNode


class TickPrimitiveFactory:
    @staticmethod
    def create_tick(bid: float = 2400.0, ask: float = 2400.2, timestamp=None) -> TickNode:
        return TickNode(
            symbol="XAUUSD",
            timestamp=timestamp or datetime.now(timezone.utc),
            bid=bid,
            ask=ask,
            volume=1,
        )
