"""Unit checks for live MT5 quote activity measurement."""

from datetime import datetime, timedelta, timezone

from src.core.domain.market_data import TickNode
from src.execution.pre_submission_guard import LiveQuoteActivityMonitor


def _tick(timestamp: datetime, bid: float) -> TickNode:
    return TickNode(symbol="GOLD.i#", timestamp=timestamp, bid=bid, ask=bid + 0.20)


def test_clock_shifted_broker_quote_becomes_fresh_after_observed_price_update() -> None:
    monitor = LiveQuoteActivityMonitor(maximum_inactivity_seconds=5.0)
    broker_clock = datetime.now(timezone.utc) + timedelta(hours=3)

    baseline = monitor.observe(_tick(broker_clock, 2400.00), observed_at_seconds=10.0)
    updated = monitor.observe(_tick(broker_clock + timedelta(seconds=1), 2400.10), observed_at_seconds=10.5)

    assert baseline.is_fresh is False
    assert updated.is_fresh is True
    assert updated.updates_observed == 1


def test_stream_becomes_inactive_after_no_observed_tick_change() -> None:
    monitor = LiveQuoteActivityMonitor(maximum_inactivity_seconds=5.0)
    now = datetime.now(timezone.utc)
    tick = _tick(now, 2400.00)
    monitor.observe(tick, observed_at_seconds=10.0)
    monitor.observe(_tick(now + timedelta(seconds=1), 2400.10), observed_at_seconds=11.0)

    inactive = monitor.observe(_tick(now + timedelta(seconds=1), 2400.10), observed_at_seconds=17.0)

    assert inactive.is_fresh is False
    assert inactive.quote_age_seconds == 6.0
