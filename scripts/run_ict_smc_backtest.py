"""Full-system ICT/SMC backtest runner.

This runner replays historical closed candles through the same ICT/SMC strategy
selector used by the live setup orchestrator, then simulates conservative market
fills, stops, targets, spread, slippage, and one-position-at-a-time execution.
It never sends broker orders.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import subprocess
import sys
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import structlog

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.liquidity_engine import LiquidityInterceptionEngine
from src.analytics.session_engine import GoldSessionIntelligenceEngine
from src.analytics.structure_engine import DeterministicStructureEngine
from src.backtest.ict_smc_backtest import (
    BacktestExitReason,
    BacktestOrderType,
    calculate_performance_metrics,
    generate_backtest_report,
    place_pending_order,
    record_skipped_setup,
    record_trade,
    simulate_order_fill,
    simulate_trade_management,
)
from src.core.domain.constants import OrderDirection
from src.core.domain.market_data import CandleNode, TickNode
from src.execution.stop_loss_engine import DynamicStructuralStopEngine
from src.infrastructure.broker.mt5_config import load_mt5_config
from src.strategy.ict_smc_strategy_selector import ICTSMCStrategySelector, StrategyEvaluation

TIMEFRAME_MINUTES = {"1m": 1, "15m": 15, "1h": 60, "4h": 240}
DEFAULT_PROFILE_PATH = ROOT / "config" / "backtest_profiles.json"


@dataclass(frozen=True, slots=True)
class BacktestInputs:
    symbol: str
    candles_by_timeframe: dict[str, list[CandleNode]]
    source: str


@dataclass(slots=True)
class ReplayState:
    candles_by_timeframe: dict[str, deque[CandleNode]]
    timeframe_indices: dict[str, int]
    structural_pivots: list[Any]
    latest_sweep_event: dict[str, Any] | None
    active_liquidity_pools: list[dict[str, Any]]
    strategy_evaluations: int = 0
    selected_signals: int = 0
    rejected_cycles: int = 0
    skipped_by_cooldown: int = 0
    skipped_by_open_position: int = 0
    skipped_by_entry_drift: int = 0
    stop_hardened: int = 0


class FullSystemICTSMCBacktester:
    """Replay closed candles through the current ICT/SMC selector."""

    def __init__(
        self,
        *,
        symbol: str,
        spread_price: float,
        slippage_price: float,
        warmup_bars: int,
        max_concurrent_positions: int,
        strategy_cooldown_minutes: int,
        harden_stops: bool,
        max_entry_drift_price: float | None,
        max_entry_drift_risk_fraction: float,
    ) -> None:
        self.symbol = symbol
        self.spread_price = max(0.0, spread_price)
        self.slippage_price = max(0.0, slippage_price)
        self.warmup_bars = max(1, warmup_bars)
        self.max_concurrent_positions = max(1, max_concurrent_positions)
        self.strategy_cooldown = timedelta(minutes=max(0, strategy_cooldown_minutes))
        self.harden_stops = harden_stops
        self.max_entry_drift_price = max_entry_drift_price
        self.max_entry_drift_risk_fraction = max(0.0, max_entry_drift_risk_fraction)
        self.selector = ICTSMCStrategySelector()
        self.session_engine = GoldSessionIntelligenceEngine()
        self.structure_engine = DeterministicStructureEngine("1m")
        self.liquidity_engine = LiquidityInterceptionEngine("1m")
        self.stop_hardener = DynamicStructuralStopEngine()
        self.cooldown_registry: dict[str, datetime] = {}

    def run(self, candles_by_timeframe: Mapping[str, Sequence[CandleNode]], config: Mapping[str, Any]) -> dict[str, Any]:
        primary = list(candles_by_timeframe.get("1m", []))
        if len(primary) < self.warmup_bars + 2:
            raise ValueError(
                f"Need at least {self.warmup_bars + 2} closed 1m candles, got {len(primary)}. "
                "Use a wider --from/--to window."
            )

        state = ReplayState(
            candles_by_timeframe={timeframe: deque(maxlen=160) for timeframe in TIMEFRAME_MINUTES},
            timeframe_indices={timeframe: 0 for timeframe in TIMEFRAME_MINUTES if timeframe != "1m"},
            structural_pivots=[],
            latest_sweep_event=None,
            active_liquidity_pools=[],
        )
        pending_orders: list[dict[str, Any]] = []
        open_positions: list[dict[str, Any]] = []
        trade_log: list[dict[str, Any]] = []
        skipped_log: list[dict[str, Any]] = []
        signal_log: list[dict[str, Any]] = []
        strategy_counter: Counter[str] = Counter()
        tradeable_strategy_counter: Counter[str] = Counter()
        blocked_strategy_counter: Counter[str] = Counter()
        status_counter: Counter[str] = Counter()
        rejection_counter: Counter[str] = Counter()

        for index, candle in enumerate(primary):
            self._advance_higher_timeframes(candles_by_timeframe, state, candle.end_time)
            state.candles_by_timeframe["1m"].append(candle)
            self._ingest_structure_and_liquidity(candle, state)

            tick = _synthetic_tick(self.symbol, candle, self.spread_price, index)
            session_context = self.session_engine.evaluate_session_context(candle.end_time, tick.mid)
            candle_payload = _candle_to_backtest_row(candle)
            candle_payload.update(
                {
                    "session_name": session_context.session_name,
                    "killzone_active": session_context.killzone_active,
                    "killzone_name": session_context.killzone_name,
                }
            )
            for position in list(open_positions):
                managed = simulate_trade_management(position, candle_payload, dict(config.get("management", {})))
                if managed["closed_trade"]:
                    trade_log.append(record_trade(managed["closed_trade"]))
                    open_positions.remove(position)
                else:
                    position.update(managed["position"])

            for order in list(pending_orders):
                fill = simulate_order_fill(
                    order,
                    candle_payload,
                    {"spread": self.spread_price},
                    {"slippage": self.slippage_price},
                )
                if fill["filled"]:
                    drift_reason = _entry_drift_rejection_reason(
                        order,
                        fill,
                        max_price=self.max_entry_drift_price,
                        max_risk_fraction=self.max_entry_drift_risk_fraction,
                    )
                    if drift_reason:
                        skipped_log.append(record_skipped_setup(order, drift_reason))
                        state.skipped_by_entry_drift += 1
                        pending_orders.remove(order)
                        continue
                    post_cost_rr = _post_cost_rr(order, fill)
                    minimum_rr = float(config.get("selector", {}).get("minimum_rr", config.get("minimum_rr", 1.5)))
                    if post_cost_rr < minimum_rr:
                        skipped_log.append(record_skipped_setup(order, f"post_cost_rr_below_minimum:{round(post_cost_rr, 4)}"))
                        pending_orders.remove(order)
                        continue
                    position = _position_from_fill(order, fill)
                    position["post_cost_rr"] = round(post_cost_rr, 5)
                    immediate = simulate_trade_management(position, candle_payload, dict(config.get("management", {})))
                    if immediate["closed_trade"]:
                        trade_log.append(record_trade(immediate["closed_trade"]))
                    else:
                        open_positions.append(immediate["position"])
                    pending_orders.remove(order)
                elif fill["reason"] == "order_expired":
                    skipped_log.append(record_skipped_setup(order, "pending_order_expired"))
                    pending_orders.remove(order)

            if index + 1 < self.warmup_bars:
                continue

            swept_pools = self.liquidity_engine.evaluate_tick_sweeps(tick)
            state.active_liquidity_pools = self.liquidity_engine.active_pools_snapshot()
            context = self._build_strategy_context(tick, state, swept_pools, session_context)
            selection = self.selector.evaluate(context, config.get("selector", {}))
            state.strategy_evaluations += len(selection.evaluations)
            _record_strategy_evaluations(
                selection.evaluations,
                tradeable_strategy_counter,
                status_counter,
                rejection_counter,
            )

            if not selection.selected:
                state.rejected_cycles += 1
                continue

            selected = selection.selected
            if len(open_positions) + len(pending_orders) >= self.max_concurrent_positions:
                state.skipped_by_open_position += 1
                blocked_strategy_counter[selected.definition.key] += 1
                skipped_log.append(
                    record_skipped_setup(_signal_stub(selected, candle.end_time), "blocked_existing_position")
                )
                continue

            cooldown_key = f"{selected.definition.key}_{selected.direction.value if selected.direction else 'UNKNOWN'}"
            current_time = candle.end_time.replace(tzinfo=None)
            if current_time < self.cooldown_registry.get(cooldown_key, datetime.min):
                state.skipped_by_cooldown += 1
                skipped_log.append(record_skipped_setup(_signal_stub(selected, candle.end_time), "strategy_cooldown_active"))
                continue
            self.cooldown_registry[cooldown_key] = current_time + self.strategy_cooldown

            setup = self.selector.build_setup_node(
                selected,
                setup_id=f"BT_{selected.definition.key.upper()}_{index}_{int(candle.end_time.timestamp())}",
                now=current_time,
                correlation_id=f"BACKTEST_{index}",
                timeframe="1m",
            )
            if self.harden_stops:
                hardened = self.stop_hardener.harden_for_demo_execution(
                    setup,
                    tuple(state.candles_by_timeframe["1m"]),
                    self.spread_price,
                )
                if hardened.adjusted:
                    state.stop_hardened += 1
                    setup = hardened.setup

            signal = _setup_to_market_signal(setup, selected, candle.end_time, self.symbol, config, context)
            pending_orders.append(place_pending_order(signal, {"order_type": signal.get("order_type")}))
            signal_log.append(signal)
            strategy_counter[selected.definition.key] += 1
            state.selected_signals += 1

        if bool(config.get("close_open_positions_at_end", True)) and primary:
            final_candle = _candle_to_backtest_row(primary[-1])
            for position in list(open_positions):
                trade_log.append(record_trade(_close_position_at_end(position, final_candle)))
                open_positions.remove(position)

        completed_trade_log, mark_to_market_trade_log = _split_completed_and_mark_to_market(trade_log)
        report = generate_backtest_report(
            completed_trade_log,
            skipped_log,
            {
                "news_calendar_loaded": bool(config.get("news_calendar_loaded", False)),
                "minimum_sample_trades": int(config.get("minimum_sample_trades", 30)),
                "minimum_rr": float(config.get("selector", {}).get("minimum_rr", config.get("minimum_rr", 3.0))),
                "strategy_summary": dict(strategy_counter),
                "data_summary": {
                    "symbol": self.symbol,
                    "source": config.get("source"),
                    "profile_name": config.get("profile_name"),
                    "candles_1m": len(primary),
                    "timeframe_counts": {timeframe: len(candles_by_timeframe.get(timeframe, [])) for timeframe in TIMEFRAME_MINUTES},
                    "from": primary[0].end_time.isoformat(),
                    "to": primary[-1].end_time.isoformat(),
                    "spread_price": self.spread_price,
                    "slippage_price": self.slippage_price,
                    "warmup_bars": self.warmup_bars,
                    "stop_hardening_enabled": self.harden_stops,
                    "max_entry_drift_price": self.max_entry_drift_price,
                    "max_entry_drift_risk_fraction": self.max_entry_drift_risk_fraction,
                    "git": config.get("git", {}),
                    "command_args": config.get("command_args", {}),
                    "active_profile": config.get("active_profile", {}),
                    "selector_config": config.get("selector", {}),
                },
            },
        )
        if mark_to_market_trade_log:
            report["warnings"] = list(report.get("warnings", [])) + ["open_positions_marked_to_market_separately"]
        return {
            "function": "run_full_system_ict_smc_backtest",
            "trade_log": trade_log,
            "completed_trade_log": completed_trade_log,
            "mark_to_market_trade_log": mark_to_market_trade_log,
            "signal_log": signal_log,
            "skipped_setup_log": skipped_log,
            "performance_metrics": calculate_performance_metrics(completed_trade_log),
            "mark_to_market_metrics": calculate_performance_metrics(mark_to_market_trade_log),
            "report": report,
            "diagnostics": {
                "strategy_evaluations": state.strategy_evaluations,
                "selected_signals": state.selected_signals,
                "tradeable_signals_observed": sum(tradeable_strategy_counter.values()),
                "rejected_cycles": state.rejected_cycles,
                "cooldown_skips": state.skipped_by_cooldown,
                "open_position_skips": state.skipped_by_open_position,
                "entry_drift_skips": state.skipped_by_entry_drift,
                "stop_hardened": state.stop_hardened,
                "strategy_counts": dict(strategy_counter),
                "tradeable_strategy_counts": dict(tradeable_strategy_counter),
                "blocked_by_open_position_strategy_counts": dict(blocked_strategy_counter),
                "evaluation_status_counts": dict(status_counter),
                "top_rejection_reasons": dict(rejection_counter.most_common(15)),
                "open_positions_at_end": len(open_positions),
                "pending_orders_at_end": len(pending_orders),
            },
        }

    def _advance_higher_timeframes(
        self,
        candles_by_timeframe: Mapping[str, Sequence[CandleNode]],
        state: ReplayState,
        eval_time: datetime,
    ) -> None:
        for timeframe in ("15m", "1h", "4h"):
            candles = candles_by_timeframe.get(timeframe, [])
            idx = state.timeframe_indices.get(timeframe, 0)
            while idx < len(candles) and candles[idx].end_time <= eval_time:
                state.candles_by_timeframe[timeframe].append(candles[idx])
                idx += 1
            state.timeframe_indices[timeframe] = idx

    def _ingest_structure_and_liquidity(self, candle: CandleNode, state: ReplayState) -> None:
        new_pivots, _ = self.structure_engine.ingest_candle_close(candle)
        for pivot in new_pivots:
            self.liquidity_engine.register_structural_pivot_pool(pivot)
        state.structural_pivots.extend(new_pivots)
        state.active_liquidity_pools = self.liquidity_engine.active_pools_snapshot()

    def _build_strategy_context(
        self,
        tick: TickNode,
        state: ReplayState,
        swept_pools: Sequence[tuple[Any, float]],
        session_context: Any,
    ) -> dict[str, Any]:
        candles_by_tf = {
            timeframe: [_candle_payload(candle, idx) for idx, candle in enumerate(candles)]
            for timeframe, candles in state.candles_by_timeframe.items()
        }
        candles_1m = candles_by_tf.get("1m", [])
        swept_payloads = [_swept_pool_payload(pool, depth) for pool, depth in swept_pools]
        liquidity_pools = state.active_liquidity_pools + swept_payloads
        swings = [_swing_payload(pivot, idx) for idx, pivot in enumerate(state.structural_pivots[-100:])]
        bias = _directional_bias(candles_by_tf)
        latest_sweep = swept_payloads[-1] if swept_payloads else None
        session_value = str(getattr(session_context, "session_name", session_context))
        killzone_active = bool(getattr(session_context, "killzone_active", False))
        killzone_name = getattr(session_context, "killzone_name", None)
        return {
            "symbol": tick.symbol,
            "timestamp": tick.timestamp,
            "current_price": tick.mid,
            "current_bid": tick.bid,
            "current_ask": tick.ask,
            "candles": candles_1m,
            "setup_df": candles_1m,
            "entry_df": candles_1m,
            "ltf_df": candles_1m,
            "m15_df": candles_by_tf.get("15m", candles_1m),
            "htf_df": candles_by_tf.get("1h", candles_by_tf.get("4h", candles_1m)),
            "candles_by_timeframe": candles_by_tf,
            "liquidity_pools": liquidity_pools,
            "ltf_liquidity_pools": liquidity_pools,
            "htf_liquidity_targets": liquidity_pools,
            "target_liquidity": _select_target_liquidity(tick.mid, liquidity_pools, latest_sweep),
            "latest_sweep_event": latest_sweep,
            "starting_liquidity_event": latest_sweep,
            "swings": swings,
            "structure_swings": swings,
            "ltf_swings": swings,
            "htf_bias": {
                "bias_direction": _bias_to_strategy_text(bias.get("1h") or bias.get("4h")),
                "timeframe_bias": bias,
                "confidence_score": 7.5,
            },
            "higher_timeframe_bias": _bias_to_strategy_text(bias.get("4h") or bias.get("1h")),
            "session_context": {
                "session": session_value,
                "session_name": session_value,
                "killzone_active": killzone_active,
                "killzone_name": killzone_name,
            },
            "session": session_value,
            "spread_status": {
                "spread_points": tick.spread,
                "spread": tick.spread,
                "average_spread": max(tick.spread, 0.01),
            },
            "news_status": {"restricted": False, "high_impact_recent": False, "post_news_window_active": False},
            "price_location": _price_location(tick.mid, candles_1m),
        }


def load_inputs(args: argparse.Namespace) -> BacktestInputs:
    symbol = args.symbol
    if args.source == "csv" or args.csv_1m:
        if not args.csv_1m:
            raise ValueError("CSV source requires --csv-1m.")
        candles_by_timeframe = {
            "1m": load_csv_candles(Path(args.csv_1m), symbol, "1m"),
            "15m": load_csv_candles(Path(args.csv_15m), symbol, "15m") if args.csv_15m else [],
            "1h": load_csv_candles(Path(args.csv_1h), symbol, "1h") if args.csv_1h else [],
            "4h": load_csv_candles(Path(args.csv_4h), symbol, "4h") if args.csv_4h else [],
        }
        return BacktestInputs(
            symbol=symbol,
            source="csv",
            candles_by_timeframe=derive_missing_timeframes(candles_by_timeframe, symbol),
        )
    return load_mt5_candles(args)


def load_mt5_candles(args: argparse.Namespace) -> BacktestInputs:
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("MetaTrader5 package is not installed. Use --source csv or install MetaTrader5.") from exc

    config = load_mt5_config(ROOT / ".env")
    symbol = args.symbol or config.symbol
    started = _parse_cli_datetime(args.from_date)
    ended = _parse_cli_datetime(args.to_date)
    initialized = mt5.initialize(path=config.terminal_path, login=config.login, password=config.password, server=config.server)
    if not initialized:
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Could not select MT5 symbol {symbol!r}: {mt5.last_error()}")
        candles_by_tf = {
            "1m": _copy_mt5_rates(mt5, symbol, mt5.TIMEFRAME_M1, "1m", started, ended),
            "15m": _copy_mt5_rates(mt5, symbol, mt5.TIMEFRAME_M15, "15m", started, ended),
            "1h": _copy_mt5_rates(mt5, symbol, mt5.TIMEFRAME_H1, "1h", started, ended),
            "4h": _copy_mt5_rates(mt5, symbol, mt5.TIMEFRAME_H4, "4h", started, ended),
        }
    finally:
        mt5.shutdown()
    return BacktestInputs(symbol=symbol, source="mt5", candles_by_timeframe=derive_missing_timeframes(candles_by_tf, symbol))


def load_csv_candles(path: Path, symbol: str, timeframe: str) -> list[CandleNode]:
    rows: list[CandleNode] = []
    minutes = TIMEFRAME_MINUTES[timeframe]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader):
            timestamp = _parse_row_datetime(row)
            start = timestamp - timedelta(minutes=minutes) if _looks_like_close_time(row) else timestamp
            end = timestamp if _looks_like_close_time(row) else timestamp + timedelta(minutes=minutes)
            rows.append(
                CandleNode(
                    symbol=symbol,
                    timeframe=timeframe,
                    start_time=start,
                    end_time=end,
                    open_p=float(_row_value(row, "open")),
                    high_p=float(_row_value(row, "high")),
                    low_p=float(_row_value(row, "low")),
                    close_p=float(_row_value(row, "close")),
                    volume=int(float(_row_value(row, "volume", default="0"))),
                    ticks_count=int(float(_row_value(row, "tick_volume", "volume", default="0"))),
                    is_closed=True,
                    sequence_id=index,
                    correlation_id=f"CSV_{timeframe}_{index}",
                )
            )
    return sorted(rows, key=lambda candle: candle.end_time)


def derive_missing_timeframes(
    candles_by_timeframe: Mapping[str, Sequence[CandleNode]],
    symbol: str,
) -> dict[str, list[CandleNode]]:
    """Build missing higher timeframe candles from 1m candles.

    CSV exports often start with only M1 data. The live strategy selector uses
    15m/1h/4h context too, so deriving closed higher-timeframe bars keeps the
    offline test closer to the live decision path.
    """

    result = {timeframe: list(candles_by_timeframe.get(timeframe, [])) for timeframe in TIMEFRAME_MINUTES}
    source = result.get("1m", [])
    if not source:
        return result
    for timeframe, minutes in (("15m", 15), ("1h", 60), ("4h", 240)):
        if not result.get(timeframe):
            result[timeframe] = _aggregate_timeframe(source, symbol, timeframe, minutes)
    return result


def _aggregate_timeframe(
    candles: Sequence[CandleNode],
    symbol: str,
    timeframe: str,
    minutes: int,
) -> list[CandleNode]:
    grouped: dict[int, list[CandleNode]] = {}
    bucket_seconds = minutes * 60
    for candle in candles:
        bucket = int(candle.start_time.timestamp()) // bucket_seconds
        grouped.setdefault(bucket, []).append(candle)

    aggregated: list[CandleNode] = []
    for sequence_id, bucket in enumerate(sorted(grouped)):
        rows = sorted(grouped[bucket], key=lambda item: item.start_time)
        if not rows:
            continue
        aggregated.append(
            CandleNode(
                symbol=symbol,
                timeframe=timeframe,
                start_time=rows[0].start_time,
                end_time=rows[-1].end_time,
                open_p=rows[0].open_p,
                high_p=max(row.high_p for row in rows),
                low_p=min(row.low_p for row in rows),
                close_p=rows[-1].close_p,
                volume=sum(row.volume for row in rows),
                ticks_count=sum(row.ticks_count for row in rows),
                is_closed=True,
                sequence_id=sequence_id,
                correlation_id=f"DERIVED_{timeframe}_{sequence_id}",
            )
        )
    return aggregated


def _copy_mt5_rates(mt5: Any, symbol: str, mt5_timeframe: int, timeframe: str, started: datetime, ended: datetime) -> list[CandleNode]:
    rates = mt5.copy_rates_range(symbol, mt5_timeframe, started, ended)
    if rates is None:
        raise RuntimeError(f"MT5 copy_rates_range failed for {timeframe}: {mt5.last_error()}")
    minutes = TIMEFRAME_MINUTES[timeframe]
    candles: list[CandleNode] = []
    for index, row in enumerate(rates):
        start = datetime.fromtimestamp(int(row["time"]), tz=timezone.utc)
        candles.append(
            CandleNode(
                symbol=symbol,
                timeframe=timeframe,
                start_time=start,
                end_time=start + timedelta(minutes=minutes),
                open_p=float(row["open"]),
                high_p=float(row["high"]),
                low_p=float(row["low"]),
                close_p=float(row["close"]),
                volume=int(row["real_volume"] or row["tick_volume"] or 0),
                ticks_count=int(row["tick_volume"] or 0),
                is_closed=True,
                sequence_id=index,
                correlation_id=f"MT5_{timeframe}_{index}",
            )
        )
    return candles


def save_outputs(result: Mapping[str, Any], output_dir: Path, label: str) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stem = f"ict_smc_backtest_{label}_{timestamp}"
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    trades_path = output_dir / f"{stem}_trades.csv"
    json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_markdown_report(result), encoding="utf-8")
    _write_trade_csv(trades_path, result.get("trade_log", []))
    return {"json": json_path, "markdown": md_path, "trades_csv": trades_path}


def _markdown_report(result: Mapping[str, Any]) -> str:
    metrics = result.get("performance_metrics", {})
    diagnostics = result.get("diagnostics", {})
    report = result.get("report", {})
    data_summary = report.get("data_summary", {})
    lines = [
        "# Apex ICT/SMC Full-System Backtest",
        "",
        "## Run Configuration",
        f"- Profile: {data_summary.get('profile_name')}",
        f"- Git branch: {data_summary.get('git', {}).get('branch')}",
        f"- Git commit: {data_summary.get('git', {}).get('commit')}",
        f"- Source: {data_summary.get('source')}",
        f"- Symbol: {data_summary.get('symbol')}",
        f"- Date range: {data_summary.get('from')} to {data_summary.get('to')}",
        f"- Spread/slippage: {data_summary.get('spread_price')} / {data_summary.get('slippage_price')}",
        f"- Timeframe counts: {data_summary.get('timeframe_counts')}",
        f"- Selector config: {data_summary.get('selector_config')}",
        f"- Management config: {data_summary.get('active_profile', {}).get('management')}",
        "",
        "## Performance",
        f"- Total trades: {metrics.get('total_trades', 0)}",
        f"- Wins: {metrics.get('wins', 0)}",
        f"- Losses: {metrics.get('losses', 0)}",
        f"- Breakevens: {metrics.get('breakevens', 0)}",
        f"- Win rate: {_pct(metrics.get('win_rate', 0.0))}",
        f"- Net R: {metrics.get('net_R', 0.0)}",
        f"- Expectancy R: {metrics.get('expectancy_R', 0.0)}",
        f"- Profit factor: {metrics.get('profit_factor')}",
        f"- Max drawdown R: {metrics.get('max_drawdown_R', 0.0)}",
        f"- Target 1 hit rate: {_pct(metrics.get('target_1_hit_rate', 0.0))}",
        f"- Final target hit rate: {_pct(metrics.get('final_target_hit_rate', 0.0))}",
        "",
        "## Strategy Diagnostics",
        f"- Strategy evaluations: {diagnostics.get('strategy_evaluations', 0)}",
        f"- Selected signals: {diagnostics.get('selected_signals', 0)}",
        f"- Tradeable signals observed: {diagnostics.get('tradeable_signals_observed', 0)}",
        f"- Rejected cycles: {diagnostics.get('rejected_cycles', 0)}",
        f"- Cooldown skips: {diagnostics.get('cooldown_skips', 0)}",
        f"- Open-position skips: {diagnostics.get('open_position_skips', 0)}",
        f"- Entry-drift skips: {diagnostics.get('entry_drift_skips', 0)}",
        f"- Stop hardenings: {diagnostics.get('stop_hardened', 0)}",
        f"- Executed strategy counts: {diagnostics.get('strategy_counts', {})}",
        f"- Tradeable strategy counts: {diagnostics.get('tradeable_strategy_counts', {})}",
        f"- Blocked-by-open-position counts: {diagnostics.get('blocked_by_open_position_strategy_counts', {})}",
        f"- Evaluation status counts: {diagnostics.get('evaluation_status_counts', {})}",
        f"- Top rejection reasons: {diagnostics.get('top_rejection_reasons', {})}",
        "",
        "## Mark-To-Market Positions",
        f"- Open-at-end trades excluded from main metrics: {result.get('mark_to_market_metrics', {}).get('total_trades', 0)}",
        f"- Mark-to-market net R: {result.get('mark_to_market_metrics', {}).get('net_R', 0.0)}",
        "",
        "## Reliability Warnings",
        *(f"- {warning}" for warning in report.get("warnings", [])),
    ]
    return "\n".join(lines) + "\n"


def _write_trade_csv(path: Path, trades: Sequence[Mapping[str, Any]]) -> None:
    fields = [
        "trade_id",
        "strategy",
        "symbol",
        "direction",
        "session_name",
        "killzone_name",
        "killzone_active",
        "entry_time",
        "exit_time",
        "duration_min",
        "entry_price",
        "intended_entry_price",
        "fill_price",
        "exit_price",
        "stop_loss",
        "initial_risk",
        "target_1",
        "target_2",
        "final_target",
        "estimated_rr",
        "post_cost_rr",
        "confidence_score",
        "realized_R",
        "result",
        "final_exit_reason",
        "ambiguous_exit",
        "components",
        "rejection_reasons",
        "spread_price",
        "slippage_price",
        "entry_drift",
        "drift_risk_fraction",
        "displacement_diagnostics",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for trade in trades:
            writer.writerow({field: trade.get(field, "") for field in fields})


def _setup_to_market_signal(
    setup: Any,
    selected: StrategyEvaluation,
    timestamp: datetime,
    symbol: str,
    config: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    targets = _target_ladder(setup, selected, config)
    final_target = targets[-1]["price"] if targets else setup.take_profit
    session_context = dict(context.get("session_context", {}) or {})
    order_type = _intended_order_type(selected.signal)
    setup_context = {
        "strategy": selected.definition.key,
        "setup_type": setup.setup_type.value,
        "estimated_rr": setup.estimated_rr,
        "confidence_score": setup.confidence_score,
        "session_name": session_context.get("session_name") or session_context.get("session"),
        "killzone_name": session_context.get("killzone_name"),
        "killzone_active": session_context.get("killzone_active"),
        "components": selected.signal.get("components") or selected.signal.get("components_detected"),
        "rejection_reasons": selected.signal.get("rejection_reasons", []),
        "displacement_diagnostics": _extract_displacement_diagnostics(selected.signal),
    }
    return {
        "order_id": setup.id,
        "setup_id": setup.id,
        "symbol": symbol,
        "strategy": selected.definition.key,
        "setup_type": setup.setup_type.value,
        "direction": setup.direction.value,
        "order_type": order_type,
        "entry_type": selected.signal.get("entry_type") or selected.signal.get("entry_mode"),
        "signal_time": timestamp.isoformat(),
        "valid_from_time": timestamp.isoformat(),
        "expiry_time": setup.expiration_time.isoformat(),
        "entry_price": setup.entry_price,
        "intended_entry_price": setup.entry_price,
        "stop_loss": setup.stop_loss,
        "targets": targets,
        "target_1": _named_target(targets, "target_1"),
        "target_2": _named_target(targets, "target_2"),
        "final_target": final_target,
        "estimated_rr": setup.estimated_rr,
        "confidence_score": setup.confidence_score,
        "session_name": setup_context["session_name"],
        "killzone_name": setup_context["killzone_name"],
        "killzone_active": setup_context["killzone_active"],
        "spread_price": config.get("spread_price"),
        "slippage_price": config.get("slippage_price"),
        "move_stop_to_be_after_target_1": True,
        "trade_allowed": True,
        "components_detected": {"strategy": selected.definition.key, "raw": setup_context["components"]},
        "setup_context": setup_context,
    }


def _signal_stub(selected: StrategyEvaluation, timestamp: datetime) -> dict[str, Any]:
    return {
        "signal_id": f"SKIP_{selected.definition.key}_{int(timestamp.timestamp())}",
        "symbol": "GOLD.i#",
        "signal_time": timestamp.isoformat(),
        "setup_type": selected.definition.setup_type.value,
        "direction": selected.direction.value if selected.direction else "none",
    }


def _position_from_fill(order: Mapping[str, Any], fill: Mapping[str, Any]) -> dict[str, Any]:
    intended = _optional_float(order.get("intended_entry_price") or order.get("entry_price"))
    fill_price = _optional_float(fill.get("fill_price"))
    stop = _optional_float(order.get("stop_loss"))
    drift = abs((fill_price or 0.0) - (intended or 0.0)) if fill_price is not None and intended is not None else None
    risk = abs((intended or 0.0) - (stop or 0.0)) if intended is not None and stop is not None else None
    return {
        "trade_id": order.get("order_id"),
        "symbol": order.get("symbol", "GOLD.i#"),
        "strategy": order.get("strategy"),
        "direction": order.get("direction"),
        "entry_price": fill["fill_price"],
        "intended_entry_price": intended,
        "fill_price": fill["fill_price"],
        "stop_loss": order["stop_loss"],
        "current_stop": order["stop_loss"],
        "targets": _targets_from_order(order),
        "remaining_percent": 1.0,
        "realized_R": 0.0,
        "partials": [],
        "move_stop_to_be_after_target_1": bool(order.get("move_stop_to_be_after_target_1", True)),
        "entry_time": fill.get("fill_time"),
        "signal_time": order.get("signal_time"),
        "order_type": order.get("order_type"),
        "entry_type": order.get("entry_type"),
        "session_name": order.get("session_name"),
        "killzone_name": order.get("killzone_name"),
        "killzone_active": order.get("killzone_active"),
        "estimated_rr": order.get("estimated_rr"),
        "confidence_score": order.get("confidence_score"),
        "spread_price": order.get("spread_price"),
        "slippage_price": order.get("slippage_price"),
        "entry_drift": round(drift, 5) if drift is not None else None,
        "drift_risk_fraction": round(drift / risk, 5) if drift is not None and risk and risk > 0 else None,
        "components": order.get("components_detected"),
        "rejection_reasons": order.get("rejection_reasons", []),
        "setup_context": order.get("setup_context", {}),
        "displacement_diagnostics": (order.get("setup_context") or {}).get("displacement_diagnostics"),
    }


def _targets_from_order(order: Mapping[str, Any]) -> list[dict[str, Any]]:
    if order.get("targets"):
        return [dict(target) for target in order.get("targets", [])]
    targets: list[dict[str, Any]] = []
    for name, close_percent in (("target_1", 0.33), ("target_2", 0.33), ("final_target", 0.34)):
        if order.get(name) is not None:
            targets.append({"name": name, "price": float(order[name]), "close_percent": close_percent})
    return targets


def _target_ladder(setup: Any, selected: StrategyEvaluation, config: Mapping[str, Any]) -> list[dict[str, Any]]:
    ladder = dict(config.get("target_ladder", {}) or {})
    milestones = ladder.get("milestones") or [1, 2, 3]
    close_percents = ladder.get("close_percents") or []
    entry = float(setup.entry_price)
    stop = float(setup.stop_loss)
    risk = abs(entry - stop)
    if risk <= 0:
        return []

    direction = setup.direction.value if hasattr(setup.direction, "value") else str(setup.direction)
    is_buy = str(direction).upper() == OrderDirection.BUY.value
    final_rr = _optional_float(ladder.get("final_rr"))
    mode = str(ladder.get("mode", "strategy")).lower()
    if final_rr is None and mode != "fixed_rr":
        final_rr = float(selected.estimated_rr or setup.estimated_rr or 0.0)
    if final_rr is None or final_rr <= 0:
        final_rr = 3.0

    cleaned_milestones = [float(item) for item in milestones if _optional_float(item) is not None and float(item) > 0]
    if not cleaned_milestones:
        cleaned_milestones = [1.0, 2.0, final_rr]
    if max(cleaned_milestones) < final_rr:
        cleaned_milestones.append(final_rr)

    targets: list[dict[str, Any]] = []
    for index, rr_value in enumerate(cleaned_milestones):
        price = entry + risk * rr_value if is_buy else entry - risk * rr_value
        close_percent = (
            float(close_percents[index])
            if index < len(close_percents) and _optional_float(close_percents[index]) is not None
            else round(1.0 / len(cleaned_milestones), 5)
        )
        name = f"target_{index + 1}"
        if index == len(cleaned_milestones) - 1:
            name = "final_target"
        targets.append({"name": name, "price": round(price, 5), "close_percent": close_percent, "rr": rr_value})
    return _normalize_close_percents(targets)


def _normalize_close_percents(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(float(target.get("close_percent", 0.0)) for target in targets)
    if total <= 0:
        return targets
    normalized = []
    for target in targets:
        data = dict(target)
        data["close_percent"] = round(float(data.get("close_percent", 0.0)) / total, 5)
        normalized.append(data)
    return normalized


def _named_target(targets: Sequence[Mapping[str, Any]], name: str) -> float | None:
    for target in targets:
        if target.get("name") == name:
            return _optional_float(target.get("price"))
    return None


def _intended_order_type(signal: Mapping[str, Any]) -> str:
    raw = " ".join(
        str(value or "")
        for value in (
            signal.get("order_type"),
            signal.get("entry_type"),
            signal.get("entry_mode"),
            signal.get("entry", {}).get("mode") if isinstance(signal.get("entry"), Mapping) else "",
            signal.get("entry_model", {}).get("mode") if isinstance(signal.get("entry_model"), Mapping) else "",
        )
    ).lower()
    if any(token in raw for token in ("market", "close", "confirmation")):
        return BacktestOrderType.MARKET.value
    return BacktestOrderType.LIMIT.value


def _post_cost_rr(order: Mapping[str, Any], fill: Mapping[str, Any]) -> float:
    fill_price = _optional_float(fill.get("fill_price"))
    stop = _optional_float(order.get("stop_loss"))
    final_target = _optional_float(order.get("final_target"))
    if final_target is None and order.get("targets"):
        final_target = _optional_float(list(order.get("targets", []))[-1].get("price"))
    if fill_price is None or stop is None or final_target is None:
        return 0.0
    risk = abs(fill_price - stop)
    if risk <= 0:
        return 0.0
    direction = str(order.get("direction", "")).upper()
    reward = final_target - fill_price if direction == OrderDirection.BUY.value else fill_price - final_target
    return round(max(0.0, reward / risk), 5)


def _extract_displacement_diagnostics(signal: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("displacement_diagnostics", "displacement"):
        value = signal.get(key)
        if isinstance(value, Mapping):
            return value
    score = signal.get("score")
    if isinstance(score, Mapping) and isinstance(score.get("displacement_diagnostics"), Mapping):
        return score["displacement_diagnostics"]
    return {}


def _record_strategy_evaluations(
    evaluations: Sequence[StrategyEvaluation],
    tradeable_counter: Counter[str],
    status_counter: Counter[str],
    rejection_counter: Counter[str],
) -> None:
    for item in evaluations:
        if item.is_tradeable:
            tradeable_counter[item.definition.key] += 1
        status_counter[f"{item.definition.key}:{item.status}"] += 1
        if not item.is_tradeable and item.reason:
            rejection_counter[f"{item.definition.key}:{item.reason}"] += 1


def _entry_drift_rejection_reason(
    order: Mapping[str, Any],
    fill: Mapping[str, Any],
    *,
    max_price: float | None,
    max_risk_fraction: float,
) -> str | None:
    if max_price is None and max_risk_fraction <= 0:
        return None
    signal_entry = _optional_float(order.get("entry_price"))
    stop = _optional_float(order.get("stop_loss"))
    fill_price = _optional_float(fill.get("fill_price"))
    if signal_entry is None or stop is None or fill_price is None:
        return "entry_drift_unmeasurable"
    drift = abs(fill_price - signal_entry)
    risk = abs(signal_entry - stop)
    price_failed = max_price is not None and drift > max_price
    risk_failed = risk > 0 and max_risk_fraction > 0 and (drift / risk) > max_risk_fraction
    if price_failed or risk_failed:
        return f"entry_drift_exceeded:{round(drift, 5)}"
    return None


def _split_completed_and_mark_to_market(
    trade_log: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    completed: list[dict[str, Any]] = []
    mark_to_market: list[dict[str, Any]] = []
    for trade in trade_log:
        data = dict(trade)
        if data.get("final_exit_reason") == BacktestExitReason.END_OF_TEST.value:
            mark_to_market.append(data)
        else:
            completed.append(data)
    return completed, mark_to_market


def _close_position_at_end(position: Mapping[str, Any], final_candle: Mapping[str, Any]) -> dict[str, Any]:
    close = float(final_candle.get("close", position.get("entry_price", 0.0)))
    entry = float(position.get("entry_price", close))
    stop = float(position.get("stop_loss", entry))
    direction = str(position.get("direction", "")).upper()
    risk = abs(entry - stop)
    if risk <= 0:
        realized_r = float(position.get("realized_R", 0.0))
    elif direction == OrderDirection.BUY.value:
        realized_r = float(position.get("realized_R", 0.0)) + float(position.get("remaining_percent", 1.0)) * ((close - entry) / risk)
    else:
        realized_r = float(position.get("realized_R", 0.0)) + float(position.get("remaining_percent", 1.0)) * ((entry - close) / risk)
    return {
        "trade_id": position.get("trade_id"),
        "symbol": position.get("symbol"),
        "direction": position.get("direction"),
        "entry_price": entry,
        "stop_loss": stop,
        "exit_price": close,
        "exit_time": final_candle.get("close_time"),
        "final_exit_reason": BacktestExitReason.END_OF_TEST.value,
        "realized_R": round(realized_r, 5),
        "partials": position.get("partials", []),
        "ambiguous_exit": False,
    }


def _candle_to_backtest_row(candle: CandleNode) -> dict[str, Any]:
    return {
        "timestamp": candle.start_time,
        "close_time": candle.end_time,
        "open": candle.open_p,
        "high": candle.high_p,
        "low": candle.low_p,
        "close": candle.close_p,
        "volume": candle.volume,
        "is_closed": candle.is_closed,
    }


def _synthetic_tick(symbol: str, candle: CandleNode, spread_price: float, sequence_id: int) -> TickNode:
    half = spread_price / 2.0
    return TickNode(
        symbol=symbol,
        timestamp=candle.end_time,
        bid=candle.close_p - half,
        ask=candle.close_p + half,
        volume=candle.ticks_count or candle.volume,
        sequence_id=sequence_id,
        correlation_id=f"BACKTEST_TICK_{sequence_id}",
    )


def _candle_payload(candle: CandleNode, index: int) -> dict[str, Any]:
    return {
        "symbol": candle.symbol,
        "timeframe": candle.timeframe,
        "timestamp": candle.end_time,
        "time": candle.end_time,
        "index": index,
        "position": index,
        "open": candle.open_p,
        "high": candle.high_p,
        "low": candle.low_p,
        "close": candle.close_p,
        "volume": candle.volume,
        "tick_volume": candle.ticks_count,
        "is_closed": candle.is_closed,
    }


def _swing_payload(pivot: Any, index: int) -> dict[str, Any]:
    is_high = "HIGH" in str(getattr(pivot, "point_type", "")).upper()
    return {
        "swing_id": getattr(pivot, "id", f"SWING_{index}"),
        "id": getattr(pivot, "id", f"SWING_{index}"),
        "kind": "high" if is_high else "low",
        "type": "high" if is_high else "low",
        "price": float(getattr(pivot, "price", 0.0)),
        "timestamp": getattr(pivot, "timestamp", None),
        "index": index,
        "position": index,
        "timeframe": getattr(pivot, "timeframe", "1m"),
        "confidence_score": float(getattr(pivot, "confidence", 7.0) or 7.0),
    }


def _swept_pool_payload(pool: Any, depth: float) -> dict[str, Any]:
    side = "buy_side" if pool.is_buy_side else "sell_side"
    reversal_direction = "bearish" if pool.is_buy_side else "bullish"
    return {
        "id": pool.id,
        "liquidity_id": pool.id,
        "swept_liquidity_id": pool.id,
        "timeframe": pool.timeframe,
        "side": side,
        "direction": side,
        "price": pool.ceiling_price if pool.is_buy_side else pool.floor_price,
        "zone_low": pool.floor_price,
        "zone_high": pool.ceiling_price,
        "quality_score": min(10.0, 6.0 + pool.accumulated_touches),
        "target_priority_score": min(10.0, 6.0 + pool.accumulated_touches),
        "touches": pool.accumulated_touches,
        "is_equal_structure": pool.is_equal_structure,
        "swept_status": "swept_rejected",
        "swept": True,
        "sweep_depth": depth,
        "sweep_timestamp": pool.sweep_timestamp,
        "direction_bias": reversal_direction,
        "direction_candidate": reversal_direction,
    }


def _select_target_liquidity(price: float, liquidity_pools: Sequence[Mapping[str, Any]], latest_sweep: Mapping[str, Any] | None) -> dict[str, Any] | None:
    candidates = [pool for pool in liquidity_pools if pool is not latest_sweep]
    if not candidates:
        return None
    return dict(min(candidates, key=lambda pool: abs(float(pool.get("price", price)) - price)))


def _directional_bias(candles_by_tf: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, str]:
    return {timeframe: _bias_for_candles(candles_by_tf.get(timeframe, [])) for timeframe in ("1m", "15m", "1h", "4h")}


def _bias_for_candles(candles: Sequence[Mapping[str, Any]]) -> str:
    if len(candles) < 5:
        return "RANGING"
    recent = candles[-5:]
    first = float(recent[0].get("close", 0.0))
    last = float(recent[-1].get("close", 0.0))
    if last > first:
        return "BULLISH"
    if last < first:
        return "BEARISH"
    return "RANGING"


def _bias_to_strategy_text(value: str | None) -> str:
    text = str(value or "").upper()
    if "BULL" in text:
        return "bullish"
    if "BEAR" in text:
        return "bearish"
    return "neutral"


def _price_location(price: float, candles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not candles:
        return {"premium_discount": "equilibrium", "range_high": price, "range_low": price}
    recent = candles[-50:]
    high = max(float(candle.get("high", price)) for candle in recent)
    low = min(float(candle.get("low", price)) for candle in recent)
    midpoint = (high + low) / 2.0
    return {
        "premium_discount": "premium" if price > midpoint else "discount" if price < midpoint else "equilibrium",
        "range_high": high,
        "range_low": low,
        "range_midpoint": midpoint,
    }


def _parse_cli_datetime(value: str) -> datetime:
    text = value.strip()
    if len(text) == 10:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_row_datetime(row: Mapping[str, str]) -> datetime:
    raw = _row_value(row, "time", "timestamp", "datetime", "date")
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _looks_like_close_time(row: Mapping[str, str]) -> bool:
    keys = {key.lower() for key in row}
    return "close_time" in keys or "end_time" in keys


def _row_value(row: Mapping[str, str], *names: str, default: str | None = None) -> str:
    lower = {key.lower(): value for key, value in row.items()}
    for name in names:
        if name.lower() in lower and lower[name.lower()] not in {None, ""}:
            return lower[name.lower()]
    if default is not None:
        return default
    raise KeyError(f"Missing required CSV column. Tried: {', '.join(names)}")


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "0.00%"


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def load_backtest_profile(profile_name: str, selector_config_path: str | None = None) -> dict[str, Any]:
    path = Path(selector_config_path) if selector_config_path else DEFAULT_PROFILE_PATH
    profiles = json.loads(path.read_text(encoding="utf-8"))
    if profile_name not in profiles:
        available = ", ".join(sorted(profiles))
        raise ValueError(f"Unknown backtest profile {profile_name!r}. Available profiles: {available}")
    profile = dict(profiles[profile_name])
    profile["profile_name"] = profile_name
    profile["profile_path"] = str(path)
    return profile


def _apply_cli_profile_overrides(profile: Mapping[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    resolved = json.loads(json.dumps(profile))
    if args.minimum_rr is not None:
        resolved["minimum_rr"] = args.minimum_rr
    if args.max_hold_minutes is not None:
        management = dict(resolved.get("management", {}) or {})
        management["max_hold_minutes"] = args.max_hold_minutes
        resolved["management"] = management
    if args.disable_session:
        filters = dict(resolved.get("session_filters", {}) or {})
        disabled = list(filters.get("disabled_sessions", []) or [])
        disabled.extend(args.disable_session)
        filters["disabled_sessions"] = sorted(set(disabled))
        resolved["session_filters"] = filters
    if args.disable_killzone:
        filters = dict(resolved.get("session_filters", {}) or {})
        disabled = list(filters.get("disabled_killzones", []) or [])
        disabled.extend(args.disable_killzone)
        filters["disabled_killzones"] = sorted(set(disabled))
        resolved["session_filters"] = filters
    if args.target_final_rr is not None:
        ladder = dict(resolved.get("target_ladder", {}) or {})
        ladder["mode"] = "fixed_rr"
        ladder["final_rr"] = args.target_final_rr
        if args.target_final_rr >= 6:
            ladder["milestones"] = [1, 2, 3, 4, 5, 6]
            ladder["close_percents"] = [0.16, 0.16, 0.17, 0.17, 0.17, 0.17]
        resolved["target_ladder"] = ladder
    resolved["spread_price"] = args.spread_price if args.spread_price is not None else float(resolved.get("spread_price", 0.30))
    resolved["slippage_price"] = args.slippage_price if args.slippage_price is not None else float(resolved.get("slippage_price", 0.05))
    return resolved


def _git_metadata() -> dict[str, str | None]:
    return {
        "commit": _git_value("rev-parse", "HEAD"),
        "branch": _git_value("rev-parse", "--abbrev-ref", "HEAD"),
    }


def _git_value(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        try:
            completed = subprocess.run(
                [r"C:\Program Files\Git\cmd\git.exe", *args],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest the current ICT/SMC selector before VPS deployment.")
    parser.add_argument("--source", choices=("mt5", "csv"), default="mt5")
    parser.add_argument("--symbol", default="GOLD.i#")
    parser.add_argument("--from", dest="from_date", required=True, help="UTC start date/time, e.g. 2026-06-01")
    parser.add_argument("--to", dest="to_date", required=True, help="UTC end date/time, e.g. 2026-06-14")
    parser.add_argument("--csv-1m")
    parser.add_argument("--csv-15m")
    parser.add_argument("--csv-1h")
    parser.add_argument("--csv-4h")
    parser.add_argument("--spread-price", type=float)
    parser.add_argument("--slippage-price", type=float)
    parser.add_argument("--profile", default="strict_intraday_xauusd")
    parser.add_argument("--selector-config", help="Path to a JSON file containing named backtest profiles.")
    parser.add_argument("--minimum-rr", type=float, help="Override profile final minimum RR after costs.")
    parser.add_argument("--max-hold-minutes", type=int, help="Override profile max trade hold time.")
    parser.add_argument("--disable-session", action="append", default=[], help="Disable a broad session name for this run.")
    parser.add_argument("--disable-killzone", action="append", default=[], help="Disable an exact killzone name for this run.")
    parser.add_argument("--target-final-rr", type=float, help="Override target ladder final RR, e.g. 3 or 6.")
    parser.add_argument("--warmup-bars", type=int, default=80)
    parser.add_argument("--strategy-cooldown-minutes", type=int, default=15)
    parser.add_argument("--max-concurrent-positions", type=int, default=1)
    parser.add_argument(
        "--max-entry-drift-price",
        type=float,
        default=1.50,
        help="Reject market fills too far from the strategy entry price. Use -1 to disable.",
    )
    parser.add_argument(
        "--max-entry-drift-risk-fraction",
        type=float,
        default=0.35,
        help="Reject fills whose entry drift exceeds this fraction of original setup risk. Use 0 to disable.",
    )
    parser.add_argument("--no-stop-hardening", action="store_true")
    parser.add_argument(
        "--verbose-logs",
        action="store_true",
        help="Print per-candle selector logs. Default keeps the console quiet and only prints the final report paths.",
    )
    parser.add_argument("--output-dir", default=str(ROOT / "backtest_outputs"))
    return parser


def _configure_backtest_logging(*, verbose: bool) -> None:
    level = logging.INFO if verbose else logging.ERROR
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.KeyValueRenderer(key_order=["event", "strategy", "score", "rr", "error"]),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=False,
    )
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_backtest_logging(verbose=args.verbose_logs)
    profile = _apply_cli_profile_overrides(load_backtest_profile(args.profile, args.selector_config), args)
    inputs = load_inputs(args)
    runner = FullSystemICTSMCBacktester(
        symbol=inputs.symbol,
        spread_price=float(profile["spread_price"]),
        slippage_price=float(profile["slippage_price"]),
        warmup_bars=args.warmup_bars,
        max_concurrent_positions=args.max_concurrent_positions,
        strategy_cooldown_minutes=args.strategy_cooldown_minutes,
        harden_stops=not args.no_stop_hardening,
        max_entry_drift_price=None if args.max_entry_drift_price < 0 else args.max_entry_drift_price,
        max_entry_drift_risk_fraction=args.max_entry_drift_risk_fraction,
    )
    result = runner.run(
        inputs.candles_by_timeframe,
        {
            "source": inputs.source,
            "profile_name": profile.get("profile_name"),
            "active_profile": profile,
            "git": _git_metadata(),
            "command_args": vars(args),
            "selector": {
                "enabled_strategies": profile.get("enabled_strategies", []),
                "disabled_strategies": profile.get("disabled_strategies", []),
                "minimum_score": profile.get("minimum_score"),
                "minimum_rr": profile.get("minimum_rr"),
                "strategy_min_rr": profile.get("strategy_min_rr", {}),
                "strategy_min_scores": profile.get("strategy_min_scores", {}),
                "session_filters": profile.get("session_filters", {}),
                "strict_displacement": profile.get("strict_displacement", False),
                "displacement_thresholds": profile.get("displacement_thresholds", {}),
            },
            "minimum_rr": profile.get("minimum_rr"),
            "target_ladder": profile.get("target_ladder", {}),
            "spread_price": profile.get("spread_price"),
            "slippage_price": profile.get("slippage_price"),
            "news_calendar_loaded": False,
            "minimum_sample_trades": 30,
            "close_open_positions_at_end": True,
            "management": profile.get("management", {"same_candle_policy": "conservative"}),
        },
    )
    result["input_summary"] = {
        "source": inputs.source,
        "symbol": inputs.symbol,
        "from": args.from_date,
        "to": args.to_date,
        "profile": profile.get("profile_name"),
        "spread_price": profile.get("spread_price"),
        "slippage_price": profile.get("slippage_price"),
        "timeframes": {key: len(value) for key, value in inputs.candles_by_timeframe.items()},
    }
    paths = save_outputs(result, Path(args.output_dir), inputs.source)
    metrics = result["performance_metrics"]
    print("Apex ICT/SMC full-system backtest complete")
    print(f"source={inputs.source}")
    print(f"symbol={inputs.symbol}")
    print(f"trades={metrics['total_trades']} wins={metrics['wins']} losses={metrics['losses']} win_rate={_pct(metrics['win_rate'])}")
    print(f"net_R={metrics['net_R']} expectancy_R={metrics['expectancy_R']} max_drawdown_R={metrics['max_drawdown_R']}")
    print(f"selected_signals={result['diagnostics']['selected_signals']} stop_hardened={result['diagnostics']['stop_hardened']}")
    print(f"report={paths['markdown']}")
    print(f"json={paths['json']}")
    print(f"trades_csv={paths['trades_csv']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
