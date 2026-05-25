"""Market data primitives shared by analytics and execution modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.core.domain.constants import EventPriority


@dataclass(frozen=True, slots=True)
class TickNode:
    symbol: str
    timestamp: datetime
    bid: float
    ask: float
    volume: int = 0
    sequence_id: int = 0
    trace_id: str = ""
    correlation_id: str = ""
    priority: EventPriority = EventPriority.NORMAL

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class CandleNode:
    symbol: str
    timeframe: str
    start_time: datetime
    end_time: datetime
    open_p: float
    high_p: float
    low_p: float
    close_p: float
    volume: int = 0
    ticks_count: int = 0
    is_closed: bool = False
    sequence_id: int = 0
    trace_id: str = ""
    correlation_id: str = ""
    priority: EventPriority = EventPriority.NORMAL
