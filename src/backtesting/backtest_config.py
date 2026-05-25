"""Backtest configuration models."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    symbol: str
    start_time: datetime
    end_time: datetime | None = None
    initial_balance: float = 10000.0
    latency_ms: float = 0.0
