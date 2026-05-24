"""
Apex Engine - Domain Primitives & Structural Models
Responsibility: Immutable, type-safe data schemas representing core market states.
Latency Profile: Optimized allocation footprint using Python slots representation.
"""

from datetime import datetime
from typing import Any
from dataclasses import dataclass, field
from src.core.domain.constants import EventPriority

@dataclass(frozen=True, slots=True)
class BaseEvent:
    """Immutable baseline structure for all event communication instances."""
    timestamp: datetime
    sequence_id: int
    trace_id: str
    correlation_id: str
    priority: EventPriority = EventPriority.NORMAL

@dataclass(frozen=True, slots=True)
class MarketTick(BaseEvent):
    """Real-time microsecond pricing node for XAUUSD asset pairs."""
    symbol: str
    ask_price: float
    bid_price: float
    ask_volume: int
    bid_volume: int
    tick_sequence: int
    
    @property
    def mid_price(self) -> float:
        return (self.ask_price + self.bid_price) / 2.0
    
    @property
    def spread(self) -> float:
        return self.ask_price - self.bid_price

@dataclass(frozen=True, slots=True)
class InfrastructureTelemetry:
    """System health metrics payload."""
    component_name: str
    uptime_seconds: float
    active_tasks_count: int
    queue_backpressure_count: int
    memory_usage_bytes: int