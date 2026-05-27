"""Backtest configuration models."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    symbol: str
    start_time: datetime
    spread_price: float
    price_unit_per_pip: float
    end_time: datetime | None = None
    initial_balance: float = 10000.0
    latency_ms: float = 0.0
    base_slippage_price: float = 0.0
    volatility_slippage_price: float = 0.0

    def __post_init__(self) -> None:
        if self.spread_price <= 0.0:
            raise ValueError("Backtests require an explicit positive execution spread.")
        if self.price_unit_per_pip <= 0.0:
            raise ValueError("Backtests require an explicit positive price-unit-per-pip conversion.")
        if self.base_slippage_price < 0.0 or self.volatility_slippage_price < 0.0:
            raise ValueError("Backtest slippage assumptions cannot be negative.")
