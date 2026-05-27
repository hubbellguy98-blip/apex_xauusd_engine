"""Unit coverage for single-position staged protection policy."""

from datetime import datetime, timedelta, timezone

from src.core.domain.constants import OrderDirection
from src.core.domain.market_data import CandleNode
from src.execution.position_tracker import (
    InstitutionalTradeLifecycleManager,
    ManagedTradePlan,
    ManagedTradePlanStore,
)


def _closed_candle(close_price: float) -> CandleNode:
    end_time = datetime.now(timezone.utc)
    return CandleNode(
        symbol="GOLD.i#",
        timeframe="1m",
        start_time=end_time - timedelta(minutes=1),
        end_time=end_time,
        open_p=100.0,
        high_p=close_price,
        low_p=99.0,
        close_p=close_price,
        is_closed=True,
    )


def test_buffered_trailing_waits_for_confirmed_profit_milestone() -> None:
    manager = InstitutionalTradeLifecycleManager()

    touched_tp1 = manager.evaluate_candle_confirmed_trail(
        OrderDirection.BUY, 100.0, 98.0, 98.0, 112.0, _closed_candle(102.0)
    )
    confirmed_tp2 = manager.evaluate_candle_confirmed_trail(
        OrderDirection.BUY, 100.0, 98.0, 98.0, 112.0, _closed_candle(104.5)
    )

    assert touched_tp1.should_modify is False
    assert confirmed_tp2.should_modify is True
    assert confirmed_tp2.confirmed_milestone == 2
    assert confirmed_tp2.stop_loss == 101.7


def test_managed_trade_plan_persists_original_risk_frame(tmp_path) -> None:
    store = ManagedTradePlanStore(tmp_path / "managed_gold_trade.json")
    plan = ManagedTradePlan("GOLD.i#", 123, OrderDirection.SELL, 100.0, 102.0, 88.0, 2)

    store.save(plan)
    restored = store.load()

    assert restored == plan
