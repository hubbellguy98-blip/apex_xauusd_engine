"""Unit coverage for single-position staged protection policy."""

from datetime import datetime, timedelta, timezone

from src.core.domain.constants import OrderDirection
from src.core.domain.execution_models import PositionSnapshot
from src.core.domain.market_data import CandleNode
from src.execution.position_tracker import (
    InstitutionalTradeLifecycleManager,
    ManagedTradePlan,
    ManagedTradePlanReconciler,
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


def _position(
    ticket: int = 123,
    direction: OrderDirection = OrderDirection.SELL,
    entry: float = 100.0,
    stop_loss: float = 101.0,
    take_profit: float = 88.0,
) -> PositionSnapshot:
    return PositionSnapshot(
        symbol="GOLD.i#",
        ticket=ticket,
        direction=direction,
        net_quantity_lots=0.01,
        average_entry_price=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )


def test_startup_reconciliation_restores_only_exact_managed_position() -> None:
    reconciler = ManagedTradePlanReconciler()
    plan = ManagedTradePlan("GOLD.i#", 123, OrderDirection.SELL, 100.0, 102.0, 88.0, 2)

    result = reconciler.reconcile(plan, [_position()])

    assert result.active_plan == plan
    assert result.allows_automatic_management is True
    assert result.blocks_new_entries is True
    assert result.clear_stale_plan is False


def test_startup_reconciliation_clears_stale_plan_only_when_position_closed() -> None:
    reconciler = ManagedTradePlanReconciler()
    plan = ManagedTradePlan("GOLD.i#", 123, OrderDirection.SELL, 100.0, 102.0, 88.0, 2)

    result = reconciler.reconcile(plan, [])

    assert result.active_plan is None
    assert result.clear_stale_plan is True
    assert result.blocks_new_entries is False


def test_startup_reconciliation_disables_management_for_mismatched_position() -> None:
    reconciler = ManagedTradePlanReconciler()
    plan = ManagedTradePlan("GOLD.i#", 123, OrderDirection.SELL, 100.0, 102.0, 88.0, 2)

    result = reconciler.reconcile(plan, [_position(ticket=999)])

    assert result.active_plan is None
    assert result.allows_automatic_management is False
    assert result.blocks_new_entries is True
    assert result.clear_stale_plan is False
