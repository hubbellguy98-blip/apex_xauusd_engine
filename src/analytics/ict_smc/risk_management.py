"""Deterministic ICT/SMC risk management gate.

This module sits after setup detection/scoring and before execution. It does
not create signals and it does not send orders. Its only job is to decide
whether an already-detected ICT/SMC setup is safe enough to route, and if so,
what position size keeps the account loss inside the configured limits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from enum import Enum
from typing import Any, Mapping, Sequence


class RiskDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    NONE = "none"


class RiskDecisionStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    INVALID_INPUT = "invalid_input"


@dataclass(frozen=True, slots=True)
class _Signal:
    signal_id: str
    symbol: str
    direction: RiskDirection
    entry: float | None
    stop: float | None
    target: float | None
    score: float
    correlation_group: str
    stop_valid: bool
    raw: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _AccountState:
    balance: float
    equity: float
    daily_realized_pnl: float
    weekly_realized_pnl: float
    current_open_risk: float
    current_spread: float
    average_spread: float
    open_positions: tuple[Mapping[str, Any], ...]
    trading_locked: bool
    news_restricted: bool
    timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class _RiskConfig:
    risk_percent: float
    pip_value: float
    min_rr: float
    min_position_size: float
    max_position_size: float | None
    lot_step: float
    max_daily_loss_percent: float
    max_weekly_loss_percent: float
    max_open_risk_percent: float
    max_correlated_risk_percent: float
    max_spread: float
    abnormal_spread_multiplier: float
    slippage_buffer: float
    min_stop_distance: float
    allow_position_size_cap: bool
    max_positions: int
    max_positions_per_symbol: int


def calculate_position_size(
    account_balance: float | int | str | None,
    risk_percent: float | int | str | None,
    entry: float | int | str | None,
    stop: float | int | str | None,
    pip_value: float | int | str | None,
) -> dict[str, Any]:
    """Calculate position size from account risk and stop distance.

    The size is derived from the actual distance between entry and stop. A
    wider stop must reduce the position size; the stop must not be moved only
    to make a larger position possible.
    """

    balance = _float(account_balance)
    risk = _float(risk_percent)
    entry_price = _float(entry)
    stop_price = _float(stop)
    value_per_price_unit = _float(pip_value)
    required = {
        "account_balance": balance,
        "risk_percent": risk,
        "entry": entry_price,
        "stop": stop_price,
        "pip_value": value_per_price_unit,
    }
    invalid = [name for name, value in required.items() if value is None]
    if invalid:
        return _sizing_result(0.0, 0.0, 0.0, 0.0, f"missing_{'_'.join(invalid)}")
    if balance <= 0:
        return _sizing_result(0.0, 0.0, 0.0, 0.0, "invalid_account_balance")
    if risk <= 0:
        return _sizing_result(0.0, 0.0, 0.0, 0.0, "invalid_risk_percent")
    if value_per_price_unit <= 0:
        return _sizing_result(0.0, 0.0, 0.0, 0.0, "invalid_pip_value")

    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        return _sizing_result(0.0, balance * risk / 100.0, 0.0, 0.0, "invalid_stop_distance")

    max_loss = balance * risk / 100.0
    risk_per_unit = stop_distance * value_per_price_unit
    if risk_per_unit <= 0:
        return _sizing_result(0.0, max_loss, stop_distance, 0.0, "invalid_risk_per_unit")

    position_size = max_loss / risk_per_unit
    return _sizing_result(position_size, max_loss, stop_distance, risk_per_unit, None)


def validate_trade_risk(
    signal: Mapping[str, Any] | Any,
    account_state: Mapping[str, Any] | Any,
    risk_config: Mapping[str, Any] | Any,
) -> dict[str, Any]:
    """Validate a setup against account, portfolio, spread, news, and RR rules."""

    parsed_signal = _signal(signal)
    account = _account_state(account_state)
    config = _risk_config(risk_config)
    warnings = [
        "Risk is evaluated after setup detection and before trade execution.",
        "Position size is calculated from stop distance, never guessed.",
    ]

    if parsed_signal.direction is RiskDirection.NONE:
        return _reject(
            parsed_signal,
            account,
            config,
            "invalid_direction",
            RiskDecisionStatus.INVALID_INPUT,
            warnings,
        )
    if parsed_signal.entry is None or parsed_signal.stop is None or parsed_signal.target is None:
        return _reject(
            parsed_signal,
            account,
            config,
            "missing_entry_stop_or_target",
            RiskDecisionStatus.INVALID_INPUT,
            warnings,
        )
    if not parsed_signal.stop_valid:
        return _reject(parsed_signal, account, config, "stop_marked_invalid", RiskDecisionStatus.REJECTED, warnings)
    if account.balance <= 0 or account.equity <= 0:
        return _reject(parsed_signal, account, config, "invalid_account_state", RiskDecisionStatus.INVALID_INPUT, warnings)
    if config.pip_value <= 0:
        return _reject(parsed_signal, account, config, "invalid_pip_value", RiskDecisionStatus.INVALID_INPUT, warnings)
    if account.trading_locked:
        return _reject(parsed_signal, account, config, "account_protection_locked", RiskDecisionStatus.REJECTED, warnings)
    if account.news_restricted:
        return _reject(parsed_signal, account, config, "news_restricted", RiskDecisionStatus.REJECTED, warnings)

    spread_rejection = _spread_rejection(account, config)
    if spread_rejection is not None:
        return _reject(parsed_signal, account, config, spread_rejection, RiskDecisionStatus.REJECTED, warnings)

    adjusted = _xauusd_adjusted_prices(parsed_signal, account.current_spread, config.slippage_buffer)
    stop_rejection = _stop_rejection(parsed_signal, adjusted, config)
    if stop_rejection is not None:
        return _reject(parsed_signal, account, config, stop_rejection, RiskDecisionStatus.REJECTED, warnings, adjusted=adjusted)

    rr = _reward_to_risk(parsed_signal.direction, adjusted["entry"], adjusted["stop"], adjusted["target"])
    if rr is None:
        return _reject(parsed_signal, account, config, "invalid_reward_to_risk", RiskDecisionStatus.REJECTED, warnings, adjusted=adjusted)
    if rr < config.min_rr:
        return _reject(
            parsed_signal,
            account,
            config,
            "reward_to_risk_below_minimum",
            RiskDecisionStatus.REJECTED,
            warnings,
            rr=rr,
            adjusted=adjusted,
        )

    risk_amount = account.balance * config.risk_percent / 100.0
    limit_rejection = _portfolio_limit_rejection(parsed_signal, account, config, risk_amount)
    if limit_rejection is not None:
        return _reject(
            parsed_signal,
            account,
            config,
            limit_rejection,
            RiskDecisionStatus.REJECTED,
            warnings,
            rr=rr,
            adjusted=adjusted,
        )

    sizing = calculate_position_size(
        account.balance,
        config.risk_percent,
        adjusted["entry"],
        adjusted["stop"],
        config.pip_value,
    )
    if sizing["rejection_reason"] is not None:
        return _reject(
            parsed_signal,
            account,
            config,
            sizing["rejection_reason"],
            RiskDecisionStatus.REJECTED,
            warnings,
            rr=rr,
            adjusted=adjusted,
            sizing=sizing,
        )

    requested_size = sizing["position_size"]
    position_size, size_warning, size_rejection = _normalize_position_size(requested_size, config)
    if size_warning:
        warnings.append(size_warning)
    if size_rejection is not None:
        return _reject(
            parsed_signal,
            account,
            config,
            size_rejection,
            RiskDecisionStatus.REJECTED,
            warnings,
            rr=rr,
            adjusted=adjusted,
            sizing=sizing,
        )

    actual_max_loss = position_size * sizing["stop_distance"] * config.pip_value
    if actual_max_loss > risk_amount + 1e-9:
        return _reject(
            parsed_signal,
            account,
            config,
            "normalized_size_exceeds_risk_limit",
            RiskDecisionStatus.REJECTED,
            warnings,
            rr=rr,
            adjusted=adjusted,
            sizing=sizing,
        )

    return _decision(
        parsed_signal,
        account,
        config,
        RiskDecisionStatus.APPROVED,
        True,
        None,
        position_size,
        actual_max_loss,
        rr,
        warnings,
        adjusted=adjusted,
        sizing=sizing,
    )


def _sizing_result(
    position_size: float,
    max_loss: float,
    stop_distance: float,
    risk_per_unit: float,
    rejection_reason: str | None,
) -> dict[str, Any]:
    return {
        "function": "calculate_position_size",
        "position_size": round(max(0.0, position_size), 6),
        "max_loss": round(max(0.0, max_loss), 6),
        "risk_amount": round(max(0.0, max_loss), 6),
        "stop_distance": round(max(0.0, stop_distance), 6),
        "risk_per_unit": round(max(0.0, risk_per_unit), 6),
        "rejection_reason": rejection_reason,
    }


def _decision(
    signal: _Signal,
    account: _AccountState,
    config: _RiskConfig,
    status: RiskDecisionStatus,
    approved: bool,
    rejection_reason: str | None,
    position_size: float,
    max_loss: float,
    rr: float | None,
    warnings: Sequence[str],
    *,
    adjusted: Mapping[str, float] | None = None,
    sizing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    risk_amount = account.balance * config.risk_percent / 100.0
    daily_loss_limit = account.balance * config.max_daily_loss_percent / 100.0
    weekly_loss_limit = account.balance * config.max_weekly_loss_percent / 100.0
    open_risk_limit = account.balance * config.max_open_risk_percent / 100.0
    correlated_risk = _correlated_open_risk(signal, account)
    correlated_limit = account.balance * config.max_correlated_risk_percent / 100.0
    return {
        "function": "validate_trade_risk",
        "approved": approved,
        "position_size": round(max(0.0, position_size), 6),
        "max_loss": round(max(0.0, max_loss), 6),
        "rr": _round(rr),
        "rejection_reason": rejection_reason,
        "decision": {
            "status": status.value,
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "direction": signal.direction.value,
        },
        "risk_details": {
            "risk_percent": config.risk_percent,
            "configured_risk_amount": _round(risk_amount),
            "requested_position_size": _round((sizing or {}).get("position_size")),
            "stop_distance": _round((sizing or {}).get("stop_distance")),
            "pip_value": config.pip_value,
            "score": signal.score,
        },
        "risk_limits": {
            "daily_loss_limit": _round(daily_loss_limit),
            "weekly_loss_limit": _round(weekly_loss_limit),
            "open_risk_limit": _round(open_risk_limit),
            "correlated_risk_limit": _round(correlated_limit),
            "daily_realized_pnl": _round(account.daily_realized_pnl),
            "weekly_realized_pnl": _round(account.weekly_realized_pnl),
            "current_open_risk": _round(account.current_open_risk),
            "current_correlated_risk": _round(correlated_risk),
        },
        "execution_safety": {
            "current_spread": _round(account.current_spread),
            "average_spread": _round(account.average_spread),
            "max_spread": config.max_spread,
            "slippage_buffer": config.slippage_buffer,
            "adjusted_entry": _round((adjusted or {}).get("entry")),
            "adjusted_stop": _round((adjusted or {}).get("stop")),
            "adjusted_target": _round((adjusted or {}).get("target")),
            "news_restricted": account.news_restricted,
        },
        "correlation_details": {
            "correlation_group": signal.correlation_group,
            "open_positions_in_group": _correlated_position_count(signal, account),
            "current_correlated_risk": _round(correlated_risk),
            "projected_correlated_risk": _round(correlated_risk + risk_amount),
        },
        "warnings": list(dict.fromkeys(warnings)),
    }


def _reject(
    signal: _Signal,
    account: _AccountState,
    config: _RiskConfig,
    reason: str,
    status: RiskDecisionStatus,
    warnings: Sequence[str],
    *,
    rr: float | None = None,
    adjusted: Mapping[str, float] | None = None,
    sizing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _decision(signal, account, config, status, False, reason, 0.0, 0.0, rr, warnings, adjusted=adjusted, sizing=sizing)


def _signal(raw_signal: Mapping[str, Any] | Any) -> _Signal:
    raw = _mapping(raw_signal)
    return _Signal(
        signal_id=str(_get(raw, "signal_id", "setup_id", "id", default="unknown_signal")),
        symbol=str(_get(raw, "symbol", default="XAUUSD")),
        direction=_direction(_get(raw, "direction", "bias", "side", default=None)),
        entry=_float(_get(raw, "entry", "entry_price", "entry_level", default=None)),
        stop=_float(_get(raw, "stop", "stop_loss", "sl", default=None)),
        target=_float(_get(raw, "target", "take_profit", "tp", "final_target", default=None)),
        score=_float(_get(raw, "score", "quality_score", "final_score", default=0.0), 0.0) or 0.0,
        correlation_group=str(_get(raw, "correlation_group", "asset_group", default="xauusd")),
        stop_valid=bool(_get(raw, "stop_valid", "is_stop_valid", default=True)),
        raw=raw,
    )


def _account_state(raw_state: Mapping[str, Any] | Any) -> _AccountState:
    raw = _mapping(raw_state)
    balance = _float(_get(raw, "account_balance", "balance", default=0.0), 0.0) or 0.0
    equity = _float(_get(raw, "equity", default=balance), balance) or balance
    timestamp = _timestamp(_get(raw, "timestamp", "current_time", default=None))
    return _AccountState(
        balance=balance,
        equity=equity,
        daily_realized_pnl=_float(_get(raw, "daily_realized_pnl", "daily_pnl", default=0.0), 0.0) or 0.0,
        weekly_realized_pnl=_float(_get(raw, "weekly_realized_pnl", "weekly_pnl", default=0.0), 0.0) or 0.0,
        current_open_risk=_float(_get(raw, "current_open_risk", "open_risk", default=0.0), 0.0) or 0.0,
        current_spread=max(0.0, _float(_get(raw, "current_spread", "spread", default=0.0), 0.0) or 0.0),
        average_spread=max(0.0, _float(_get(raw, "average_spread", "avg_spread", default=0.0), 0.0) or 0.0),
        open_positions=tuple(_records(_get(raw, "open_positions", default=[]))),
        trading_locked=bool(_get(raw, "trading_locked", "account_locked", "risk_locked", default=False)),
        news_restricted=bool(_get(raw, "news_restricted", "in_news_window", "news_window_active", default=False)),
        timestamp=timestamp,
    )


def _risk_config(raw_config: Mapping[str, Any] | Any) -> _RiskConfig:
    raw = _mapping(raw_config)
    max_position = _float(_get(raw, "max_position_size", "max_lot", "max_lots", default=None))
    return _RiskConfig(
        risk_percent=max(0.0, _float(_get(raw, "risk_percent", "risk_pct", default=0.5), 0.5) or 0.0),
        pip_value=max(0.0, _float(_get(raw, "pip_value", "value_per_price_unit", default=0.0), 0.0) or 0.0),
        min_rr=max(0.0, _float(_get(raw, "min_rr", "minimum_rr", default=1.5), 1.5) or 0.0),
        min_position_size=max(0.0, _float(_get(raw, "min_position_size", "min_lot", default=0.01), 0.01) or 0.0),
        max_position_size=max_position,
        lot_step=max(0.0, _float(_get(raw, "lot_step", "volume_step", default=0.01), 0.01) or 0.0),
        max_daily_loss_percent=max(0.0, _float(_get(raw, "max_daily_loss_percent", default=3.0), 3.0) or 0.0),
        max_weekly_loss_percent=max(0.0, _float(_get(raw, "max_weekly_loss_percent", default=6.0), 6.0) or 0.0),
        max_open_risk_percent=max(0.0, _float(_get(raw, "max_open_risk_percent", default=3.0), 3.0) or 0.0),
        max_correlated_risk_percent=max(0.0, _float(_get(raw, "max_correlated_risk_percent", default=2.0), 2.0) or 0.0),
        max_spread=max(0.0, _float(_get(raw, "max_spread", "max_allowed_spread", default=0.8), 0.8) or 0.0),
        abnormal_spread_multiplier=max(0.0, _float(_get(raw, "abnormal_spread_multiplier", default=3.0), 3.0) or 0.0),
        slippage_buffer=max(0.0, _float(_get(raw, "slippage_buffer", default=0.0), 0.0) or 0.0),
        min_stop_distance=max(0.0, _float(_get(raw, "min_stop_distance", default=0.0), 0.0) or 0.0),
        allow_position_size_cap=bool(_get(raw, "allow_position_size_cap", default=True)),
        max_positions=max(0, int(_float(_get(raw, "max_positions", default=10), 10) or 10)),
        max_positions_per_symbol=max(0, int(_float(_get(raw, "max_positions_per_symbol", default=3), 3) or 3)),
    )


def _spread_rejection(account: _AccountState, config: _RiskConfig) -> str | None:
    if config.max_spread > 0 and account.current_spread > config.max_spread:
        return "spread_too_high"
    if (
        account.average_spread > 0
        and config.abnormal_spread_multiplier > 0
        and account.current_spread > account.average_spread * config.abnormal_spread_multiplier
    ):
        return "spread_abnormally_high"
    return None


def _xauusd_adjusted_prices(signal: _Signal, spread: float, slippage: float) -> dict[str, float]:
    half_spread = max(0.0, spread) / 2.0
    buffer = max(0.0, slippage)
    if signal.direction is RiskDirection.LONG:
        return {
            "entry": signal.entry + half_spread + buffer,
            "stop": signal.stop - half_spread,
            "target": signal.target - half_spread,
        }
    return {
        "entry": signal.entry - half_spread - buffer,
        "stop": signal.stop + half_spread,
        "target": signal.target + half_spread,
    }


def _stop_rejection(signal: _Signal, adjusted: Mapping[str, float], config: _RiskConfig) -> str | None:
    entry = adjusted["entry"]
    stop = adjusted["stop"]
    distance = abs(entry - stop)
    if distance <= 0:
        return "invalid_stop_distance"
    if config.min_stop_distance > 0 and distance < config.min_stop_distance:
        return "stop_too_tight"
    if signal.direction is RiskDirection.LONG and stop >= entry:
        return "invalid_long_stop"
    if signal.direction is RiskDirection.SHORT and stop <= entry:
        return "invalid_short_stop"
    return None


def _reward_to_risk(direction: RiskDirection, entry: float, stop: float, target: float) -> float | None:
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    if direction is RiskDirection.LONG:
        reward = target - entry
    elif direction is RiskDirection.SHORT:
        reward = entry - target
    else:
        return None
    if reward <= 0:
        return 0.0
    return reward / risk


def _portfolio_limit_rejection(
    signal: _Signal,
    account: _AccountState,
    config: _RiskConfig,
    risk_amount: float,
) -> str | None:
    daily_loss = abs(min(0.0, account.daily_realized_pnl))
    weekly_loss = abs(min(0.0, account.weekly_realized_pnl))
    daily_limit = account.balance * config.max_daily_loss_percent / 100.0
    weekly_limit = account.balance * config.max_weekly_loss_percent / 100.0
    open_risk_limit = account.balance * config.max_open_risk_percent / 100.0
    correlated_limit = account.balance * config.max_correlated_risk_percent / 100.0

    if daily_limit > 0 and daily_loss >= daily_limit:
        return "max_daily_loss_reached"
    if weekly_limit > 0 and weekly_loss >= weekly_limit:
        return "max_weekly_loss_reached"
    if daily_limit > 0 and daily_loss + risk_amount > daily_limit:
        return "projected_daily_loss_limit_exceeded"
    if weekly_limit > 0 and weekly_loss + risk_amount > weekly_limit:
        return "projected_weekly_loss_limit_exceeded"
    if open_risk_limit > 0 and account.current_open_risk + risk_amount > open_risk_limit:
        return "max_open_risk_exceeded"
    if correlated_limit > 0 and _correlated_open_risk(signal, account) + risk_amount > correlated_limit:
        return "correlated_exposure_too_high"
    if config.max_positions > 0 and len(account.open_positions) >= config.max_positions:
        return "max_open_positions_reached"
    same_symbol = sum(1 for position in account.open_positions if str(_get(position, "symbol", default="")) == signal.symbol)
    if config.max_positions_per_symbol > 0 and same_symbol >= config.max_positions_per_symbol:
        return "max_symbol_positions_reached"
    return None


def _normalize_position_size(requested_size: float, config: _RiskConfig) -> tuple[float, str | None, str | None]:
    capped = requested_size
    warning = None
    if config.max_position_size is not None and capped > config.max_position_size:
        if not config.allow_position_size_cap:
            return 0.0, None, "position_size_above_configured_max"
        capped = config.max_position_size
        warning = "position_size_capped_to_configured_max"
    normalized = _floor_to_step(capped, config.lot_step)
    if normalized < config.min_position_size:
        return 0.0, warning, "position_size_below_broker_minimum"
    return normalized, warning, None


def _correlated_open_risk(signal: _Signal, account: _AccountState) -> float:
    total = 0.0
    for position in account.open_positions:
        if str(_get(position, "correlation_group", "asset_group", default="")) == signal.correlation_group:
            total += max(0.0, _float(_get(position, "open_risk", "risk_amount", default=0.0), 0.0) or 0.0)
    return total


def _correlated_position_count(signal: _Signal, account: _AccountState) -> int:
    return sum(
        1
        for position in account.open_positions
        if str(_get(position, "correlation_group", "asset_group", default="")) == signal.correlation_group
    )


def _floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        return max(0.0, value)
    decimal_step = Decimal(str(step))
    steps = (Decimal(str(max(0.0, value))) / decimal_step).to_integral_value(rounding=ROUND_FLOOR)
    return float(steps * decimal_step)


def _direction(raw: Any) -> RiskDirection:
    value = str(raw or "").strip().lower()
    if value in {"long", "buy", "bullish", "bull", "buy_side"}:
        return RiskDirection.LONG
    if value in {"short", "sell", "bearish", "bear", "sell_side"}:
        return RiskDirection.SHORT
    return RiskDirection.NONE


def _records(values: Any) -> list[Mapping[str, Any]]:
    if values is None:
        return []
    if isinstance(values, Mapping):
        return [values]
    try:
        return [_mapping(item) for item in values]
    except TypeError:
        return [_mapping(values)]


def _mapping(value: Mapping[str, Any] | Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "__dict__"):
        return vars(value)
    return {}


def _get(mapping: Mapping[str, Any] | Any, *keys: str, default: Any = None) -> Any:
    data = _mapping(mapping)
    for key in keys:
        if key in data:
            return data[key]
    return default


def _float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _round(value: Any, digits: int = 6) -> float | None:
    numeric = _float(value)
    if numeric is None:
        return None
    return round(numeric, digits)
