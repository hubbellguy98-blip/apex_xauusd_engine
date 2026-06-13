"""Conservative ICT/SMC backtesting primitives.

The functions in this module replay closed OHLCV candles and evaluate prepared
SMC setup/entry candidates without lookahead bias. They are intentionally
strict: same-candle stop/target ambiguity is handled conservatively and orders
cannot fill before they are active.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from statistics import mean, median
from typing import Any, Mapping, Sequence

from src.analytics.ict_smc.multitimeframe_engine import (
    align_timeframes,
    prepare_timeframe_data,
)


class BacktestDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"


class BacktestOrderType(str, Enum):
    LIMIT = "limit_order"
    MARKET = "market_order"


class BacktestExitReason(str, Enum):
    STOP_LOSS = "stop_loss"
    TARGET_1 = "target_1"
    TARGET_2 = "target_2"
    FINAL_TARGET = "final_target"
    BREAKEVEN_STOP = "breakeven_stop"
    END_OF_TEST = "end_of_test"
    AMBIGUOUS_STOP_FIRST = "ambiguous_stop_first"


class BacktestTradeResult(str, Enum):
    WIN = "win"
    LOSS = "loss"
    BREAKEVEN = "breakeven"
    OPEN = "open"


@dataclass(frozen=True, slots=True)
class _Order:
    order_id: str
    symbol: str
    direction: BacktestDirection
    order_type: BacktestOrderType
    signal_time: datetime
    valid_from_time: datetime
    entry_price: float
    stop_loss: float
    targets: tuple[dict[str, Any], ...]
    setup_context: Mapping[str, Any]
    expiry_time: datetime | None


def clean_ohlcv_data(
    rows: Sequence[Mapping[str, Any] | Any] | Any,
    timeframe: str = "5M",
) -> list[dict[str, Any]]:
    """Normalize, sort, deduplicate, and retain only valid OHLCV rows."""

    cleaned: list[dict[str, Any]] = []
    for row in prepare_timeframe_data(rows, timeframe):
        if (
            _float(row.get("high")) is None
            or _float(row.get("low")) is None
            or _float(row.get("open")) is None
            or _float(row.get("close")) is None
        ):
            continue
        if row["high"] < row["low"]:
            continue
        cleaned.append(row)
    return cleaned


def apply_spread_slippage(
    price: float,
    direction: str | BacktestDirection,
    *,
    spread: float = 0.0,
    slippage: float = 0.0,
    action: str = "entry",
) -> float:
    """Adjust a mid-price to conservative executable bid/ask-style pricing."""

    side = _direction(direction)
    spread_half = max(0.0, float(spread)) / 2.0
    slip = max(0.0, float(slippage))
    if side is BacktestDirection.BULLISH:
        return round(price + spread_half + slip, 10) if action == "entry" else round(price - spread_half, 10)
    if side is BacktestDirection.BEARISH:
        return round(price - spread_half - slip, 10) if action == "entry" else round(price + spread_half, 10)
    return price


def place_pending_order(signal: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Create a pending order record from a confirmed setup signal."""

    cfg = dict(config or {})
    order = _normalize_order(signal, cfg)
    return _order_output(order)


def simulate_order_fill(
    order: Mapping[str, Any],
    candle: Mapping[str, Any],
    spread_model: Mapping[str, Any] | None = None,
    slippage_model: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Simulate market or limit fill using a single closed candle."""

    parsed = _normalize_order(order, {})
    row = _mapping(candle)
    candle_open = _parse_time(row.get("timestamp"))
    candle_close = _parse_time(row.get("close_time") or row.get("timestamp"))
    if not bool(row.get("is_closed", True)):
        return _fill_result(False, parsed, None, None, "candle_not_closed")
    if candle_open is None or candle_close is None:
        return _fill_result(False, parsed, None, None, "invalid_candle_time")
    if parsed.expiry_time and candle_open >= parsed.expiry_time:
        return _fill_result(False, parsed, None, None, "order_expired")
    if candle_close < parsed.valid_from_time:
        return _fill_result(False, parsed, None, None, "order_not_active")
    if candle_open < parsed.valid_from_time and candle_close <= parsed.valid_from_time:
        return _fill_result(False, parsed, None, None, "no_retroactive_fill")

    spread = _cost_value(spread_model, row, "spread")
    slippage = _cost_value(slippage_model, row, "slippage")
    if parsed.order_type is BacktestOrderType.MARKET:
        raw_open = _float(row.get("open"))
        if raw_open is None:
            return _fill_result(False, parsed, None, None, "missing_open_price")
        fill = apply_spread_slippage(
            raw_open,
            parsed.direction,
            spread=spread,
            slippage=slippage,
            action="entry",
        )
        return _fill_result(True, parsed, fill, candle_open, "market_next_open")

    high = _float(row.get("high"))
    low = _float(row.get("low"))
    if high is None or low is None:
        return _fill_result(False, parsed, None, None, "missing_high_low")
    touched = low <= parsed.entry_price <= high
    if not touched:
        return _fill_result(False, parsed, None, None, "limit_not_touched")
    fill = apply_spread_slippage(
        parsed.entry_price,
        parsed.direction,
        spread=spread,
        slippage=0.0,
        action="entry",
    )
    stop_touched = _stop_touched(parsed.direction, parsed.stop_loss, row)
    ambiguity = stop_touched
    return _fill_result(
        True,
        parsed,
        fill,
        candle_close,
        "limit_touched_after_activation",
        ambiguity,
    )


def simulate_trade_management(
    position: Mapping[str, Any],
    candle: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Update an open position using conservative stop/target ordering."""

    cfg = dict(config or {})
    pos = dict(position)
    row = _mapping(candle)
    direction = _direction(pos.get("direction"))
    entry = _float(pos.get("entry_price"))
    initial_stop = _float(pos.get("stop_loss"))
    current_stop = _float(pos.get("current_stop", pos.get("stop_loss")))
    if direction is BacktestDirection.NONE or entry is None or initial_stop is None or current_stop is None:
        return {"position": pos, "events": [], "closed_trade": None, "status": "invalid_position"}

    targets = _targets_from_position(pos)
    remaining = float(pos.get("remaining_percent", 1.0))
    realized_r = float(pos.get("realized_R", 0.0))
    partials = list(pos.get("partials", []))
    events: list[dict[str, Any]] = []
    risk = _risk_per_unit(direction, entry, initial_stop)
    if risk <= 0:
        return {"position": pos, "events": [], "closed_trade": None, "status": "invalid_risk"}

    stop_hit = _stop_touched(direction, current_stop, row)
    hit_targets = [target for target in targets if not target.get("filled") and _target_touched(direction, target["price"], row)]
    if stop_hit and hit_targets and cfg.get("same_candle_policy", "conservative") == "conservative":
        realized_r += remaining * _r_multiple(direction, entry, initial_stop, current_stop)
        closed = _closed_trade(pos, row, BacktestExitReason.AMBIGUOUS_STOP_FIRST.value, realized_r, partials, True)
        pos.update({"remaining_percent": 0.0, "realized_R": realized_r, "closed": True})
        return {"position": pos, "events": events, "closed_trade": closed, "status": "closed"}

    if stop_hit:
        reason = BacktestExitReason.BREAKEVEN_STOP.value if abs(current_stop - entry) < 1e-9 else BacktestExitReason.STOP_LOSS.value
        realized_r += remaining * _r_multiple(direction, entry, initial_stop, current_stop)
        closed = _closed_trade(pos, row, reason, realized_r, partials, False)
        pos.update({"remaining_percent": 0.0, "realized_R": realized_r, "closed": True})
        return {"position": pos, "events": events, "closed_trade": closed, "status": "closed"}

    for target in hit_targets:
        if remaining <= 0:
            break
        close_percent = min(remaining, float(target.get("close_percent", remaining)))
        r_value = _r_multiple(direction, entry, initial_stop, target["price"])
        realized_r += close_percent * r_value
        remaining -= close_percent
        event = {
            "target": target["name"],
            "exit_price": target["price"],
            "exit_time": row.get("close_time") or row.get("timestamp"),
            "closed_percent": close_percent,
            "realized_R": round(r_value, 5),
        }
        partials.append(event)
        events.append(event)
        target["filled"] = True
        if target["name"] == "target_1" and bool(pos.get("move_stop_to_be_after_target_1", False)):
            pos["current_stop"] = entry

    pos.update(
        {
            "targets": targets,
            "remaining_percent": max(0.0, remaining),
            "realized_R": round(realized_r, 5),
            "partials": partials,
        }
    )
    if remaining <= 1e-9:
        closed = _closed_trade(pos, row, BacktestExitReason.FINAL_TARGET.value, realized_r, partials, False)
        pos["closed"] = True
        return {"position": pos, "events": events, "closed_trade": closed, "status": "closed"}
    return {"position": pos, "events": events, "closed_trade": None, "status": "open"}


def record_trade(trade: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a completed trade log record."""

    data = dict(trade)
    r = float(data.get("realized_R", 0.0))
    data.setdefault("result", _trade_result(r).value)
    data.setdefault("notes", [])
    return data


def record_skipped_setup(setup: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """Create a structured skipped-setup audit record."""

    return {
        "skipped_id": setup.get("setup_id") or setup.get("signal_id"),
        "symbol": setup.get("symbol", "XAUUSD"),
        "timestamp": setup.get("signal_time"),
        "setup_type": setup.get("setup_type"),
        "direction": _direction(setup.get("direction")).value,
        "reason": reason,
        "trade_allowed": False,
        "components_detected": setup.get("components_detected", {}),
        "filter_failed": [reason],
    }


def calculate_performance_metrics(trade_log: Sequence[Mapping[str, Any] | Any]) -> dict[str, Any]:
    """Calculate R-multiple metrics from completed trade logs."""

    trades = [record_trade(_mapping(trade)) for trade in trade_log]
    values = [float(trade.get("realized_R", 0.0)) for trade in trades]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    breakevens = [value for value in values if abs(value) < 1e-9]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    max_wins = _max_streak(values, positive=True)
    max_losses = _max_streak(values, positive=False)
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    total = len(values)
    target_1_hits = sum(1 for trade in trades if any(p.get("target") == "target_1" for p in trade.get("partials", [])))
    final_hits = sum(1 for trade in trades if trade.get("final_exit_reason") == BacktestExitReason.FINAL_TARGET.value)
    ambiguous = sum(1 for trade in trades if bool(trade.get("ambiguous_exit", False)))
    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(breakevens),
        "win_rate": round(len(wins) / total, 5) if total else 0.0,
        "loss_rate": round(len(losses) / total, 5) if total else 0.0,
        "average_win_R": round(mean(wins), 5) if wins else 0.0,
        "average_loss_R": round(mean(losses), 5) if losses else 0.0,
        "average_trade_R": round(mean(values), 5) if values else 0.0,
        "median_trade_R": round(median(values), 5) if values else 0.0,
        "net_R": round(sum(values), 5),
        "gross_profit_R": round(gross_profit, 5),
        "gross_loss_R": round(gross_loss, 5),
        "profit_factor": round(gross_profit / gross_loss, 5) if gross_loss else None,
        "expectancy_R": round(mean(values), 5) if values else 0.0,
        "max_drawdown_R": round(abs(max_drawdown), 5),
        "max_consecutive_wins": max_wins,
        "max_consecutive_losses": max_losses,
        "target_1_hit_rate": round(target_1_hits / total, 5) if total else 0.0,
        "final_target_hit_rate": round(final_hits / total, 5) if total else 0.0,
        "ambiguous_candle_count": ambiguous,
    }


def generate_backtest_report(
    trade_log: Sequence[Mapping[str, Any] | Any],
    skipped_setup_log: Sequence[Mapping[str, Any] | Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize performance and reliability warnings."""

    cfg = dict(config or {})
    skipped = [_mapping(item) for item in _as_list(skipped_setup_log)]
    metrics = calculate_performance_metrics(trade_log)
    warnings: list[str] = []
    if not cfg.get("news_calendar_loaded", False):
        warnings.append("missing_news_calendar_for_xauusd")
    if metrics["total_trades"] < int(cfg.get("minimum_sample_trades", 30)):
        warnings.append("small_sample_size")
    if metrics["ambiguous_candle_count"]:
        warnings.append("ambiguous_ohlc_exits_used_conservative_stop_first")
    return {
        "function": "generate_backtest_report",
        "strategy_summary": cfg.get("strategy_summary", {}),
        "data_summary": cfg.get("data_summary", {}),
        "performance_metrics": metrics,
        "skipped_setup_count": len(skipped),
        "top_skip_reasons": _top_reasons(skipped),
        "warnings": _dedupe(warnings),
    }


def run_backtest(
    data: Mapping[str, Sequence[Mapping[str, Any] | Any] | Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay prepared setup signals through conservative order and exit rules."""

    cfg = dict(config or {})
    execution_tf = cfg.get("execution_timeframe", "5M")
    candles = clean_ohlcv_data(data.get(execution_tf, data.get("m5", data.get("5M", []))), execution_tf)
    signals = sorted([_mapping(s) for s in _as_list(cfg.get("signals", []))], key=lambda s: _parse_time(s.get("signal_time")) or datetime.max.replace(tzinfo=timezone.utc))
    pending_orders: list[dict[str, Any]] = []
    open_positions: list[dict[str, Any]] = []
    trade_log: list[dict[str, Any]] = []
    skipped_log: list[dict[str, Any]] = []
    signal_index = 0

    for index, candle in enumerate(candles):
        eval_time = _parse_time(candle.get("close_time"))
        if eval_time is None:
            continue

        for position in list(open_positions):
            managed = simulate_trade_management(position, candle, cfg.get("management", {}))
            if managed["closed_trade"]:
                trade_log.append(record_trade(managed["closed_trade"]))
                open_positions.remove(position)
            else:
                position.update(managed["position"])

        for order in list(pending_orders):
            fill = simulate_order_fill(
                order,
                candle,
                cfg.get("spread_model", {}),
                cfg.get("slippage_model", {}),
            )
            if fill["filled"]:
                open_positions.append(_position_from_fill(order, fill))
                pending_orders.remove(order)
            elif fill["reason"] == "order_expired":
                skipped_log.append(record_skipped_setup(order, "pending_order_expired"))
                pending_orders.remove(order)

        while signal_index < len(signals):
            signal = signals[signal_index]
            signal_time = _parse_time(signal.get("signal_time"))
            if signal_time is None or signal_time > eval_time:
                break
            signal_index += 1
            if not bool(signal.get("trade_allowed", True)):
                skipped_log.append(record_skipped_setup(signal, str(signal.get("reason", "trade_not_allowed"))))
                continue
            if bool(signal.get("news_restricted", False)):
                skipped_log.append(record_skipped_setup(signal, "news_restricted"))
                continue
            aligned = align_timeframes(data, eval_time)
            if any(value is None for value in aligned["closed_candle_status"].values() if cfg.get("require_all_timeframes", False)):
                skipped_log.append(record_skipped_setup(signal, "insufficient_mtf_closed_candles"))
                continue
            order = place_pending_order(signal, cfg.get("order_config", {}))
            if order["order_type"] == BacktestOrderType.MARKET.value:
                next_candle = candles[index + 1] if index + 1 < len(candles) else None
                if not next_candle:
                    skipped_log.append(record_skipped_setup(signal, "no_next_candle_for_market_fill"))
                    continue
                fill = simulate_order_fill(order, next_candle, cfg.get("spread_model", {}), cfg.get("slippage_model", {}))
                if fill["filled"]:
                    open_positions.append(_position_from_fill(order, fill))
                else:
                    skipped_log.append(record_skipped_setup(signal, fill["reason"]))
            else:
                pending_orders.append(order)

    if bool(cfg.get("close_open_positions_at_end", True)) and candles:
        final_candle = candles[-1]
        for position in list(open_positions):
            entry = _float(position.get("entry_price")) or 0.0
            close = _float(final_candle.get("close")) or entry
            r = _r_multiple(_direction(position.get("direction")), entry, _float(position.get("stop_loss")) or entry, close)
            closed = _closed_trade(position, final_candle, BacktestExitReason.END_OF_TEST.value, position.get("realized_R", 0.0) + position.get("remaining_percent", 1.0) * r, position.get("partials", []), False)
            trade_log.append(record_trade(closed))
            open_positions.remove(position)

    report = generate_backtest_report(trade_log, skipped_log, cfg.get("report", {}))
    return {
        "function": "run_backtest",
        "trade_log": trade_log,
        "skipped_setup_log": skipped_log,
        "performance_metrics": report["performance_metrics"],
        "report": report,
        "open_positions": open_positions,
        "pending_orders": pending_orders,
    }


def _normalize_order(signal: Mapping[str, Any], config: Mapping[str, Any]) -> _Order:
    data = _mapping(signal)
    direction = _direction(data.get("direction"))
    order_type = _order_type(data.get("order_type", data.get("entry_type", config.get("order_type", "limit_order"))))
    signal_time = _parse_time(data.get("signal_time") or data.get("order_placed_time"))
    valid_from = _parse_time(data.get("valid_from_time") or data.get("order_placed_time") or signal_time)
    if signal_time is None or valid_from is None:
        raise ValueError("Backtest orders require signal_time/order_placed_time.")
    expiry = _parse_time(data.get("expiry_time"))
    targets = tuple(_normalize_targets(data.get("targets") or data.get("target_ladder") or data))
    return _Order(
        order_id=str(data.get("order_id") or data.get("trade_id") or data.get("setup_id") or f"ORDER_{signal_time.isoformat()}"),
        symbol=str(data.get("symbol", config.get("symbol", "XAUUSD"))),
        direction=direction,
        order_type=order_type,
        signal_time=signal_time,
        valid_from_time=valid_from,
        entry_price=float(data.get("entry_price")),
        stop_loss=float(data.get("stop_loss")),
        targets=targets,
        setup_context=data.get("setup_context", data),
        expiry_time=expiry,
    )


def _normalize_targets(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        raw = []
        for name in ("target_1", "target_2", "final_target"):
            price = _float(value.get(name))
            if price is not None:
                raw.append({"name": name, "price": price})
    else:
        raw = _as_list(value)
    targets: list[dict[str, Any]] = []
    default_weights = {"target_1": 0.5, "target_2": 0.25, "final_target": 0.25}
    for index, target in enumerate(raw):
        data = dict(_mapping(target))
        price = _float(data.get("price", data.get("target_price")))
        if price is None:
            continue
        name = str(data.get("name") or data.get("target") or f"target_{index + 1}")
        data.update(
            {
                "name": name,
                "price": price,
                "close_percent": float(data.get("close_percent", default_weights.get(name, 1.0))),
                "filled": bool(data.get("filled", False)),
            }
        )
        targets.append(data)
    return targets


def _position_from_fill(order: Mapping[str, Any], fill: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(order)
    data.update(
        {
            "trade_id": data.get("order_id"),
            "entry_time": fill.get("fill_time"),
            "entry_price": fill.get("fill_price"),
            "remaining_percent": 1.0,
            "realized_R": 0.0,
            "partials": [],
            "current_stop": data.get("stop_loss"),
            "move_stop_to_be_after_target_1": data.get("move_stop_to_be_after_target_1", True),
            "ambiguous_fill": fill.get("ambiguity_flag", False),
        }
    )
    return data


def _order_output(order: _Order) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "symbol": order.symbol,
        "direction": order.direction.value,
        "order_type": order.order_type.value,
        "signal_time": _iso(order.signal_time),
        "valid_from_time": _iso(order.valid_from_time),
        "entry_price": order.entry_price,
        "stop_loss": order.stop_loss,
        "targets": [dict(target) for target in order.targets],
        "setup_context": dict(order.setup_context),
        "expiry_time": _iso(order.expiry_time),
    }


def _fill_result(
    filled: bool,
    order: _Order,
    price: float | None,
    fill_time: datetime | None,
    reason: str,
    ambiguity: bool = False,
) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "filled": filled,
        "fill_price": price,
        "fill_time": _iso(fill_time),
        "fill_reason": reason if filled else None,
        "reason": reason,
        "ambiguity_flag": ambiguity,
    }


def _closed_trade(
    position: Mapping[str, Any],
    candle: Mapping[str, Any],
    reason: str,
    realized_r: float,
    partials: Sequence[Mapping[str, Any]],
    ambiguous: bool,
) -> dict[str, Any]:
    return {
        "trade_id": position.get("trade_id") or position.get("order_id"),
        "symbol": position.get("symbol"),
        "direction": _direction(position.get("direction")).value,
        "setup_type": _mapping(position.get("setup_context", {})).get("setup_type"),
        "entry_type": position.get("entry_type"),
        "order_type": position.get("order_type"),
        "signal_time": position.get("signal_time"),
        "entry_time": position.get("entry_time"),
        "exit_time": candle.get("close_time") or candle.get("timestamp"),
        "entry_price": position.get("entry_price"),
        "stop_loss": position.get("stop_loss"),
        "partials": [dict(item) for item in partials],
        "final_exit_reason": reason,
        "realized_R": round(realized_r, 5),
        "result": _trade_result(realized_r).value,
        "ambiguous_exit": ambiguous,
        "setup_score": _mapping(position.get("setup_context", {})).get("setup_score"),
        "grade": _mapping(position.get("setup_context", {})).get("grade"),
    }


def _targets_from_position(position: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(target) for target in _normalize_targets(position.get("targets", []))]


def _risk_per_unit(direction: BacktestDirection, entry: float, stop: float) -> float:
    if direction is BacktestDirection.BULLISH:
        return entry - stop
    if direction is BacktestDirection.BEARISH:
        return stop - entry
    return 0.0


def _r_multiple(direction: BacktestDirection, entry: float, stop: float, exit_price: float) -> float:
    risk = _risk_per_unit(direction, entry, stop)
    if risk <= 0:
        return 0.0
    if direction is BacktestDirection.BULLISH:
        return round((exit_price - entry) / risk, 5)
    if direction is BacktestDirection.BEARISH:
        return round((entry - exit_price) / risk, 5)
    return 0.0


def _stop_touched(direction: BacktestDirection, stop: float, candle: Mapping[str, Any]) -> bool:
    low = _float(candle.get("low"))
    high = _float(candle.get("high"))
    if low is None or high is None:
        return False
    if direction is BacktestDirection.BULLISH:
        return low <= stop
    if direction is BacktestDirection.BEARISH:
        return high >= stop
    return False


def _target_touched(direction: BacktestDirection, target: float, candle: Mapping[str, Any]) -> bool:
    low = _float(candle.get("low"))
    high = _float(candle.get("high"))
    if low is None or high is None:
        return False
    if direction is BacktestDirection.BULLISH:
        return high >= target
    if direction is BacktestDirection.BEARISH:
        return low <= target
    return False


def _cost_value(model: Mapping[str, Any] | None, candle: Mapping[str, Any], key: str) -> float:
    data = dict(model or {})
    return float(candle.get(key, data.get(key, data.get(f"default_{key}", 0.0))) or 0.0)


def _direction(value: Any) -> BacktestDirection:
    raw = value.value if isinstance(value, BacktestDirection) else str(value or "").strip().lower()
    if raw in {"bullish", "buy", "long"}:
        return BacktestDirection.BULLISH
    if raw in {"bearish", "sell", "short"}:
        return BacktestDirection.BEARISH
    return BacktestDirection.NONE


def _order_type(value: Any) -> BacktestOrderType:
    raw = value.value if isinstance(value, BacktestOrderType) else str(value or "").strip().lower()
    if raw in {"market", "market_order"}:
        return BacktestOrderType.MARKET
    return BacktestOrderType.LIMIT


def _trade_result(realized_r: float) -> BacktestTradeResult:
    if realized_r > 0:
        return BacktestTradeResult.WIN
    if realized_r < 0:
        return BacktestTradeResult.LOSS
    return BacktestTradeResult.BREAKEVEN


def _max_streak(values: Sequence[float], *, positive: bool) -> int:
    best = current = 0
    for value in values:
        hit = value > 0 if positive else value < 0
        current = current + 1 if hit else 0
        best = max(best, current)
    return best


def _top_reasons(skipped: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in skipped:
        reason = str(item.get("reason", "unknown"))
        counts[reason] = counts.get(reason, 0) + 1
    return [
        {"reason": reason, "count": count}
        for reason, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _dedupe(values: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
