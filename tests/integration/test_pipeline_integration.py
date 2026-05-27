"""
Apex Engine - Event Bus Communications Integration Suite
Responsibility: Verifies message prioritization and routes data nodes through async pipelines.
Latency Profile: Evaluates event queue processing under simulated load.
"""

import asyncio
import pytest
from typing import Any
from src.core.events.event_bus import EventBus
from src.core.domain.constants import EventPriority
from src.core.events.event_types import EngineEventType
from tests.factories.tick_factory import TickPrimitiveFactory
from tests.utils.async_helpers import AsynchronousTestingHelpers

@pytest.mark.integration
@pytest.mark.asyncio
async def test_priority_queue_message_routing_precedence() -> None:
    """Verifies that the event bus routes high-priority data updates before low-priority tasks."""
    active_event_bus = EventBus()
    received_order_audit_trail = []

    async def low_priority_subscriber(event: Any) -> None:
        received_order_audit_trail.append("LOW_PRIORITY_NODE")

    async def high_priority_subscriber(event: Any) -> None:
        received_order_audit_trail.append("HIGH_PRIORITY_NODE")

    active_event_bus.subscribe(EngineEventType.CANDLE_CLOSED, low_priority_subscriber)
    active_event_bus.subscribe(EngineEventType.MARKET_TICK, high_priority_subscriber)

    # Construct test payloads using precise priority properties
    tick_low = TickPrimitiveFactory.create_tick(bid=2400.0, ask=2400.2)
    from dataclasses import replace
    tick_low_modified = replace(tick_low, priority=EventPriority.LOW)
    
    tick_critical = TickPrimitiveFactory.create_tick(bid=2401.0, ask=2401.2)
    tick_critical_modified = replace(tick_critical, priority=EventPriority.CRITICAL)

    # Stage traffic before starting the drain worker so ordering is deterministic.
    active_event_bus._is_running = True
    await active_event_bus.publish(EngineEventType.CANDLE_CLOSED, tick_low_modified)
    await active_event_bus.publish(EngineEventType.MARKET_TICK, tick_critical_modified)

    active_event_bus._processing_task = asyncio.create_task(active_event_bus._drain_queue_loop())

    is_completed = await AsynchronousTestingHelpers.poll_condition_timeout(
        lambda: len(received_order_audit_trail) == 2, timeout_seconds=1.0
    )
    
    assert is_completed is True
    assert received_order_audit_trail[0] == "HIGH_PRIORITY_NODE"
    await active_event_bus.stop()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_equal_priority_events_remain_fifo_and_report_handler_failure() -> None:
    bus = EventBus()
    received = []

    async def failing_subscriber(event: Any) -> None:
        raise RuntimeError("deliberate subscriber failure")

    async def observing_subscriber(event: Any) -> None:
        received.append(event.bid)

    bus.subscribe(EngineEventType.MARKET_TICK, failing_subscriber)
    bus.subscribe(EngineEventType.MARKET_TICK, observing_subscriber)
    bus._is_running = True
    await bus.publish(EngineEventType.MARKET_TICK, TickPrimitiveFactory.create_tick(bid=2400.0, ask=2400.2))
    await bus.publish(EngineEventType.MARKET_TICK, TickPrimitiveFactory.create_tick(bid=2401.0, ask=2401.2))
    bus._processing_task = asyncio.create_task(bus._drain_queue_loop())

    completed = await AsynchronousTestingHelpers.poll_condition_timeout(lambda: len(received) == 2)

    assert completed is True
    assert received == [2400.0, 2401.0]
    assert bus.handler_failure_count == 2
    await bus.stop()
