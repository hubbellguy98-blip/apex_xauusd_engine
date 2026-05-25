"""Execution request and broker response models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, unique
from typing import Optional

from src.core.domain.constants import OrderDirection


@unique
class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


@unique
class OrderStatus(str, Enum):
    PENDING_SUBMIT = "PENDING_SUBMIT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    direction: OrderDirection
    quantity_lots: float
    entry_price: float
    stop_loss: float
    take_profit: float
    idempotency_key: str
    timestamp: datetime
    order_type: OrderType = OrderType.MARKET


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    execution_id: str
    client_order_id: str
    broker_order_id: str
    timestamp: datetime
    status: OrderStatus
    filled_quantity: float
    remaining_quantity: float
    average_fill_price: float
    last_fill_price: float
    slippage_pips: float
    rejection_reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    symbol: str
    net_quantity_lots: float
    average_entry_price: float
    floating_pnl_pips: float = 0.0
