"""ICT Silver Bullet time-window strategy model.

Silver Bullet is deliberately narrower than a generic sweep strategy. A setup is
valid only inside a configured intraday window and only after the full sequence:

window -> liquidity -> sweep/reclaim -> displacement -> FVG -> retest ->
stop/target/RR validation -> scoring.

This module is pure Python and does not place broker orders. It returns plain
dictionaries so tests, backtests, and future live orchestration can consume the
same deterministic output.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


class SilverBulletDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"


class SilverBulletStatus(str, Enum):
    VALID = "valid"
    REJECTED = "rejected"
    WAITING_FOR_RETEST = "waiting_for_retest"
    OUTSIDE_WINDOW = "outside_window"


class SilverBulletEntryMode(str, Enum):
    AGGRESSIVE = "aggressive"
    BALANCED = "balanced"
    CONSERVATIVE = "conservative"


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
    def bullish_close_position(self) -> float:
        return (self.close - self.low) / self.range

    @property
    def bearish_close_position(self) -> float:
        return (self.high - self.close) / self.range


def is_in_silver_bullet_window(
    timestamp: Any,
    window_config: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    broker_timezone: str | timezone | None = "UTC",
) -> dict[str, Any]:
    """Return the active Silver Bullet window for a timestamp, if any."""

    ts = _as_datetime(timestamp, broker_timezone)
    for window in _windows(window_config):
        if not bool(window.get("enabled", True)):
            continue
        window_tz = _tz(window.get("timezone", "UTC"))
        local_ts = ts.astimezone(window_tz)
        start_dt, end_dt = _window_bounds(local_ts, str(window["start_time"]), str(window["end_time"]), window_tz)
        if start_dt <= local_ts <= end_dt:
            return {
                "in_window": True,
                "active_window_name": str(window.get("window_name", window.get("name", "Silver Bullet Window"))),
                "active_window": dict(window),
                "window_start": start_dt.isoformat(),
                "window_end": end_dt.isoformat(),
                "timestamp_in_window_timezone": local_ts.isoformat(),
                "minutes_from_window_start": int((local_ts - start_dt).total_seconds() // 60),
                "minutes_to_window_end": int((end_dt - local_ts).total_seconds() // 60),
            }
    return {
        "in_window": False,
        "active_window_name": None,
        "active_window": None,
        "window_start": None,
        "window_end": None,
        "timestamp_in_window_timezone": ts.isoformat(),
        "minutes_from_window_start": None,
        "minutes_to_window_end": None,
    }


def detect_window_liquidity(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    liquidity_pools: Sequence[Mapping[str, Any]],
    active_window: Mapping[str, Any] | None,
    htf_bias: Mapping[str, Any] | str | None = None,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Rank relevant liquidity for the active Silver Bullet window."""

    cfg = _config(config)
    candles = _closed_candles(df)
    if not candles or active_window is None:
        return []
    current_price = candles[-1].close
    max_distance = float(cfg["max_liquidity_distance"])
    min_quality = float(_get(active_window, "min_liquidity_quality", default=cfg["minimum_liquidity_quality"]))
    session = _window_session(active_window)
    draw = _draw_on_liquidity(htf_bias)
    ranked: list[dict[str, Any]] = []
    for pool in liquidity_pools:
        if _pool_swept(pool):
            continue
        quality = float(_get(pool, "quality_score", default=5.0))
        if quality < min_quality:
            continue
        zone_low, zone_high = _pool_bounds(pool)
        price = float(_get(pool, "price", default=(zone_low + zone_high) / 2.0))
        distance = abs(price - current_price)
        if max_distance > 0 and distance > max_distance and not _is_external_liquidity(pool):
            continue
        side = str(_get(pool, "direction", "side", default="")).lower()
        pool_type = str(_get(pool, "liquidity_type", "type", default="generic_liquidity")).lower()
        session_bonus = _session_liquidity_bonus(session, pool_type)
        htf_bonus = _htf_liquidity_bonus(draw, side)
        distance_score = max(0.0, 2.0 - distance / max(max_distance, 1.0)) if max_distance > 0 else 1.0
        priority = _clamp(quality * 0.55 + session_bonus + htf_bonus + distance_score, 0, 10)
        enriched = dict(pool)
        enriched.update(
            {
                "priority_score": round(priority, 2),
                "session_relevance": round(session_bonus, 2),
                "htf_relevance": round(htf_bonus, 2),
                "distance_from_price": round(distance, 5),
            }
        )
        ranked.append(enriched)
    return sorted(ranked, key=lambda item: float(item["priority_score"]), reverse=True)


def detect_silver_bullet_sweep(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    window_liquidity: Sequence[Mapping[str, Any]],
    active_window: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Detect the best reclaim/rejection sweep inside the active window."""

    cfg = _config(config)
    candles = _closed_candles(df)
    if active_window is None:
        return None
    atr = _atr(candles, int(cfg["atr_period"]))
    sweep_buffer = _configured_buffer(cfg, "sweep_buffer", atr * float(cfg["sweep_buffer_atr_multiplier"]))
    candidates: list[dict[str, Any]] = []
    for candle in candles:
        window_status = is_in_silver_bullet_window(candle.timestamp, [active_window], cfg["broker_timezone"])
        if not window_status["in_window"]:
            continue
        for pool in window_liquidity:
            side = str(_get(pool, "direction", "side", default="")).lower()
            zone_low, zone_high = _pool_bounds(pool)
            quality = float(_get(pool, "quality_score", default=5.0))
            priority = float(_get(pool, "priority_score", default=quality))
            if side == "sell_side" and candle.low < zone_low - sweep_buffer and candle.close > zone_low:
                quality_score = _clamp(6.0 + priority * 0.25 + (zone_low - candle.low) / max(atr, 1e-9), 0, 10)
                candidates.append(
                    _sweep_event(
                        "sell_side",
                        SilverBulletDirection.BULLISH,
                        candle,
                        pool,
                        zone_low,
                        quality,
                        quality_score,
                        window_status,
                    )
                )
            if side == "buy_side" and candle.high > zone_high + sweep_buffer and candle.close < zone_high:
                quality_score = _clamp(6.0 + priority * 0.25 + (candle.high - zone_high) / max(atr, 1e-9), 0, 10)
                candidates.append(
                    _sweep_event(
                        "buy_side",
                        SilverBulletDirection.BEARISH,
                        candle,
                        pool,
                        zone_high,
                        quality,
                        quality_score,
                        window_status,
                    )
                )
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item["sweep_quality_score"], item["sweep_index"]), reverse=True)[0]


def detect_silver_bullet_fvg(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    sweep_event: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Detect displacement and the FVG it creates after the sweep."""

    if sweep_event is None:
        return None
    cfg = _config(config)
    candles = _closed_candles(df)
    if len(candles) < 3:
        return None
    direction = _direction(sweep_event.get("strategy_direction"))
    if direction is SilverBulletDirection.NONE:
        return None
    atr = _atr(candles, int(cfg["atr_period"]))
    by_index = {candle.index: candle for candle in candles}
    sweep_index = int(sweep_event["sweep_index"])
    for candle in candles:
        if candle.index <= sweep_index or candle.index > sweep_index + int(cfg["max_fvg_wait_candles"]):
            continue
        displacement = _displacement_event(candle, direction, atr, cfg)
        if displacement is None or displacement["oversized"]:
            continue
        fvg = _fvg_from_creation_candle(by_index, candle.index, direction, atr, cfg)
        if fvg is not None:
            return {
                "direction": direction.value,
                "displacement": displacement,
                "fvg": fvg,
                "valid_from_time": fvg["creation_time"],
            }
    return None


def generate_silver_bullet_signal(
    context: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a complete Silver Bullet signal or a deterministic no-trade reason."""

    cfg = _config(config)
    setup_df = context.get("m15_df", context.get("df", context.get("candles", [])))
    entry_df = context.get("m5_df", setup_df)
    candles = _closed_candles(setup_df)
    if len(candles) < 3:
        return _no_trade(context, SilverBulletStatus.REJECTED, ["insufficient_closed_candles"])
    window_status = is_in_silver_bullet_window(
        context.get("timestamp", candles[-1].timestamp), cfg["silver_bullet_windows"], cfg["broker_timezone"]
    )
    if not window_status["in_window"]:
        return _no_trade(
            context, SilverBulletStatus.OUTSIDE_WINDOW, ["outside_silver_bullet_window"], window=window_status
        )
    safety_reasons = _safety_filter_reasons(context, cfg)
    if safety_reasons:
        return _no_trade(context, SilverBulletStatus.REJECTED, safety_reasons, window=window_status)
    active_window = window_status["active_window"]
    window_liquidity = detect_window_liquidity(
        setup_df,
        context.get("liquidity_pools", []),
        active_window,
        context.get("htf_bias", context.get("higher_timeframe_bias")),
        cfg,
    )
    if not window_liquidity:
        return _no_trade(context, SilverBulletStatus.REJECTED, ["no_window_liquidity"], window=window_status)
    sweep = detect_silver_bullet_sweep(setup_df, window_liquidity, active_window, cfg)
    if sweep is None:
        return _no_trade(context, SilverBulletStatus.REJECTED, ["no_silver_bullet_sweep"], window=window_status)
    fvg_setup = detect_silver_bullet_fvg(setup_df, sweep, cfg)
    if fvg_setup is None:
        return _no_trade(
            context, SilverBulletStatus.REJECTED, ["no_valid_silver_bullet_fvg"], window=window_status, sweep=sweep
        )
    retest = _detect_fvg_retest(entry_df, fvg_setup["fvg"], fvg_setup["direction"], cfg)
    if retest is None:
        return _no_trade(
            context,
            SilverBulletStatus.WAITING_FOR_RETEST,
            ["waiting_for_fvg_retest"],
            window=window_status,
            sweep=sweep,
            fvg_setup=fvg_setup,
        )
    direction = _direction(fvg_setup["direction"])
    risk_plan = _risk_plan(
        sweep,
        retest,
        context.get("liquidity_pools", []),
        context,
        direction,
        _atr(candles, int(cfg["atr_period"])),
        cfg,
    )
    if risk_plan["target"] is None:
        return _no_trade(context, SilverBulletStatus.REJECTED, ["no_opposite_liquidity_target"], window=window_status)
    if risk_plan["rr"] < float(cfg["min_rr"]):
        return _no_trade(context, SilverBulletStatus.REJECTED, ["rr_below_minimum"], window=window_status)
    if bool(_get(context.get("htf_context", {}) or {}, "htf_blocker_present", default=False)):
        return _no_trade(context, SilverBulletStatus.REJECTED, ["htf_poi_blocks_target"], window=window_status)
    setup = {
        "direction": direction.value,
        "window": window_status,
        "window_liquidity": window_liquidity,
        "sweep": sweep,
        "displacement": fvg_setup["displacement"],
        "fvg": fvg_setup["fvg"],
        "entry": retest,
        "risk": risk_plan,
    }
    score = score_silver_bullet_setup(setup, context, cfg)
    if not score["trade_allowed"]:
        return _no_trade(
            context,
            SilverBulletStatus.REJECTED,
            score.get("hard_filter_reasons") or ["score_or_filter_failed"],
            window=window_status,
            setup=setup,
            score=score,
        )
    symbol = str(context.get("symbol", "XAUUSD"))
    return {
        "strategy": "ICT Silver Bullet",
        "symbol": symbol,
        "signal_id": f"{symbol}_SB_{direction.value.upper()}_{retest['retest_index']}",
        "signal_status": SilverBulletStatus.VALID.value,
        "trade_allowed": True,
        "direction": direction.value,
        "timestamp": retest["retest_time"],
        "window": window_status,
        "liquidity": {
            "swept_liquidity_id": sweep["swept_liquidity_id"],
            "liquidity_type": sweep["liquidity_type"],
            "swept_side": sweep["swept_side"],
            "swept_level": sweep["swept_level"],
            "liquidity_quality_score": sweep["liquidity_quality_score"],
        },
        "sweep": sweep,
        "displacement": fvg_setup["displacement"],
        "fvg": fvg_setup["fvg"],
        "entry": retest,
        "risk": risk_plan,
        "filters": _passed_filters(context),
        "score": score,
        "rejection_reasons": [],
        "warnings": [
            "Do not force another Silver Bullet in the same window unless new liquidity forms.",
            "Backtests should assume stop first when stop and target occur in the same candle.",
        ],
    }


def score_silver_bullet_setup(
    setup: Mapping[str, Any],
    context: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a complete Silver Bullet setup from 0 to 10."""

    cfg = _config(config)
    direction = _direction(setup.get("direction"))
    rr = float(_get(setup.get("risk", {}) or {}, "rr", default=0.0))
    sweep = setup.get("sweep", {}) or {}
    displacement = setup.get("displacement", {}) or {}
    fvg = setup.get("fvg", {}) or {}
    entry = setup.get("entry", {}) or {}
    window = setup.get("window", {}) or {}
    safety_reasons = _safety_filter_reasons(context, cfg)
    hard_reasons = list(safety_reasons)
    if not bool(_get(window, "in_window", default=False)):
        hard_reasons.append("outside_silver_bullet_window")
    if not sweep:
        hard_reasons.append("no_silver_bullet_sweep")
    if not fvg:
        hard_reasons.append("no_valid_silver_bullet_fvg")
    if not bool(_get(entry, "entry_triggered", default=False)):
        hard_reasons.append("no_fvg_retest")
    if rr < float(cfg["min_rr"]):
        hard_reasons.append("rr_below_minimum")
    component_scores = {
        "time_window": _time_window_score(window),
        "liquidity_quality": min(10.0, float(_get(sweep, "liquidity_quality_score", default=0.0))),
        "sweep_quality": min(10.0, float(_get(sweep, "sweep_quality_score", default=0.0))),
        "displacement_strength": min(10.0, float(_get(displacement, "strength_score", default=0.0))),
        "fvg_quality": min(10.0, float(_get(fvg, "quality_score", default=0.0))),
        "retest_entry": 8.5 if bool(_get(entry, "entry_triggered", default=False)) else 0.0,
        "htf_alignment": _htf_score(
            context.get("htf_bias", context.get("higher_timeframe_bias", "neutral")), direction
        ),
        "xauusd_safety": 9.0 if not safety_reasons else 1.0,
        "target_rr": min(10.0, rr / max(float(cfg["min_rr"]), 1e-9) * 8.0),
        "session_cleanliness": _session_cleanliness_score(context),
    }
    weights = {
        "time_window": 0.12,
        "liquidity_quality": 0.10,
        "sweep_quality": 0.12,
        "displacement_strength": 0.12,
        "fvg_quality": 0.10,
        "retest_entry": 0.10,
        "htf_alignment": 0.08,
        "xauusd_safety": 0.10,
        "target_rr": 0.12,
        "session_cleanliness": 0.04,
    }
    total = round(sum(component_scores[key] * weights[key] for key in weights), 2)
    min_score = float(cfg["minimum_setup_score"])
    return {
        "total_score": total,
        "grade": _grade(total),
        "trade_allowed": total >= min_score and not hard_reasons,
        "component_scores": {key: round(value, 2) for key, value in component_scores.items()},
        "minimum_required": min_score,
        "hard_filter_reasons": _dedupe(hard_reasons),
    }


def _sweep_event(
    side: str,
    direction: SilverBulletDirection,
    candle: _Candle,
    pool: Mapping[str, Any],
    swept_level: float,
    liquidity_quality: float,
    sweep_quality: float,
    window_status: Mapping[str, Any],
) -> dict[str, Any]:
    reclaim_status = (
        "reclaimed_above_swept_level" if direction is SilverBulletDirection.BULLISH else "rejected_below_swept_level"
    )
    return {
        "sweep_id": f"SB_SWEEP_{side.upper()}_{candle.index}_{_get(pool, 'liquidity_id', 'id', default='pool')}",
        "strategy_direction": direction.value,
        "swept_side": side,
        "swept_liquidity_id": _get(pool, "liquidity_id", "id", default="unknown"),
        "liquidity_type": _get(pool, "liquidity_type", "type", default=f"{side}_liquidity"),
        "liquidity_quality_score": round(liquidity_quality, 2),
        "sweep_index": candle.index,
        "sweep_time": candle.timestamp,
        "sweep_extreme": candle.low if direction is SilverBulletDirection.BULLISH else candle.high,
        "sweep_low": candle.low,
        "sweep_high": candle.high,
        "swept_level": swept_level,
        "reclaim_status": reclaim_status,
        "window_name": window_status["active_window_name"],
        "sweep_quality_score": round(sweep_quality, 2),
    }


def _detect_fvg_retest(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    fvg: Mapping[str, Any],
    direction: str,
    cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    candles = _closed_candles(df)
    expected = _direction(direction)
    expiry = int(cfg["fvg_retest_expiry_candles"])
    entry_mode = SilverBulletEntryMode(str(cfg["entry_mode"]).lower())
    creation_index = int(fvg["creation_index"])
    zone_low = float(fvg["zone_low"])
    zone_high = float(fvg["zone_high"])
    zone_mid = float(fvg["zone_mid"])
    for candle in candles:
        if candle.index <= creation_index:
            continue
        if candle.index > creation_index + expiry:
            return None
        if not (candle.low <= zone_high and candle.high >= zone_low):
            continue
        if entry_mode is SilverBulletEntryMode.CONSERVATIVE:
            if expected is SilverBulletDirection.BULLISH and not (candle.bullish and candle.close > zone_mid):
                continue
            if expected is SilverBulletDirection.BEARISH and not (candle.bearish and candle.close < zone_mid):
                continue
            entry_price = candle.close
            entry_type = f"{expected.value}_silver_bullet_confirmation_entry"
        else:
            entry_price = zone_mid
            entry_type = f"{expected.value}_silver_bullet_fvg_midpoint_entry"
        return {
            "entry_triggered": True,
            "direction": expected.value,
            "entry_type": entry_type,
            "entry_price": round(entry_price, 5),
            "retest_index": candle.index,
            "retest_time": candle.timestamp,
            "retest_status": "fvg_retested",
            "confirmation_mode": entry_mode.value,
        }
    return None


def _displacement_event(
    candle: _Candle,
    direction: SilverBulletDirection,
    atr: float,
    cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    body_to_range = candle.body / candle.range
    range_to_atr = candle.range / max(atr, 1e-9)
    close_position = (
        candle.bullish_close_position if direction is SilverBulletDirection.BULLISH else candle.bearish_close_position
    )
    directional = candle.bullish if direction is SilverBulletDirection.BULLISH else candle.bearish
    if (
        not directional
        or body_to_range < float(cfg["displacement_min_body_to_range"])
        or range_to_atr < float(cfg["displacement_min_range_to_atr"])
        or close_position < float(cfg["displacement_min_close_position"])
    ):
        return None
    oversized = range_to_atr > float(cfg["max_displacement_atr_multiplier"])
    strength = _clamp(body_to_range * 3.0 + min(range_to_atr, 3.0) * 1.5 + close_position * 2.0, 0, 10)
    return {
        "direction": direction.value,
        "confirmed": True,
        "start_index": candle.index,
        "end_index": candle.index,
        "body_to_range_ratio": round(body_to_range, 3),
        "range_to_atr_ratio": round(range_to_atr, 3),
        "close_position_score": round(close_position, 3),
        "oversized": oversized,
        "strength_score": round(strength, 2),
    }


def _fvg_from_creation_candle(
    by_index: Mapping[int, _Candle],
    creation_index: int,
    direction: SilverBulletDirection,
    atr: float,
    cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    c1 = by_index.get(creation_index - 2)
    c2 = by_index.get(creation_index - 1)
    c3 = by_index.get(creation_index)
    if c1 is None or c2 is None or c3 is None:
        return None
    if direction is SilverBulletDirection.BULLISH and c1.high < c3.low:
        return _fvg_event(direction, c1, c2, c3, c1.high, c3.low, atr, cfg)
    if direction is SilverBulletDirection.BEARISH and c1.low > c3.high:
        return _fvg_event(direction, c1, c2, c3, c3.high, c1.low, atr, cfg)
    return None


def _fvg_event(
    direction: SilverBulletDirection,
    candle_1: _Candle,
    candle_2: _Candle,
    candle_3: _Candle,
    zone_low: float,
    zone_high: float,
    atr: float,
    cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    fvg_size = zone_high - zone_low
    if fvg_size < float(cfg["min_fvg_size"]):
        return None
    if fvg_size > atr * float(cfg["max_fvg_atr_multiplier"]):
        return None
    body_ratio = candle_2.body / candle_2.range
    quality = _clamp(6.0 + min(body_ratio * 2.5, 2.0) + min(fvg_size / max(atr, 1e-9), 1.5), 0, 10)
    return {
        "fvg_id": f"SB_{direction.value.upper()}_FVG_{candle_3.index}",
        "fvg_type": f"{direction.value}_fvg",
        "direction": direction.value,
        "zone_low": round(zone_low, 5),
        "zone_high": round(zone_high, 5),
        "zone_mid": round((zone_low + zone_high) / 2.0, 5),
        "fvg_size": round(fvg_size, 5),
        "creation_index": candle_3.index,
        "creation_time": candle_3.timestamp,
        "candle_1_index": candle_1.index,
        "candle_2_index": candle_2.index,
        "candle_3_index": candle_3.index,
        "active_status": True,
        "filled_percent": 0.0,
        "quality_score": round(quality, 2),
    }


def _risk_plan(
    sweep: Mapping[str, Any],
    entry: Mapping[str, Any],
    liquidity_pools: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    direction: SilverBulletDirection,
    atr: float,
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    entry_price = float(entry["entry_price"])
    spread_buffer = float(_get(context.get("spread_status", {}) or {}, "spread_points", "spread", default=0.0))
    spread_buffer *= float(cfg["spread_buffer_multiplier"])
    atr_buffer = atr * float(cfg["stop_atr_buffer_multiplier"])
    stop = (
        float(sweep["sweep_low"]) - atr_buffer - spread_buffer
        if direction is SilverBulletDirection.BULLISH
        else float(sweep["sweep_high"]) + atr_buffer + spread_buffer
    )
    target = _select_target(entry_price, stop, liquidity_pools, context, direction, float(cfg["min_rr"]))
    rr = 0.0
    if target is not None:
        rr = (
            (target - entry_price) / max(entry_price - stop, 1e-9)
            if direction is SilverBulletDirection.BULLISH
            else (entry_price - target) / max(stop - entry_price, 1e-9)
        )
    return {
        "stop_loss": round(stop, 5),
        "stop_reference": (
            "below_sweep_low_with_atr_and_spread_buffer"
            if direction is SilverBulletDirection.BULLISH
            else "above_sweep_high_with_atr_and_spread_buffer"
        ),
        "target": round(target, 5) if target is not None else None,
        "target_reference": (
            "opposite_buy_side_liquidity"
            if direction is SilverBulletDirection.BULLISH
            else "opposite_sell_side_liquidity"
        ),
        "risk_distance": round(abs(entry_price - stop), 5),
        "reward_distance": round(abs((target or entry_price) - entry_price), 5),
        "rr": round(rr, 2),
        "min_rr_required": float(cfg["min_rr"]),
    }


def _select_target(
    entry: float,
    stop: float,
    pools: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    direction: SilverBulletDirection,
    min_rr: float,
) -> float | None:
    target_context = context.get("target_liquidity")
    if isinstance(target_context, Mapping):
        price = _float(_get(target_context, "price", "target_price", "zone_mid", default=None))
        if price is not None:
            return price
    side = "buy_side" if direction is SilverBulletDirection.BULLISH else "sell_side"
    candidates: list[float] = []
    for pool in pools:
        if str(_get(pool, "direction", "side", default="")).lower() != side or _pool_swept(pool):
            continue
        zone_low, zone_high = _pool_bounds(pool)
        target = zone_high if direction is SilverBulletDirection.BULLISH else zone_low
        if direction is SilverBulletDirection.BULLISH and target > entry:
            candidates.append(target)
        if direction is SilverBulletDirection.BEARISH and target < entry:
            candidates.append(target)
    candidates = sorted(candidates, reverse=direction is SilverBulletDirection.BEARISH)
    risk = abs(entry - stop)
    for price in candidates:
        if risk > 0 and abs(price - entry) / risk >= min_rr:
            return price
    return None


def _safety_filter_reasons(context: Mapping[str, Any], cfg: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    news = context.get("news_status", {}) or {}
    spread = context.get("spread_status", {}) or {}
    session = context.get("session_context", {}) or {}
    if bool(_get(news, "restricted", "news_restricted", default=False)):
        reasons.append("news_restricted")
    if bool(_get(news, "first_news_spike", default=False)):
        reasons.append("first_news_spike_signal")
    if bool(_get(news, "post_news_stabilized", default=True)) is False:
        reasons.append("post_news_structure_not_stabilized")
    spread_value = _float(_get(spread, "spread_points", "spread", default=None))
    spread_status = str(_get(spread, "status", default="normal")).lower()
    if bool(_get(spread, "spread_safe", default=True)) is False or spread_status in {
        "high",
        "wide",
        "unsafe",
        "too_high",
    }:
        reasons.append("spread_too_high")
    elif spread_value is not None and spread_value > float(cfg["max_spread_points"]):
        reasons.append("spread_too_high")
    if str(_get(session, "liquidity_condition", "status", default="normal")).lower() in {
        "low_liquidity_chop",
        "dead",
        "closed",
    }:
        reasons.append("low_liquidity_session_chop")
    if bool(_get(context, "duplicate_signal", default=False)):
        reasons.append("duplicate_signal")
    return _dedupe(reasons)


def _no_trade(
    context: Mapping[str, Any], status: SilverBulletStatus, reasons: Sequence[str], **payload: Any
) -> dict[str, Any]:
    return {
        "strategy": "ICT Silver Bullet",
        "symbol": str(context.get("symbol", "XAUUSD")),
        "signal_status": status.value,
        "trade_allowed": False,
        "direction": None,
        "rejection_reason": reasons[0] if reasons else None,
        "rejection_reasons": _dedupe(list(reasons)),
        **payload,
    }


def _passed_filters(context: Mapping[str, Any]) -> dict[str, str]:
    return {
        "news_filter": "passed",
        "spread_filter": "passed",
        "time_window_filter": "passed",
        "fvg_size_filter": "passed",
        "displacement_size_filter": "passed",
        "chop_filter": "passed",
        "htf_blocker_filter": "passed",
        "htf_bias": str(context.get("htf_bias", "neutral")),
    }


def _config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    data = dict(config or {})
    return {
        "silver_bullet_windows": data.get("silver_bullet_windows", data.get("windows", _default_windows())),
        "broker_timezone": data.get("broker_timezone", "UTC"),
        "atr_period": int(data.get("atr_period", 14)),
        "minimum_liquidity_quality": float(data.get("minimum_liquidity_quality", 0.0)),
        "max_liquidity_distance": float(data.get("max_liquidity_distance", 0.0)),
        "sweep_buffer": data.get("sweep_buffer"),
        "sweep_buffer_atr_multiplier": float(data.get("sweep_buffer_atr_multiplier", 0.02)),
        "displacement_min_body_to_range": float(data.get("displacement_min_body_to_range", 0.55)),
        "displacement_min_range_to_atr": float(data.get("displacement_min_range_to_atr", 1.0)),
        "displacement_min_close_position": float(data.get("displacement_min_close_position", 0.70)),
        "max_displacement_atr_multiplier": float(data.get("max_displacement_atr_multiplier", 3.5)),
        "max_fvg_atr_multiplier": float(data.get("max_fvg_atr_multiplier", 2.5)),
        "max_fvg_wait_candles": int(data.get("max_fvg_wait_candles", 8)),
        "min_fvg_size": float(data.get("min_fvg_size", 0.01)),
        "fvg_retest_expiry_candles": int(data.get("fvg_retest_expiry_candles", 12)),
        "entry_mode": str(data.get("entry_mode", SilverBulletEntryMode.BALANCED.value)).lower(),
        "stop_atr_buffer_multiplier": float(data.get("stop_atr_buffer_multiplier", 0.05)),
        "spread_buffer_multiplier": float(data.get("spread_buffer_multiplier", 1.0)),
        "max_spread_points": float(data.get("max_spread_points", 1.0)),
        "min_rr": float(data.get("min_rr", 2.0)),
        "minimum_setup_score": float(data.get("minimum_setup_score", 7.5)),
    }


def _default_windows() -> list[dict[str, Any]]:
    return [
        {
            "window_name": "London Silver Bullet",
            "session": "london",
            "start_time": "08:00",
            "end_time": "10:00",
            "timezone": "Europe/London",
            "enabled": True,
        },
        {
            "window_name": "New York Silver Bullet",
            "session": "new_york",
            "start_time": "09:30",
            "end_time": "11:00",
            "timezone": "America/New_York",
            "enabled": True,
        },
        {
            "window_name": "London New York Overlap",
            "session": "overlap",
            "start_time": "13:00",
            "end_time": "16:00",
            "timezone": "Europe/London",
            "enabled": True,
        },
    ]


def _windows(window_config: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if window_config is None:
        return _default_windows()
    if isinstance(window_config, Mapping):
        if "silver_bullet_windows" in window_config:
            return [dict(window) for window in window_config["silver_bullet_windows"]]
        if "windows" in window_config:
            return [dict(window) for window in window_config["windows"]]
        return [dict(window_config)]
    return [dict(window) for window in window_config]


def _window_bounds(
    local_ts: datetime, start_text: str, end_text: str, tz: timezone | ZoneInfo
) -> tuple[datetime, datetime]:
    start_dt = datetime.combine(local_ts.date(), _parse_time(start_text), tzinfo=tz)
    end_dt = datetime.combine(local_ts.date(), _parse_time(end_text), tzinfo=tz)
    if end_dt < start_dt:
        end_dt += timedelta(days=1)
        if local_ts < start_dt:
            start_dt -= timedelta(days=1)
            end_dt -= timedelta(days=1)
    return start_dt, end_dt


def _parse_time(value: str) -> time:
    parts = value.split(":")
    if len(parts) < 2:
        raise ValueError(f"Invalid Silver Bullet time value: {value}")
    return time(hour=int(parts[0]), minute=int(parts[1]))


def _as_datetime(value: Any, broker_timezone: str | timezone | None) -> datetime:
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, (int, float)):
        ts = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_tz(broker_timezone or "UTC"))
    return ts


def _tz(value: str | timezone | ZoneInfo) -> timezone | ZoneInfo:
    if isinstance(value, (timezone, ZoneInfo)):
        return value
    if str(value).upper() == "UTC":
        return timezone.utc
    return ZoneInfo(str(value))


def _closed_candles(rows: Sequence[Mapping[str, Any] | Any] | Any) -> list[_Candle]:
    candles: list[_Candle] = []
    for position, row in enumerate(_records(rows)):
        is_closed = bool(_get(row, "is_closed", "closed", default=True))
        if not is_closed:
            continue
        candles.append(
            _Candle(
                position=len(candles),
                index=int(_get(row, "index", default=position)),
                timestamp=_get(row, "timestamp", "time", default=position),
                open=float(_get(row, "open", default=0.0)),
                high=float(_get(row, "high", default=0.0)),
                low=float(_get(row, "low", default=0.0)),
                close=float(_get(row, "close", default=0.0)),
                volume=float(_get(row, "volume", default=0.0)),
                is_closed=is_closed,
            )
        )
    return sorted(candles, key=lambda candle: candle.index)


def _records(rows: Sequence[Mapping[str, Any] | Any] | Any) -> list[Any]:
    if hasattr(rows, "to_dict"):
        return list(rows.to_dict("records"))  # type: ignore[call-arg, union-attr]
    return list(rows or [])


def _pool_bounds(pool: Mapping[str, Any]) -> tuple[float, float]:
    price = _float(_get(pool, "price", default=None))
    low = _float(_get(pool, "zone_low", default=price))
    high = _float(_get(pool, "zone_high", default=price))
    if low is None or high is None:
        raise ValueError("liquidity pool requires price or zone_low/zone_high")
    return min(low, high), max(low, high)


def _pool_swept(pool: Mapping[str, Any]) -> bool:
    return str(_get(pool, "swept_status", "status", default="active")).lower() in {
        "swept",
        "already_swept",
        "fully_swept",
        "cleared",
        "inactive",
    }


def _is_external_liquidity(pool: Mapping[str, Any]) -> bool:
    pool_type = str(_get(pool, "liquidity_type", "type", default="")).lower()
    timeframe = str(_get(pool, "timeframe", default="")).lower()
    return any(token in pool_type for token in ("pdh", "pdl", "previous_day", "external")) or timeframe in {
        "h1",
        "h4",
        "daily",
        "d1",
    }


def _atr(candles: Sequence[_Candle], period: int) -> float:
    if not candles:
        return 0.0
    ranges: list[float] = []
    previous_close = candles[0].close
    for candle in candles:
        ranges.append(
            max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close))
        )
        previous_close = candle.close
    selected = ranges[-period:] if len(ranges) >= period else ranges
    return mean(selected) if selected else 0.0


def _window_session(active_window: Mapping[str, Any]) -> str:
    return str(_get(active_window, "session", "window_name", "name", default="unknown")).lower()


def _session_liquidity_bonus(session: str, pool_type: str) -> float:
    if "london" in session and any(token in pool_type for token in ("asian", "asia", "previous_day", "pdh", "pdl")):
        return 2.0
    if ("new_york" in session or "ny" in session) and any(
        token in pool_type for token in ("london", "asian", "asia", "previous_day", "pdh", "pdl", "opening_range")
    ):
        return 2.0
    if "overlap" in session and any(token in pool_type for token in ("london", "previous_day", "external")):
        return 2.0
    return 1.0


def _draw_on_liquidity(htf_bias: Mapping[str, Any] | str | None) -> str:
    if isinstance(htf_bias, Mapping):
        return str(_get(htf_bias, "draw_on_liquidity", "bias", default="neutral")).lower()
    return str(htf_bias or "neutral").lower()


def _htf_liquidity_bonus(draw: str, side: str) -> float:
    if draw in {"buy_side", "bullish"} and side == "buy_side":
        return 1.5
    if draw in {"sell_side", "bearish"} and side == "sell_side":
        return 1.5
    if draw in {"neutral", "none", ""}:
        return 0.75
    return 0.25


def _time_window_score(window: Mapping[str, Any]) -> float:
    if not bool(_get(window, "in_window", default=False)):
        return 0.0
    minutes_from = _float(_get(window, "minutes_from_window_start", default=None))
    minutes_to = _float(_get(window, "minutes_to_window_end", default=None))
    if minutes_from is None or minutes_to is None:
        return 8.0
    if minutes_from <= 5 or minutes_to <= 5:
        return 7.0
    return 9.5


def _htf_score(htf_bias: Mapping[str, Any] | str | None, direction: SilverBulletDirection) -> float:
    text = (
        " ".join(str(value).lower() for value in htf_bias.values())
        if isinstance(htf_bias, Mapping)
        else str(htf_bias or "neutral").lower()
    )
    if direction.value in text:
        return 9.0
    if "neutral" in text or not text:
        return 7.0
    if ("bullish" in text and direction is SilverBulletDirection.BEARISH) or (
        "bearish" in text and direction is SilverBulletDirection.BULLISH
    ):
        return 4.0
    return 6.0


def _session_cleanliness_score(context: Mapping[str, Any]) -> float:
    session = context.get("session_context", {}) or {}
    status = str(_get(session, "liquidity_condition", "status", default="normal")).lower()
    if status in {"clean", "normal", "active"}:
        return 8.5
    if status in {"choppy", "mixed"}:
        return 5.0
    if status in {"low_liquidity_chop", "dead", "closed"}:
        return 1.0
    return 7.0


def _grade(score: float) -> str:
    if score >= 9.0:
        return "A+"
    if score >= 8.0:
        return "A"
    if score >= 7.0:
        return "B"
    if score >= 6.0:
        return "C"
    if score >= 5.0:
        return "D"
    return "F"


def _configured_buffer(cfg: Mapping[str, Any], key: str, default: float) -> float:
    value = cfg.get(key)
    return default if value in {None, ""} else float(value)


def _direction(value: Any) -> SilverBulletDirection:
    text = str(value or "").lower()
    if text in {"bullish", "buy", "long"}:
        return SilverBulletDirection.BULLISH
    if text in {"bearish", "sell", "short"}:
        return SilverBulletDirection.BEARISH
    return SilverBulletDirection.NONE


def _get(row: Mapping[str, Any] | Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if isinstance(row, Mapping) and key in row and row[key] is not None:
            return row[key]
        if not isinstance(row, Mapping) and hasattr(row, key):
            value = getattr(row, key)
            if value is not None:
                return value
    return default


def _float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
