"""Market data provider interface definitions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator, List

from src.core.domain.market_data import TickNode


class MarketDataProviderABC(ABC):
    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def subscribe(self, symbols: List[str]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stream_ticks(self) -> AsyncGenerator[TickNode, None]:
        raise NotImplementedError
