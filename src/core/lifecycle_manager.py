"""Application lifecycle coordinator."""

from __future__ import annotations

import asyncio
from typing import List

from src.core.base_engine import BaseSubsystem
from src.core.events.event_bus import EventBus


class ApplicationLifecycleManager:
    """Bootstraps and terminates registered subsystems in order."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._subsystems: List[BaseSubsystem] = []
        self._is_terminating = False

    def register(self, subsystem: BaseSubsystem) -> None:
        self._subsystems.append(subsystem)

    async def initiate_bootstrap(self) -> None:
        await self._event_bus.start()
        for subsystem in self._subsystems:
            await subsystem.bootstrap()

    async def initiate_shutdown(self) -> None:
        self._is_terminating = True
        for subsystem in reversed(self._subsystems):
            await subsystem.terminate()
        await self._event_bus.stop()
        await asyncio.sleep(0)
