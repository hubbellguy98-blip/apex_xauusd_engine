"""Structural market models used by liquidity and structure engines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, unique
from typing import Optional


@unique
class StructuralPointType(str, Enum):
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"


@unique
class StructureBreakType(str, Enum):
    BOS = "BREAK_OF_STRUCTURE"
    MSS = "MARKET_STRUCTURE_SHIFT"
    CHOCH = "CHANGE_OF_CHARACTER"


@dataclass(frozen=True, slots=True)
class SwingPoint:
    id: str
    symbol: str
    timeframe: str
    point_type: StructuralPointType
    timestamp: datetime
    price: float
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class LiquidityPool:
    id: str
    timeframe: str
    is_buy_side: bool
    is_equal_structure: bool
    ceiling_price: float
    floor_price: float
    accumulated_touches: int = 0
    is_swept: bool = False
    sweep_timestamp: Optional[datetime] = None
