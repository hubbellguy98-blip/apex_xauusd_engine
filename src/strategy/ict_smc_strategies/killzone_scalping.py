"""Kill Zone Scalping strategy model for ICT/SMC research.

Kill zones are treated as permission windows only. A valid scalp still needs the
full sequence:

kill zone -> relevant liquidity -> sweep/reclaim -> MSS/displacement ->
FVG/OB POI -> retracement -> nearby target -> RR/safety/session checks.

The module is deterministic, closed-candle only, and never places orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


class KillZoneScalpStatus(str, Enum):
    VALID = "valid"
    REJECTED = "rejected"
    WAITING_FOR_RETRACEMENT = "waiting_for_retracement"
    OUTSIDE_KILLZONE = "outside_killzone"


class KillZoneDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class _Candle:
    position: int
    index: int
    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    is_closed: bool = True

    @property
    def range(self) -> float:
        return max(self.high - self.low, 1e-9)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_to_range(self) -> float:
        return self.body / self.range

    @property
    def bullish_close_position(self) -> float:
        return (self.close - self.low) / self.range

    @property
    def bearish_close_position(self) -> float:
        return (self.high - self.close) / self.range


def is_in_killzone(
    timestamp: Any,
    killzone_config: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    broker_timezone: str | timezone | None = "UTC",
) -> dict[str, Any]:
    """Return the active configured kill zone for a timestamp."""

    ts = _as_datetime(timestamp, broker_timezone)
    for window in _windows(killzone_config):
        if not bool(window.get("enabled", True)):
            continue
        window_tz = _tz(window.get("timezone", broker_timezone or "UTC"))
        local_ts = ts.astimezone(window_tz)
        start_dt, end_dt = _window_bounds(local_ts, str(window["start_time"]), str(window["end_time"]), window_tz)
        if start_dt <= local_ts <= end_dt:
            return {
                "in_killzone": True,
                "active_killzone_name": str(window.get("window_name", window.get("name", "Kill Zone"))),
                "active_killzone": dict(window),
                "window_start": start_dt.isoformat(),
                "window_end": end_dt.isoformat(),
                "timestamp_in_window_timezone": local_ts.isoformat(),
                "minutes_from_window_start": int((local_ts - start_dt).total_seconds() // 60),
                "minutes_to_window_end": int((end_dt - local_ts).total_seconds() // 60),
            }
    return {
        "in_killzone": False,
        "active_killzone_name": None,
        "active_killzone": None,
        "window_start": None,
        "window_end": None,
        "timestamp_in_window_timezone": ts.isoformat(),
        "minutes_from_window_start": None,
        "minutes_to_window_end": None,
    }


def detect_killzone_liquidity_sweep(
    df: Any,
    liquidity_pools: Sequence[Mapping[str, Any]],
    active_killzone: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect a kill-zone sweep with reclaim/rejection confirmation."""

    cfg = _config(config)
    candles = _candles(df)
    if active_killzone is None:
        return {"sweep_detected": False, "rejection_reasons": ["outside_killzone"]}
    if not candles:
        return {"sweep_detected": False, "rejection_reasons": ["insufficient_closed_candles"]}

    atr = _atr(candles, int(cfg["atr_period"]))
    sweep_buffer = float(cfg["sweep_buffer"])
    candidates: list[dict[str, Any]] = []
    for candle in candles:
        window_state = is_in_killzone(candle.timestamp, [active_killzone], cfg["broker_timezone"])
        if not window_state["in_killzone"]:
            continue
        for pool in liquidity_pools:
            if _pool_swept(pool):
                continue
            side = _pool_side(pool)
            zone_low, zone_high = _pool_bounds(pool)
            quality = float(_value(pool, "quality_score", 7.0) or 7.0)
            if side == "sell_side" and candle.low < zone_low - sweep_buffer and candle.close > zone_low:
                candidates.append(
                    _sweep_event(
                        candle,
                        pool,
                        "sell_side",
                        KillZoneDirection.BULLISH.value,
                        zone_low,
                        quality,
                        (zone_low - candle.low) / max(atr, 1e-9),
                        window_state,
                    )
                )
            if side == "buy_side" and candle.high > zone_high + sweep_buffer and candle.close < zone_high:
                candidates.append(
                    _sweep_event(
                        candle,
                        pool,
                        "buy_side",
                        KillZoneDirection.BEARISH.value,
                        zone_high,
                        quality,
                        (candle.high - zone_high) / max(atr, 1e-9),
                        window_state,
                    )
                )
    if not candidates:
        return {"sweep_detected": False, "rejection_reasons": ["no_killzone_liquidity_sweep"]}
    return sorted(candidates, key=lambda item: (item["sweep_quality_score"], item["sweep_index"]), reverse=True)[0]


def detect_killzone_mss(
    df: Any,
    sweep: Mapping[str, Any] | None,
    structure_swings: Sequence[Mapping[str, Any]] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect post-sweep market structure shift with displacement."""

    cfg = _config(config)
    candles = _candles(df)
    if not sweep or not sweep.get("sweep_detected"):
        return {"mss_confirmed": False, "rejection_reasons": ["missing_sweep"]}
    if len(candles) < int(cfg["min_total_candles"]):
        return {"mss_confirmed": False, "rejection_reasons": ["insufficient_closed_candles"]}

    direction = _direction(sweep.get("direction"))
    sweep_position = int(sweep["sweep_position"])
    atr = _atr(candles, int(cfg["atr_period"]), sweep_position)
    break_buffer = float(cfg["mss_break_buffer"])
    max_wait = int(cfg["max_mss_wait_candles"])
    prior = candles[max(0, sweep_position - int(cfg["structure_lookback"])) : sweep_position]
    if not prior:
        return {"mss_confirmed": False, "rejection_reasons": ["missing_prior_structure"]}

    bullish_level = _swing_level(structure_swings, "high", prior, max)
    bearish_level = _swing_level(structure_swings, "low", prior, min)
    for candle in candles[sweep_position + 1 : sweep_position + 1 + max_wait]:
        displacement = _displacement(candle, atr, cfg)
        if direction == "bullish" and candle.close > bullish_level + break_buffer and displacement["valid"]:
            return _mss_event(candle, direction, bullish_level, displacement)
        if direction == "bearish" and candle.close < bearish_level - break_buffer and displacement["valid"]:
            return _mss_event(candle, direction, bearish_level, displacement)

    reasons = [f"no_{direction}_mss_after_killzone_sweep"]
    if direction == "unknown":
        reasons = ["unknown_sweep_direction"]
    return {"mss_confirmed": False, "direction": direction, "rejection_reasons": reasons}


def detect_killzone_fvg_or_ob(
    df: Any,
    mss: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect a post-MSS FVG or order block and confirm retracement."""

    cfg = _config(config)
    candles = _candles(df)
    if not mss or not mss.get("mss_confirmed"):
        return {"entry_poi_detected": False, "rejection_reasons": ["missing_mss"]}

    direction = _direction(mss.get("direction"))
    mss_position = int(mss["mss_position"])
    max_wait = int(cfg["max_entry_wait_candles"])
    min_fvg_size = float(cfg["min_fvg_size"])
    max_poi_width = float(cfg["max_poi_width"])
    candidates: list[dict[str, Any]] = []

    for position in range(max(2, mss_position - 1), min(len(candles), mss_position + max_wait + 1)):
        previous_two = candles[position - 2]
        middle = candles[position - 1]
        current = candles[position]
        if direction == "bullish" and previous_two.high < current.low:
            low, high = previous_two.high, current.low
            if high - low >= min_fvg_size and high - low <= max_poi_width:
                candidates.append(_poi_event("fvg", direction, middle, low, high, position))
        if direction == "bearish" and previous_two.low > current.high:
            low, high = current.high, previous_two.low
            if high - low >= min_fvg_size and high - low <= max_poi_width:
                candidates.append(_poi_event("fvg", direction, middle, low, high, position))

    if not candidates:
        ob = _order_block_poi(candles, direction, mss_position, cfg)
        if ob is not None:
            candidates.append(ob)

    if not candidates:
        return {"entry_poi_detected": False, "rejection_reasons": ["no_killzone_fvg_or_order_block"]}

    poi = sorted(candidates, key=lambda item: (item["poi_type"] == "fvg", -item["poi_width"]), reverse=True)[0]
    retest = _retest_status(candles, poi, int(cfg["max_retracement_wait_candles"]))
    poi.update(retest)
    return poi


def enforce_session_trade_limit(
    session_state: Mapping[str, Any] | None,
    killzone_status: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Prevent overtrading inside a single kill zone/session."""

    cfg = _config(config)
    session_state = session_state or {}
    name = str(killzone_status.get("active_killzone_name") or "unknown_session")
    trades_by_session = session_state.get("trades_by_session", {})
    session_trades = int(trades_by_session.get(name, session_state.get("session_trades", 0)) or 0)
    open_trades = int(session_state.get("open_trades", session_state.get("open_positions", 0)) or 0)
    daily_trades = int(session_state.get("daily_trades", 0) or 0)
    max_per_session = int(cfg["max_trades_per_killzone"])
    max_open = int(cfg["max_open_trades"])
    max_daily = int(cfg["max_daily_trades"])
    reasons: list[str] = []
    if session_trades >= max_per_session:
        reasons.append("session_trade_limit_reached")
    if open_trades >= max_open:
        reasons.append("open_trade_limit_reached")
    if daily_trades >= max_daily:
        reasons.append("daily_trade_limit_reached")
    return {
        "trade_limit_ok": not reasons,
        "active_session": name,
        "session_trades": session_trades,
        "max_trades_per_killzone": max_per_session,
        "open_trades": open_trades,
        "max_open_trades": max_open,
        "daily_trades": daily_trades,
        "max_daily_trades": max_daily,
        "rejection_reasons": reasons,
    }


def score_killzone_scalp_setup(
    setup: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a kill-zone scalp setup from 0 to 10."""

    cfg = _config(config)
    context = context or {}
    score = 0.0
    components: dict[str, float] = {}
    reasons = list(setup.get("rejection_reasons", []))

    if setup.get("killzone", {}).get("in_killzone"):
        components["killzone"] = 1.0
    if setup.get("sweep", {}).get("sweep_detected"):
        components["liquidity_sweep"] = min(1.6, float(setup["sweep"].get("sweep_quality_score", 6.0)) / 6.0)
    if setup.get("mss", {}).get("mss_confirmed"):
        components["mss_displacement"] = 2.0
    if setup.get("entry_poi", {}).get("entry_poi_detected"):
        components["fvg_or_ob"] = 1.5 if setup["entry_poi"].get("poi_type") == "fvg" else 1.1
    if setup.get("entry_poi", {}).get("retest_status") == "retested":
        components["retracement"] = 1.0
    if setup.get("risk", {}).get("rr") and float(setup["risk"]["rr"]) >= float(cfg["min_rr"]):
        components["rr"] = min(1.2, float(setup["risk"]["rr"]) / 3.0)
    if context.get("htf_context", {}).get("mss_confirmed") or context.get("mtf_confirmation", {}).get("mss_confirmed"):
        components["mtf_confirmation"] = 0.8
    elif str(context.get("timeframe", cfg["timeframe"])).lower() in {"1m", "m1"}:
        reasons.append("no_5m_mss_confirmation")

    total = round(_clamp(sum(components.values()), 0.0, 10.0), 2)
    if str(context.get("timeframe", cfg["timeframe"])).lower() in {"1m", "m1"} and "mtf_confirmation" not in components:
        reasons.append("low_quality_1m_setup")
        total = min(total, float(cfg["low_quality_1m_score_cap"]))

    min_score = float(cfg["minimum_setup_score"])
    trade_allowed = total >= min_score and not reasons
    if total < min_score:
        reasons.append("confirmation_score_below_minimum_threshold")
    return {
        "total_score": total,
        "minimum_required_score": min_score,
        "components": components,
        "trade_allowed": trade_allowed,
        "rejection_reasons": _unique(reasons),
    }


def generate_killzone_scalp_signal(
    context: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a complete Kill Zone Scalping setup decision."""

    cfg = _config(config)
    candles = _candles(context.get("candles", context.get("df")))
    rejection_reasons: list[str] = []
    if len(candles) < int(cfg["min_total_candles"]):
        rejection_reasons.append("insufficient_closed_candles")

    timestamp = context.get("timestamp") or (candles[-1].timestamp if candles else None)
    killzone = is_in_killzone(timestamp, context.get("killzones", cfg["killzones"]), cfg["broker_timezone"])
    if not killzone["in_killzone"]:
        rejection_reasons.append("outside_killzone")

    rejection_reasons.extend(_environment_rejections(candles, context, cfg))
    limit = enforce_session_trade_limit(context.get("session_state"), killzone, cfg)
    rejection_reasons.extend(limit["rejection_reasons"])

    active_window = killzone["active_killzone"]
    sweep = detect_killzone_liquidity_sweep(candles, context.get("liquidity_pools", []), active_window, cfg)
    if not sweep.get("sweep_detected"):
        rejection_reasons.extend(sweep.get("rejection_reasons", []))
    mss = detect_killzone_mss(candles, sweep, context.get("structure_swings"), cfg)
    if not mss.get("mss_confirmed"):
        rejection_reasons.extend(mss.get("rejection_reasons", []))
    entry_poi = detect_killzone_fvg_or_ob(candles, mss, cfg)
    if not entry_poi.get("entry_poi_detected"):
        rejection_reasons.extend(entry_poi.get("rejection_reasons", []))
    elif entry_poi.get("retest_status") != "retested":
        rejection_reasons.append("entry_retracement_not_confirmed")

    target = _select_scalp_target(candles, _direction(sweep.get("direction")), context.get("liquidity_pools", []), cfg)
    if not target["target_valid"]:
        rejection_reasons.extend(target["rejection_reasons"])
    if target.get("target_distance") and _spread_from_context(context, cfg) / max(
        float(target["target_distance"]), 1e-9
    ) > float(cfg["max_spread_to_target_ratio"]):
        rejection_reasons.append("spread_too_large_relative_to_target")

    risk = _risk_plan(candles, sweep, entry_poi, target, cfg)
    if not risk["risk_valid"]:
        rejection_reasons.extend(risk["rejection_reasons"])

    setup = {
        "killzone": killzone,
        "sweep": sweep,
        "mss": mss,
        "entry_poi": entry_poi,
        "target": target,
        "risk": risk,
        "rejection_reasons": _unique(rejection_reasons),
    }
    score = score_killzone_scalp_setup(setup, context, cfg)
    rejection_reasons.extend(score["rejection_reasons"])
    rejection_reasons = _unique(rejection_reasons)

    if "outside_killzone" in rejection_reasons:
        status = KillZoneScalpStatus.OUTSIDE_KILLZONE.value
    elif rejection_reasons:
        status = KillZoneScalpStatus.REJECTED.value
    elif entry_poi.get("retest_status") != "retested":
        status = KillZoneScalpStatus.WAITING_FOR_RETRACEMENT.value
    else:
        status = KillZoneScalpStatus.VALID.value

    direction = _direction(sweep.get("direction"))
    return {
        "strategy": "killzone_scalping",
        "symbol": context.get("symbol", "XAUUSD"),
        "signal_status": status,
        "trade_allowed": status == KillZoneScalpStatus.VALID.value,
        "direction": direction,
        "killzone": killzone,
        "sweep": sweep,
        "mss": mss,
        "entry_poi": entry_poi,
        "target": target,
        "risk": risk,
        "session_trade_limit": limit,
        "score": score,
        "rejection_reasons": rejection_reasons,
        "uses_closed_candles_only": True,
    }


def _value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _rows(data: Any) -> list[Any]:
    if data is None:
        return []
    if hasattr(data, "to_dict"):
        try:
            return list(data.to_dict("records"))
        except TypeError:
            pass
    return list(data)


def _candles(data: Any, *, closed_only: bool = True) -> list[_Candle]:
    candles: list[_Candle] = []
    for position, row in enumerate(_rows(data)):
        is_closed = bool(_value(row, "is_closed", True))
        if closed_only and not is_closed:
            continue
        try:
            candles.append(
                _Candle(
                    position=position,
                    index=int(_value(row, "index", position)),
                    timestamp=_value(row, "timestamp", _value(row, "time", position)),
                    open=float(_value(row, "open")),
                    high=float(_value(row, "high")),
                    low=float(_value(row, "low")),
                    close=float(_value(row, "close")),
                    volume=float(_value(row, "volume", 0.0) or 0.0),
                    is_closed=is_closed,
                )
            )
        except (TypeError, ValueError):
            continue
    return candles


def _config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    defaults = {
        "broker_timezone": "UTC",
        "killzones": [
            {"name": "London Kill Zone", "start_time": "07:00", "end_time": "10:00", "timezone": "UTC"},
            {"name": "New York Kill Zone", "start_time": "12:30", "end_time": "16:00", "timezone": "UTC"},
            {"name": "London/New York Overlap", "start_time": "12:30", "end_time": "14:30", "timezone": "UTC"},
        ],
        "timeframe": "5m",
        "min_total_candles": 8,
        "atr_period": 14,
        "structure_lookback": 5,
        "sweep_buffer": 0.03,
        "mss_break_buffer": 0.03,
        "max_mss_wait_candles": 5,
        "min_body_to_range": 0.55,
        "displacement_min_range_to_atr": 0.80,
        "min_fvg_size": 0.03,
        "max_poi_width": 3.0,
        "max_entry_wait_candles": 5,
        "max_retracement_wait_candles": 5,
        "stop_buffer": 0.10,
        "minimum_target_distance": 0.50,
        "minimum_target_distance_for_1m": 0.80,
        "min_rr": 1.4,
        "minimum_setup_score": 7.0,
        "low_quality_1m_score_cap": 5.5,
        "max_spread": 0.35,
        "max_spread_to_target_ratio": 0.25,
        "max_candle_range": 8.0,
        "news_block_minutes": 15,
        "max_trades_per_killzone": 1,
        "max_open_trades": 1,
        "max_daily_trades": 4,
    }
    if config:
        defaults.update(dict(config))
    return defaults


def _as_datetime(value: Any, broker_timezone: str | timezone | None = "UTC") -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif value is None:
        dt = datetime.now(tz=_tz(broker_timezone or "UTC"))
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz(broker_timezone or "UTC"))
    return dt


def _tz(value: str | timezone | None) -> timezone:
    if isinstance(value, timezone):
        return value
    if value in {None, "UTC", "utc"}:
        return timezone.utc
    return ZoneInfo(str(value))


def _parse_clock(value: str) -> time:
    hour, minute, *_ = [int(part) for part in value.split(":")]
    return time(hour, minute)


def _window_bounds(local_ts: datetime, start: str, end: str, tz: timezone) -> tuple[datetime, datetime]:
    start_time = _parse_clock(start)
    end_time = _parse_clock(end)
    start_dt = datetime.combine(local_ts.date(), start_time, tzinfo=tz)
    end_dt = datetime.combine(local_ts.date(), end_time, tzinfo=tz)
    if end_dt < start_dt:
        end_dt = end_dt + timedelta(days=1)
    return start_dt, end_dt


def _windows(config: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if config is None:
        return _config()["killzones"]
    if isinstance(config, Mapping):
        if "windows" in config:
            return list(config["windows"])
        if "killzones" in config:
            return list(config["killzones"])
        return [config]
    return list(config)


def _atr(candles: Sequence[_Candle], period: int = 14, end_position: int | None = None) -> float:
    if not candles:
        return 1.0
    end = len(candles) if end_position is None else max(1, min(len(candles), end_position + 1))
    window = candles[max(0, end - max(1, period)) : end]
    return max(mean([c.range for c in window]), 1e-9) if window else 1.0


def _pool_side(pool: Mapping[str, Any]) -> str:
    raw = str(_value(pool, "side", _value(pool, "direction", ""))).lower()
    if raw in {"sell_side", "sellside", "ssl", "low", "equal_lows", "asian_low", "london_low"}:
        return "sell_side"
    if raw in {"buy_side", "buyside", "bsl", "high", "equal_highs", "asian_high", "london_high"}:
        return "buy_side"
    return raw


def _pool_bounds(pool: Mapping[str, Any]) -> tuple[float, float]:
    price = float(_value(pool, "price", 0.0))
    low = float(_value(pool, "zone_low", _value(pool, "low", price)))
    high = float(_value(pool, "zone_high", _value(pool, "high", price)))
    return min(low, high), max(low, high)


def _pool_swept(pool: Mapping[str, Any]) -> bool:
    return bool(_value(pool, "swept", _value(pool, "is_swept", False)))


def _sweep_event(
    candle: _Candle,
    pool: Mapping[str, Any],
    side: str,
    direction: str,
    swept_level: float,
    pool_quality: float,
    depth_atr: float,
    window_state: Mapping[str, Any],
) -> dict[str, Any]:
    quality = _clamp(5.5 + pool_quality * 0.25 + depth_atr, 0.0, 10.0)
    return {
        "sweep_detected": True,
        "sweep_index": candle.index,
        "sweep_position": candle.position,
        "sweep_timestamp": candle.timestamp,
        "swept_side": side,
        "direction": direction,
        "swept_level": round(swept_level, 8),
        "sweep_extreme": round(candle.low if direction == "bullish" else candle.high, 8),
        "liquidity_pool_id": _value(pool, "id", _value(pool, "name", "liquidity_pool")),
        "pool_quality_score": round(pool_quality, 2),
        "sweep_quality_score": round(quality, 2),
        "reclaimed_swept_level": True,
        "active_killzone_name": window_state.get("active_killzone_name"),
        "rejection_reasons": [],
    }


def _direction(value: Any) -> str:
    raw = str(value.value if isinstance(value, Enum) else value or "unknown").lower()
    if raw in {"bull", "buy", "long", "bullish", "sell_side", "ssl"}:
        return "bullish"
    if raw in {"bear", "sell", "short", "bearish", "buy_side", "bsl"}:
        return "bearish"
    return "unknown"


def _swing_level(
    swings: Sequence[Mapping[str, Any]] | None,
    kind: str,
    candles: Sequence[_Candle],
    fallback: Any,
) -> float:
    if swings:
        prices = [
            float(s["price"])
            for s in swings
            if str(s.get("kind", s.get("type", ""))).lower() == kind and bool(s.get("confirmed", True))
        ]
        if prices:
            return prices[-1]
    values = [c.high if kind == "high" else c.low for c in candles]
    return float(fallback(values))


def _displacement(candle: _Candle, atr: float, cfg: Mapping[str, Any]) -> dict[str, Any]:
    body_ok = candle.body_to_range >= float(cfg["min_body_to_range"])
    range_ok = candle.range >= atr * float(cfg["displacement_min_range_to_atr"])
    return {
        "valid": body_ok and range_ok,
        "body_to_range": round(candle.body_to_range, 4),
        "range_to_atr": round(candle.range / max(atr, 1e-9), 4),
        "body_ok": body_ok,
        "range_ok": range_ok,
    }


def _mss_event(candle: _Candle, direction: str, broken_level: float, displacement: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "mss_confirmed": True,
        "direction": direction,
        "mss_index": candle.index,
        "mss_position": candle.position,
        "mss_timestamp": candle.timestamp,
        "broken_structure_level": round(broken_level, 8),
        "displacement": dict(displacement),
        "rejection_reasons": [],
    }


def _poi_event(
    poi_type: str, direction: str, candle: _Candle, low: float, high: float, created_position: int
) -> dict[str, Any]:
    return {
        "entry_poi_detected": True,
        "poi_type": poi_type,
        "direction": direction,
        "poi_low": round(low, 8),
        "poi_high": round(high, 8),
        "entry_price": round((low + high) / 2.0, 8),
        "poi_width": round(high - low, 8),
        "created_index": candle.index,
        "created_position": created_position,
        "rejection_reasons": [],
    }


def _order_block_poi(
    candles: Sequence[_Candle],
    direction: str,
    mss_position: int,
    cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    for candle in reversed(candles[max(0, mss_position - 4) : mss_position + 1]):
        if direction == "bullish" and candle.bearish and candle.range <= float(cfg["max_poi_width"]):
            return _poi_event("order_block", direction, candle, candle.low, candle.high, candle.position)
        if direction == "bearish" and candle.bullish and candle.range <= float(cfg["max_poi_width"]):
            return _poi_event("order_block", direction, candle, candle.low, candle.high, candle.position)
    return None


def _retest_status(candles: Sequence[_Candle], poi: Mapping[str, Any], max_wait: int) -> dict[str, Any]:
    low = float(poi["poi_low"])
    high = float(poi["poi_high"])
    created_position = int(poi["created_position"])
    for candle in candles[created_position + 1 : created_position + 1 + max_wait]:
        if candle.low <= high and candle.high >= low:
            return {
                "retest_status": "retested",
                "retest_index": candle.index,
                "retest_position": candle.position,
                "retest_timestamp": candle.timestamp,
            }
    return {
        "retest_status": "waiting_for_retracement",
        "retest_index": None,
        "retest_position": None,
        "retest_timestamp": None,
    }


def _select_scalp_target(
    candles: Sequence[_Candle],
    direction: str,
    liquidity_pools: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    if not candles or direction == "unknown":
        return {"target_valid": False, "rejection_reasons": ["missing_target_direction"]}
    current = candles[-1]
    current_price = current.close
    target_side = "buy_side" if direction == "bullish" else "sell_side"
    targets: list[dict[str, Any]] = []
    for pool in liquidity_pools:
        if _pool_swept(pool):
            continue
        if _pool_side(pool) != target_side:
            continue
        zone_low, zone_high = _pool_bounds(pool)
        price = zone_high if direction == "bullish" else zone_low
        distance = price - current_price if direction == "bullish" else current_price - price
        if distance <= 0:
            continue
        if direction == "bullish" and current.high >= price:
            continue
        if direction == "bearish" and current.low <= price:
            continue
        targets.append(
            {
                "target_id": _value(pool, "id", _value(pool, "name", "nearest_liquidity")),
                "target_side": target_side,
                "target_price": round(price, 8),
                "target_distance": round(distance, 8),
            }
        )
    if not targets:
        return {"target_valid": False, "rejection_reasons": ["no_nearby_scalp_liquidity_target"]}
    target = sorted(targets, key=lambda item: item["target_distance"])[0]
    min_distance = float(cfg["minimum_target_distance"])
    if target["target_distance"] < min_distance:
        target.update({"target_valid": False, "rejection_reasons": ["target_distance_too_small"]})
        return target
    target.update({"target_valid": True, "rejection_reasons": []})
    return target


def _risk_plan(
    candles: Sequence[_Candle],
    sweep: Mapping[str, Any],
    poi: Mapping[str, Any],
    target: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    if not sweep.get("sweep_detected") or not poi.get("entry_poi_detected") or not target.get("target_valid"):
        return {"risk_valid": False, "rejection_reasons": ["incomplete_setup_for_risk_plan"]}
    direction = _direction(sweep.get("direction"))
    entry = float(poi["entry_price"])
    target_price = float(target["target_price"])
    stop_buffer = float(cfg["stop_buffer"])
    if direction == "bullish":
        stop = float(sweep["sweep_extreme"]) - stop_buffer
        risk = entry - stop
        reward = target_price - entry
    else:
        stop = float(sweep["sweep_extreme"]) + stop_buffer
        risk = stop - entry
        reward = entry - target_price
    reasons: list[str] = []
    if risk <= 0:
        reasons.append("invalid_stop_distance")
    if reward <= 0:
        reasons.append("invalid_target_distance")
    rr = reward / risk if risk > 0 else 0.0
    if rr < float(cfg["min_rr"]):
        reasons.append("rr_below_minimum")
    spread = _spread_from_context({}, cfg)
    if target.get("target_distance", reward) and spread / max(
        float(target.get("target_distance", reward)), 1e-9
    ) > float(cfg["max_spread_to_target_ratio"]):
        reasons.append("spread_too_large_relative_to_target")
    return {
        "risk_valid": not reasons,
        "entry_price": round(entry, 8),
        "stop_loss": round(stop, 8),
        "target_price": round(target_price, 8),
        "risk_distance": round(max(risk, 0.0), 8),
        "reward_distance": round(max(reward, 0.0), 8),
        "rr": round(rr, 4),
        "rejection_reasons": reasons,
    }


def _environment_rejections(
    candles: Sequence[_Candle], context: Mapping[str, Any], cfg: Mapping[str, Any]
) -> list[str]:
    reasons: list[str] = []
    news = context.get("news_status", {})
    if bool(news.get("restricted", news.get("news_restricted", False))):
        reasons.append("news_restricted")
    if bool(news.get("first_news_spike_signal", False)):
        reasons.append("first_news_spike_signal")

    spread = _spread_from_context(context, cfg)
    if spread > float(cfg["max_spread"]):
        reasons.append("spread_too_high")

    if candles:
        max_range = max(c.range for c in candles)
        if max_range > float(cfg["max_candle_range"]):
            reasons.append("max_candle_size_exceeded")
    return reasons


def _spread_from_context(context: Mapping[str, Any], cfg: Mapping[str, Any]) -> float:
    spread_status = context.get("spread_status", {}) if context else {}
    return float(spread_status.get("spread_points", spread_status.get("spread", 0.0)) or 0.0)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _unique(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out
