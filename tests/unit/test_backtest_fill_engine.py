"""Unit tests for explicit adverse simulation transaction costs."""

from datetime import datetime, timezone

import pytest

from src.backtesting.fill_engine import BacktestFillEngine
from src.core.domain.constants import OrderDirection
from src.core.domain.execution_models import OrderRequest


def _request(direction: OrderDirection) -> OrderRequest:
    return OrderRequest(
        client_order_id=f"SIM_{direction.value}",
        symbol="XAUUSD",
        direction=direction,
        quantity_lots=0.01,
        entry_price=2400.0,
        stop_loss=2398.0 if direction == OrderDirection.BUY else 2402.0,
        take_profit=2406.0 if direction == OrderDirection.BUY else 2394.0,
        idempotency_key=f"SIM_{direction.value}",
        timestamp=datetime.now(timezone.utc),
    )


def test_market_fills_apply_spread_and_adverse_slippage_by_direction() -> None:
    engine = BacktestFillEngine(
        spread_price=0.20,
        price_unit_per_pip=0.01,
        base_slippage_price=0.02,
        volatility_slippage_price=0.03,
    )
    timestamp = datetime.now(timezone.utc)

    buy = engine.fill_market_order(_request(OrderDirection.BUY), 2400.0, 2.0, timestamp)
    sell = engine.fill_market_order(_request(OrderDirection.SELL), 2400.0, 2.0, timestamp)

    assert buy.average_fill_price == pytest.approx(2400.15)
    assert sell.average_fill_price == pytest.approx(2399.85)
    assert buy.slippage_pips == pytest.approx(5.0)
    assert sell.slippage_pips == pytest.approx(5.0)


def test_simulator_refuses_costless_spread_assumption() -> None:
    with pytest.raises(ValueError, match="positive spread"):
        BacktestFillEngine(spread_price=0.0, price_unit_per_pip=0.01)
