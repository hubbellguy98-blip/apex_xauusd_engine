from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.backtest.ict_smc_backtest import (
    apply_spread_slippage,
    calculate_performance_metrics,
    clean_ohlcv_data,
    generate_backtest_report,
    run_backtest,
    simulate_order_fill,
    simulate_trade_management,
)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _candle(start: str, open_: float, high: float, low: float, close: float, *, closed: bool = True):
    opened = _dt(start)
    return {
        "timestamp": opened,
        "close_time": opened + timedelta(minutes=5),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000,
        "is_closed": closed,
    }


def _limit_order(**overrides):
    order = {
        "order_id": "BT_LIMIT_001",
        "symbol": "XAUUSD",
        "direction": "bullish",
        "order_type": "limit_order",
        "signal_time": "2026-06-04T10:15:00+00:00",
        "valid_from_time": "2026-06-04T10:15:00+00:00",
        "entry_price": 100.0,
        "stop_loss": 98.0,
        "target_1": 102.0,
        "final_target": 106.0,
    }
    order.update(overrides)
    return order


def test_clean_ohlcv_data_uses_closed_valid_candles_only_when_sliced_later() -> None:
    rows = [
        _candle("2026-06-04T10:00:00", 100, 102, 99, 101),
        _candle("2026-06-04T10:05:00", 101, 100, 102, 101),  # invalid high/low
        _candle("2026-06-04T10:10:00", 101, 103, 100, 102, closed=False),
    ]

    cleaned = clean_ohlcv_data(rows, "5M")

    assert len(cleaned) == 2
    assert cleaned[0]["close_time"] == "2026-06-04T10:05:00+00:00"
    assert cleaned[1]["is_closed"] is False


def test_limit_order_does_not_fill_retroactively_before_activation() -> None:
    order = _limit_order(valid_from_time="2026-06-04T10:15:00+00:00", entry_price=100.0)
    candle_that_touched_before_order_existed = _candle("2026-06-04T10:05:00", 102, 103, 99, 101)

    fill = simulate_order_fill(order, candle_that_touched_before_order_existed)

    assert fill["filled"] is False
    assert fill["reason"] in {"order_not_active", "no_retroactive_fill"}


def test_market_order_applies_spread_and_slippage_on_next_open() -> None:
    order = _limit_order(order_type="market_order", entry_price=100.0)
    next_candle = _candle("2026-06-04T10:15:00", 101.0, 103.0, 100.5, 102.0)

    fill = simulate_order_fill(
        order,
        next_candle,
        {"spread": 0.20},
        {"slippage": 0.05},
    )

    assert fill["filled"] is True
    assert fill["fill_price"] == 101.15
    assert apply_spread_slippage(101.0, "bearish", spread=0.20, slippage=0.05) == 100.85


def test_same_candle_stop_and_target_uses_conservative_stop_first() -> None:
    position = {
        "trade_id": "BT_001",
        "symbol": "XAUUSD",
        "direction": "bullish",
        "entry_price": 100.0,
        "stop_loss": 98.0,
        "current_stop": 98.0,
        "targets": [{"name": "target_1", "price": 104.0, "close_percent": 1.0}],
        "remaining_percent": 1.0,
        "realized_R": 0.0,
        "partials": [],
    }
    ambiguous = _candle("2026-06-04T10:20:00", 100, 105, 97.5, 102)

    result = simulate_trade_management(position, ambiguous, {"same_candle_policy": "conservative"})

    assert result["closed_trade"]["ambiguous_exit"] is True
    assert result["closed_trade"]["final_exit_reason"] == "ambiguous_stop_first"
    assert result["closed_trade"]["realized_R"] == -1.0


def test_partial_take_profit_tracks_weighted_r_and_breakeven_stop() -> None:
    position = {
        "trade_id": "BT_002",
        "symbol": "XAUUSD",
        "direction": "bullish",
        "entry_price": 100.0,
        "stop_loss": 98.0,
        "current_stop": 98.0,
        "targets": [
            {"name": "target_1", "price": 102.0, "close_percent": 0.5},
            {"name": "final_target", "price": 104.0, "close_percent": 0.5},
        ],
        "remaining_percent": 1.0,
        "realized_R": 0.0,
        "partials": [],
        "move_stop_to_be_after_target_1": True,
    }
    first = simulate_trade_management(position, _candle("2026-06-04T10:20:00", 100, 102.5, 99.5, 102))

    assert first["status"] == "open"
    assert first["position"]["realized_R"] == 0.5
    assert first["position"]["current_stop"] == 100.0

    second = simulate_trade_management(first["position"], _candle("2026-06-04T10:25:00", 102, 102.2, 99.8, 100))

    assert second["closed_trade"]["final_exit_reason"] == "breakeven_stop"
    assert second["closed_trade"]["realized_R"] == 0.5


def test_run_backtest_logs_trade_metrics_and_skips_news_restricted_signal() -> None:
    candles = [
        _candle("2026-06-04T10:00:00", 100, 101, 99, 100.5),
        _candle("2026-06-04T10:05:00", 100.5, 101, 99.5, 100.2),
        _candle("2026-06-04T10:10:00", 100.2, 100.8, 99.8, 100.1),
        _candle("2026-06-04T10:15:00", 100.1, 102.5, 99.9, 102.0),
        _candle("2026-06-04T10:20:00", 102.0, 104.5, 101.5, 104.0),
    ]
    signals = [
        _limit_order(
            order_id="BT_MARKET_001",
            order_type="market_order",
            signal_time="2026-06-04T10:10:00+00:00",
            valid_from_time="2026-06-04T10:10:00+00:00",
            entry_price=100.0,
            stop_loss=98.0,
            target_1=102.0,
            final_target=104.0,
        ),
        _limit_order(
            order_id="BT_SKIP_001",
            signal_time="2026-06-04T10:15:00+00:00",
            news_restricted=True,
        ),
    ]

    result = run_backtest(
        {"m5": candles},
        {
            "signals": signals,
            "execution_timeframe": "5M",
            "spread_model": {"spread": 0.0},
            "slippage_model": {"slippage": 0.0},
            "report": {"news_calendar_loaded": False, "minimum_sample_trades": 2},
        },
    )

    assert len(result["trade_log"]) == 1
    assert result["performance_metrics"]["total_trades"] == 1
    assert result["performance_metrics"]["wins"] == 1
    assert result["skipped_setup_log"][0]["reason"] == "news_restricted"
    assert "missing_news_calendar_for_xauusd" in result["report"]["warnings"]


def test_performance_metrics_include_profit_factor_and_drawdown() -> None:
    metrics = calculate_performance_metrics(
        [
            {"trade_id": "A", "realized_R": 2.0, "partials": [{"target": "target_1"}], "final_exit_reason": "final_target"},
            {"trade_id": "B", "realized_R": -1.0, "partials": [], "final_exit_reason": "stop_loss"},
            {"trade_id": "C", "realized_R": 0.0, "partials": [], "final_exit_reason": "breakeven_stop"},
        ]
    )

    assert metrics["total_trades"] == 3
    assert metrics["profit_factor"] == 2.0
    assert metrics["max_drawdown_R"] == 1.0
    assert metrics["target_1_hit_rate"] == 0.33333


def test_report_warns_on_ambiguous_exits() -> None:
    report = generate_backtest_report(
        [{"trade_id": "A", "realized_R": -1.0, "ambiguous_exit": True}],
        [],
        {"news_calendar_loaded": True, "minimum_sample_trades": 1},
    )

    assert "ambiguous_ohlc_exits_used_conservative_stop_first" in report["warnings"]
