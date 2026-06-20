from __future__ import annotations

import csv

from src.core.domain.constants import OrderDirection
from scripts.run_ict_smc_backtest import (
    _early_trap_filter_rejection,
    _entry_drift_rejection_reason,
    _post_cost_rr,
    _position_from_fill,
    _setup_to_market_signal,
    _split_completed_and_mark_to_market,
    _strict_profile_gate_errors,
    _target_ladder,
    _write_trade_csv,
)
from scripts.analyze_trade_log import analyze_trade_log


class _Setup:
    id = "BT_UNIT"
    setup_type = type("SetupTypeStub", (), {"value": "FVG_CONTINUATION"})()
    direction = OrderDirection.BUY
    entry_price = 100.0
    stop_loss = 98.0
    take_profit = 106.0
    estimated_rr = 3.0
    confidence_score = 90.0
    expiration_time = None


class _Definition:
    key = "sweep_mss_fvg"


class _Selected:
    definition = _Definition()
    signal = {"entry_mode": "fvg_midpoint"}
    estimated_rr = 3.0


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


def test_target_ladder_supports_fixed_six_r_profile() -> None:
    targets = _target_ladder(
        _Setup(),
        _Selected(),
        {"target_ladder": {"mode": "fixed_rr", "final_rr": 6.0, "milestones": [1, 2, 3, 4, 5, 6]}},
    )

    assert len(targets) == 6
    assert targets[-1]["name"] == "final_target"
    assert targets[-1]["price"] == 112.0


def test_post_cost_rr_uses_actual_fill_price() -> None:
    order = {
        "direction": "BUY",
        "stop_loss": 98.0,
        "final_target": 106.0,
    }
    fill = {"fill_price": 100.5}

    assert _post_cost_rr(order, fill) == 2.2


def test_expanded_trade_csv_schema_is_written(tmp_path) -> None:
    path = tmp_path / "trades.csv"
    _write_trade_csv(
        path,
        [
            {
                "trade_id": "A",
                "strategy": "sweep_mss_fvg",
                "symbol": "GOLD.i#",
                "direction": "bullish",
                "session_name": "NEWYORK_SESSION",
                "killzone_name": "NY Open",
                "post_cost_rr": 3.2,
                "duration_min": 42,
                "realized_R": 1.0,
            }
        ],
    )

    with path.open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))

    assert "post_cost_rr" in row
    assert "profile_name" in row
    assert "active_profile_hash" in row
    assert "selector_config_hash" in row
    assert "killzone_name" in row
    assert row["strategy"] == "sweep_mss_fvg"


def test_strict_profile_gate_rejects_non_compliant_completed_trades() -> None:
    result = {
        "completed_trade_log": [
            {
                "trade_id": "BAD_RR",
                "post_cost_rr": 2.4,
                "killzone_active": False,
                "killzone_name": "Silver Bullet PM",
                "duration_min": 240,
            }
        ]
    }
    profile = {
        "profile_name": "strict_intraday_xauusd",
        "minimum_rr": 3.0,
        "session_filters": {"require_killzone": True, "disabled_killzones": ["Silver Bullet PM"]},
        "management": {"max_hold_minutes": 180},
    }

    errors = _strict_profile_gate_errors(result, profile)

    assert any(error.startswith("strict_gate_post_cost_rr_below_minimum") for error in errors)
    assert any(error.startswith("strict_gate_no_killzone") for error in errors)
    assert any(error.startswith("strict_gate_disabled_killzone") for error in errors)
    assert any(error.startswith("strict_gate_max_hold_exceeded") for error in errors)


def test_early_trap_filter_rejects_no_followthrough_pattern() -> None:
    signal = {"direction": "BUY", "entry_price": 100.0, "stop_loss": 98.0, "setup_context": {}}
    context = {
        "candles": [
            {"index": 1, "open": 100.2, "high": 101.0, "low": 99.5, "close": 99.8},
            {"index": 2, "open": 99.9, "high": 100.1, "low": 98.2, "close": 99.7},
        ]
    }
    config = {
        "active_profile": {
            "early_trap_filter": {
                "enabled": True,
                "reject_if_no_followthrough_after_retest": True,
                "reject_if_retest_candle_closes_against_direction": True,
            }
        }
    }

    reason = _early_trap_filter_rejection(signal, context, config)

    assert reason in {"early_trap_filter_failed", "adverse_move_before_confirmation", "no_followthrough_after_retest"}


def test_analyze_trade_log_outputs_group_metrics(tmp_path) -> None:
    path = tmp_path / "trades.csv"
    _write_trade_csv(
        path,
        [
            {
                "trade_id": "A",
                "strategy": "sweep_mss_fvg",
                "symbol": "GOLD.i#",
                "direction": "bullish",
                "session_name": "NEWYORK_SESSION",
                "killzone_name": "NY Open",
                "entry_time": "2026-05-01T13:00:00+00:00",
                "duration_min": 30,
                "confidence_score": 72,
                "post_cost_rr": 3.1,
                "realized_R": 2.0,
            },
            {
                "trade_id": "B",
                "strategy": "sweep_mss_fvg",
                "symbol": "GOLD.i#",
                "direction": "bearish",
                "session_name": "LONDON_SESSION",
                "killzone_name": "London Open",
                "entry_time": "2026-05-01T07:00:00+00:00",
                "duration_min": 300,
                "confidence_score": 88,
                "post_cost_rr": 2.4,
                "realized_R": -1.0,
            },
        ],
    )

    analysis = analyze_trade_log(path)

    assert analysis["overall"]["trades"] == 2
    assert analysis["by_killzone"]["NY Open"]["net_R"] == 2.0
    assert "post_cost_rr_below_3_present" in analysis["warnings"]
    assert "duration_outliers_present" in analysis["warnings"]


def test_analyzer_supports_legacy_score_and_column_names(tmp_path) -> None:
    path = tmp_path / "legacy.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["trade_id", "score", "entry", "exit", "stop", "exit_reason", "tp1", "tp2", "realized_R", "post_cost_rr", "duration_min"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "trade_id": "L1",
                "score": 72,
                "entry": 100,
                "exit": 99,
                "stop": 98,
                "exit_reason": "stop_loss",
                "tp1": 101,
                "tp2": 102,
                "realized_R": -1,
                "post_cost_rr": 2.1,
                "duration_min": 12,
            }
        )

    analysis = analyze_trade_log(path)

    assert analysis["by_score_bucket"]["70-74"]["trades"] == 1
    assert "legacy_or_unprofiled_trade_log" in analysis["warnings"]
    assert analysis["early_exit_0_15m"]["trades"] == 1
