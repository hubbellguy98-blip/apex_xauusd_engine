"""Backtest layer for replay, optimization, and simulation abstractions."""

from src.backtest.ict_smc_backtest import (
    BacktestDirection,
    BacktestExitReason,
    BacktestOrderType,
    BacktestTradeResult,
    apply_spread_slippage,
    calculate_performance_metrics,
    clean_ohlcv_data,
    generate_backtest_report,
    place_pending_order,
    record_skipped_setup,
    record_trade,
    run_backtest,
    simulate_order_fill,
    simulate_trade_management,
)

__all__ = [
    "BacktestDirection",
    "BacktestExitReason",
    "BacktestOrderType",
    "BacktestTradeResult",
    "apply_spread_slippage",
    "calculate_performance_metrics",
    "clean_ohlcv_data",
    "generate_backtest_report",
    "optimization_matrix",
    "place_pending_order",
    "record_skipped_setup",
    "record_trade",
    "replay_engine",
    "run_backtest",
    "simulate_order_fill",
    "simulate_trade_management",
    "simulator_interface",
]
