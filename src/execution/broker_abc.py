"""Broker gateway interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator, List

from src.core.domain.execution_models import ExecutionReport, OrderRequest, PositionSnapshot


class BrokerGatewayABC(ABC):
    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def route_order_submission(self, request: OrderRequest) -> ExecutionReport:
        raise NotImplementedError

    @abstractmethod
    async def stream_execution_lifecycle_events(self) -> AsyncGenerator[ExecutionReport, None]:
        raise NotImplementedError

    @abstractmethod
    async def query_live_positions(self) -> List[PositionSnapshot]:
        raise NotImplementedError
