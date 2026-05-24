"""
Apex Engine - Async Event Bus Architecture
Responsibility: Memory-efficient Pub/Sub router featuring multi-tier priority queues.
Latency Profile: Async non-blocking dispatch path utilizing priority execution loops.
"""

import asyncio
from collections import defaultdict
from typing import Callable, Coroutine, Any, Dict, List
from src.core.events.event_types import EngineEventType
from src.core.domain.data_primitives import BaseEvent
import structlog

logger = structlog.get_logger()

class EventBus:
    """High-speed internal event distribution array for non-blocking processing loops."""
    
    def __init__(self) -> None:
        self._subscribers: Dict[EngineEventType, List[Callable[[Any], Coroutine[Any, Any, None]]]] = defaultdict(list)
        self._priority_queue: asyncio.PriorityQueue[tuple[int, BaseEvent]] = asyncio.PriorityQueue()
        self._processing_task: asyncio.Task[None] | None = None
        self._is_running: bool = False

    async def start(self) -> None:
        """Starts the asynchronous event routing consumer loop."""
        self._is_running = True
        self._processing_task = asyncio.create_task(self._drain_queue_loop())
        logger.info("event_bus.started", status="LIVE")

    async def stop(self) -> None:
        """Gracefully drains remaining event queues and halts distribution processing."""
        self._is_running = False
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
        logger.info("event_bus.stopped", status="TERMINATED")

    def subscribe(self, event_type: EngineEventType, callback: Callable[[Any], Coroutine[Any, Any, None]]) -> None:
        """Registers an asynchronous callback to listen for specific event types."""
        if callback not in self._subscribers[event_type]:
            self._subscribers[event_type].append(callback)
            logger.debug("event_bus.subscriber_registered", event_type=event_type, handler=callback.__name__)

    async def publish(self, event_type: EngineEventType, event_payload: BaseEvent) -> None:
        """Pushes an event payload into the priority distribution queue."""
        if not self._is_running:
            raise RuntimeError("Cannot publish events into an inactive routing engine context.")
        # Queue item follows shape: (priority_integer_value, payload)
        await self._priority_queue.put((event_payload.priority.value, (event_type, event_payload)))

    async def _drain_queue_loop(self) -> None:
        """Internal worker task loop that dispatches items based on priority rules."""
        while self._is_running:
            try:
                priority, (event_type, event_payload) = await self._priority_queue.get()
                handlers = self._subscribers.get(event_type, [])
                
                if handlers:
                    # Execute all subscribed handlers concurrently
                    await asyncio.gather(
                        *(handler(event_payload) for handler in handlers),
                        return_exceptions=True
                    )
                self._priority_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as ex:
                logger.error("event_bus.dispatch_failure", error=str(ex), event_type=event_type)