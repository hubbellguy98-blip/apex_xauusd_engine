"""
Apex Engine - Async Event Bus Architecture
Responsibility: Memory-efficient Pub/Sub router featuring multi-tier priority queues.
Latency Profile: Async non-blocking dispatch path utilizing priority execution loops.
"""

import asyncio
from collections import defaultdict
from itertools import count
from typing import Callable, Coroutine, Any, Dict, List
from src.core.events.event_types import EngineEventType
from src.core.domain.data_primitives import BaseEvent
import structlog

logger = structlog.get_logger()

class EventBus:
    """High-speed internal event distribution array for non-blocking processing loops."""
    
    def __init__(self, max_queue_size: int = 10000) -> None:
        if max_queue_size <= 0:
            raise ValueError("Event bus queue size must be positive.")
        self._subscribers: Dict[EngineEventType, List[Callable[[Any], Coroutine[Any, Any, None]]]] = defaultdict(list)
        self._priority_queue: asyncio.PriorityQueue[tuple[int, int, EngineEventType, BaseEvent]] = asyncio.PriorityQueue(
            maxsize=max_queue_size
        )
        self._publish_sequence = count()
        self._handler_failure_count = 0
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
        # Sequence preserves deterministic FIFO ordering when priorities match.
        await self._priority_queue.put(
            (event_payload.priority.value, next(self._publish_sequence), event_type, event_payload)
        )

    async def _drain_queue_loop(self) -> None:
        """Internal worker task loop that dispatches items based on priority rules."""
        while self._is_running:
            try:
                _priority, _sequence, event_type, event_payload = await self._priority_queue.get()
                handlers = self._subscribers.get(event_type, [])
                
                if handlers:
                    results = await asyncio.gather(
                        *(handler(event_payload) for handler in handlers),
                        return_exceptions=True
                    )
                    for handler, result in zip(handlers, results):
                        if isinstance(result, BaseException):
                            self._handler_failure_count += 1
                            logger.error(
                                "event_bus.handler_failure",
                                event_type=event_type.value,
                                handler=getattr(handler, "__name__", repr(handler)),
                                error=str(result),
                            )
                self._priority_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as ex:
                self._handler_failure_count += 1
                logger.error("event_bus.dispatch_failure", error=str(ex))

    @property
    def handler_failure_count(self) -> int:
        """Return observed subscriber/dispatch failures for health monitoring."""
        return self._handler_failure_count
