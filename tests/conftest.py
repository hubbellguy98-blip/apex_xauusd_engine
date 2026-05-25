"""Shared test fixtures."""

import pytest

from src.strategy.state_manager import CentralRuntimeStateManager


@pytest.fixture
async def state_manager() -> CentralRuntimeStateManager:
    manager = CentralRuntimeStateManager()
    await manager.bootstrap()
    return manager


@pytest.fixture
async def active_event_bus():
    from src.core.events.event_bus import EventBus

    bus = EventBus()
    await bus.start()
    return bus
