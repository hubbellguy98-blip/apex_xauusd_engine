from __future__ import annotations

from scripts.run_ict_smc_backtest import (
    _entry_drift_rejection_reason,
    _position_from_fill,
    _split_completed_and_mark_to_market,
)


def test_position_from_fill_preserves_normalized_target_ladder() -> None:
    order = {
        "order_id": "BT_001",
        "symbol": "GOLD.i#",
        "strategy": "breaker_block",
        "direction": "BUY",
        "entry_price": 4500.0,
        "stop_loss": 4495.0,
        "targets": [
            {"name": "target_1", "price": 4505.0, "close_percent": 0.5},
            {"name": "final_target", "price": 4515.0, "close_percent": 0.5},
        ],
    }
    fill = {"fill_price": 4500.25, "fill_time": "2026-06-01T00:01:00+00:00"}

    position = _position_from_fill(order, fill)

    assert position["targets"] == order["targets"]


def test_entry_drift_rejects_fill_that_invalidates_original_setup() -> None:
    order = {"entry_price": 4500.0, "stop_loss": 4495.0}
    fill = {"fill_price": 4503.0}

    reason = _entry_drift_rejection_reason(
        order,
        fill,
        max_price=1.5,
        max_risk_fraction=0.35,
    )

    assert reason is not None
    assert reason.startswith("entry_drift_exceeded")


def test_entry_drift_allows_small_fill_variation() -> None:
    order = {"entry_price": 4500.0, "stop_loss": 4495.0}
    fill = {"fill_price": 4500.5}

    reason = _entry_drift_rejection_reason(
        order,
        fill,
        max_price=1.5,
        max_risk_fraction=0.35,
    )

    assert reason is None


def test_end_of_test_trades_are_excluded_from_completed_metrics() -> None:
    completed, mark_to_market = _split_completed_and_mark_to_market(
        [
            {"trade_id": "A", "final_exit_reason": "stop_loss", "realized_R": -1.0},
            {"trade_id": "B", "final_exit_reason": "end_of_test", "realized_R": 12.0},
        ]
    )

    assert [trade["trade_id"] for trade in completed] == ["A"]
    assert [trade["trade_id"] for trade in mark_to_market] == ["B"]
