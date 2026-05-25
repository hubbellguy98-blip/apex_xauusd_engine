"""Async polling helpers for tests."""

import asyncio
import time
from typing import Callable


class AsynchronousTestingHelpers:
    @staticmethod
    async def poll_condition_timeout(condition: Callable[[], bool], timeout_seconds: float = 1.0, interval_seconds: float = 0.01) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if condition():
                return True
            await asyncio.sleep(interval_seconds)
        return condition()
