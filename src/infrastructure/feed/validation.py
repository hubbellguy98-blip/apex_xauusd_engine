"""Market data validation guards."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

from src.core.domain.market_data import TickNode


class MarketDataValidator:
    """Rejects stale, duplicated, or structurally invalid ticks."""

    def __init__(self, max_stale_seconds: float = 5.0, deduplication_window: int = 100) -> None:
        self._max_stale_seconds = max_stale_seconds
        self._seen: deque[tuple[str, datetime, float, float]] = deque(maxlen=deduplication_window)

    def validate_tick(self, tick: TickNode) -> bool:
        if tick.ask < tick.bid:
            return False
        now = datetime.now(timezone.utc) if tick.timestamp.tzinfo else datetime.utcnow()
        if abs((now - tick.timestamp).total_seconds()) > self._max_stale_seconds:
            return False
        key = (tick.symbol, tick.timestamp, tick.bid, tick.ask)
        if key in self._seen:
            return False
        self._seen.append(key)
        return True
