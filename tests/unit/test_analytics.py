"""
Apex Engine - Market Data Ingestion Unit Verification Suite
Responsibility: Verifies pricing tick validation matrices and downsampled candle construction.
Latency Profile: High-speed structural execution test frames.
"""

import pytest
from datetime import datetime, timezone
from src.core.domain.market_data import TickNode
from src.infrastructure.feed.validation import MarketDataValidator
from src.analytics.candle_builder import IncrementalCandleBuilder
from tests.factories.tick_factory import TickPrimitiveFactory

@pytest.mark.unit
def test_tick_validation_invariants_checker() -> None:
    """Verifies that data cleaning layers catch malformed or inverted spreads."""
    validator = MarketDataValidator(max_stale_seconds=5.0, deduplication_window=10)
    
    # Valid structural configuration tracking node verification
    clean_tick = TickPrimitiveFactory.create_tick(bid=2400.0, ask=2400.2)
    assert validator.validate_tick(clean_tick) is True

    # Inverted spread structural verification check node detection
    inverted_tick = TickPrimitiveFactory.create_tick(bid=2400.5, ask=2400.1)
    assert validator.validate_tick(inverted_tick) is False

@pytest.mark.unit
def test_incremental_candle_building_boundary_close() -> None:
    """Verifies that candle builders construct downsampled bars correctly without time drift."""
    builder = IncrementalCandleBuilder(symbol="XAUUSD", timeframe="1m", interval_seconds=60)
    base_epoch = 1779868800  # Fixed operational test epoch coordinate matching 2026 timelines
    
    t1 = TickPrimitiveFactory.create_tick(bid=2400.0, ask=2400.0, timestamp=datetime.fromtimestamp(base_epoch, tz=timezone.utc))
    t2 = TickPrimitiveFactory.create_tick(bid=2405.0, ask=2405.0, timestamp=datetime.fromtimestamp(base_epoch + 30, tz=timezone.utc))
    t3 = TickPrimitiveFactory.create_tick(bid=2402.0, ask=2402.0, timestamp=datetime.fromtimestamp(base_epoch + 65, tz=timezone.utc))

    closed_1, active_1 = builder.process_tick(t1)
    assert closed_1 is None
    assert active_1.open_p == 2400.0

    closed_2, active_2 = builder.process_tick(t2)
    assert closed_2 is None
    assert active_2.high_p == 2405.0

    # Retest boundary breach segment execution validation
    closed_3, active_3 = builder.process_tick(t3)
    assert closed_3 is not None
    assert closed_3.close_p == 2405.0
    assert closed_3.is_closed is True
    assert active_3.open_p == 2402.0