"""Liquidity Sweep + MSS + FVG reversal-entry strategy.

The strategy is deliberately strict: no single ICT/SMC concept is enough to
approve a trade. A valid signal must pass the full sequence:

liquidity sweep -> reclaim/rejection -> MSS -> displacement -> FVG -> retest
-> stop/target/RR validation -> scoring.

All detectors use closed candles only and return plain dictionaries so the
module can be used by tests, backtests, and future live orchestration without
taking a dependency on pandas.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence


class SweepMSSFVGDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"


class SweepMSSFVGStatus(str, Enum):
    VALID = "valid"
    REJECTED = "rejected"
    WAITING_FOR_RETEST = "waiting_for_retest"
    CONTEXT_ONLY = "context_only"


class EntryMode(str, Enum):
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


def detect_liquidity_sweep(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    liquidity_pools: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Detect sell-side or buy-side sweeps that reclaim/reject on a close."""

    cfg = _config(config)
    candles = _closed_candles(df)
    atr = _atr(candles, int(cfg["atr_period"]))
    sweep_buffer = _configured_buffer(cfg, "sweep_buffer", atr * float(cfg["sweep_buffer_atr_multiplier"]))
    min_quality = float(cfg["minimum_liquidity_quality"])
    sweeps: list[dict[str, Any]] = []

    for candle in candles:
        for pool in liquidity_pools:
            if _pool_swept(pool) or float(_get(pool, "quality_score", default=5.0)) < min_quality:
                continue
            side = str(_get(pool, "direction", "side", default="")).lower()
            zone_low, zone_high = _pool_bounds(pool)
            if side == "sell_side" and candle.low < zone_low - sweep_buffer and candle.close > zone_low:
                reclaim = "reclaimed_zone_high" if candle.close > zone_high else "reclaimed_swept_level"
                depth = zone_low - candle.low
                quality = _clamp(
                    6.0 + depth / max(atr, 1e-9) + float(_get(pool, "quality_score", default=5.0)) * 0.25, 0, 10
                )
                sweeps.append(
                    {
                        "sweep_id": f"SWEEP_SELL_SIDE_{candle.index}_{_get(pool, 'liquidity_id', 'pool', default='pool')}",
                        "direction_bias": SweepMSSFVGDirection.BULLISH.value,
                        "swept_side": "sell_side",
                        "swept_liquidity_id": _get(pool, "liquidity_id", "id", default="unknown"),
                        "liquidity_type": _get(pool, "liquidity_type", "type", default="sell_side_liquidity"),
                        "sweep_index": candle.index,
                        "sweep_time": candle.timestamp,
                        "sweep_extreme": candle.low,
                        "sweep_low": candle.low,
                        "sweep_high": candle.high,
                        "swept_level": zone_low,
                        "reclaim_status": reclaim,
                        "sweep_quality_score": round(quality, 2),
                    }
                )
            if side == "buy_side" and candle.high > zone_high + sweep_buffer and candle.close < zone_high:
                reclaim = "rejected_zone_low" if candle.close < zone_low else "rejected_swept_level"
                depth = candle.high - zone_high
                quality = _clamp(
                    6.0 + depth / max(atr, 1e-9) + float(_get(pool, "quality_score", default=5.0)) * 0.25, 0, 10
                )
                sweeps.append(
                    {
                        "sweep_id": f"SWEEP_BUY_SIDE_{candle.index}_{_get(pool, 'liquidity_id', 'pool', default='pool')}",
                        "direction_bias": SweepMSSFVGDirection.BEARISH.value,
                        "swept_side": "buy_side",
                        "swept_liquidity_id": _get(pool, "liquidity_id", "id", default="unknown"),
                        "liquidity_type": _get(pool, "liquidity_type", "type", default="buy_side_liquidity"),
                        "sweep_index": candle.index,
                        "sweep_time": candle.timestamp,
                        "sweep_extreme": candle.high,
                        "sweep_low": candle.low,
                        "sweep_high": candle.high,
                        "swept_level": zone_high,
                        "reclaim_status": reclaim,
                        "sweep_quality_score": round(quality, 2),
                    }
                )

    return sorted(sweeps, key=lambda item: (item["sweep_index"], item["sweep_quality_score"]), reverse=True)


def detect_mss(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    swings: Sequence[Mapping[str, Any]] | None,
    sweep_event: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Detect a candle-close market structure shift after a sweep."""

    cfg = _config(config)
    candles = _closed_candles(df)
    direction = _direction(sweep_event.get("direction_bias"))
    if direction is SweepMSSFVGDirection.NONE:
        return None
    sweep_index = int(sweep_event["sweep_index"])
    atr = _atr(candles, int(cfg["atr_period"]))
    break_buffer = _configured_buffer(cfg, "break_buffer", atr * float(cfg["break_buffer_atr_multiplier"]))
    wait = int(cfg["max_mss_wait_candles"])
    normalized_swings = _confirmed_swings(candles, swings)
    wanted_kind = "high" if direction is SweepMSSFVGDirection.BULLISH else "low"
    candidates = [
        swing
        for swing in normalized_swings
        if swing["kind"] == wanted_kind and sweep_index < int(swing["index"]) <= sweep_index + wait
    ]
    candidates = sorted(candidates, key=lambda item: int(item["index"]))

    for swing in candidates:
        swing_index = int(swing["index"])
        limit_index = sweep_index + wait
        for candle in candles:
            if candle.index <= swing_index or candle.index > limit_index:
                continue
            if direction is SweepMSSFVGDirection.BULLISH and candle.close > float(swing["price"]) + break_buffer:
                return _mss_event(direction, swing, candle, break_buffer)
            if direction is SweepMSSFVGDirection.BEARISH and candle.close < float(swing["price"]) - break_buffer:
                return _mss_event(direction, swing, candle, break_buffer)
    return None


def detect_displacement(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    start_index: int,
    direction: str,
    atr: float | Sequence[float] | Mapping[int, float] | None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Detect directional expansion after MSS using body/range/ATR rules."""

    cfg = _config(config)
    candles = _closed_candles(df)
    expected = _direction(direction)
    if expected is SweepMSSFVGDirection.NONE:
        return None
    atr_value = _atr_from_input(atr, start_index, candles, cfg)
    body_floor = float(cfg["displacement_min_body_to_range"])
    atr_floor = float(cfg["displacement_min_range_to_atr"])
    close_floor = float(cfg["displacement_min_close_position"])
    max_scan = int(cfg["max_displacement_wait_candles"])

    for candle in candles:
        if candle.index < int(start_index) or candle.index > int(start_index) + max_scan:
            continue
        body_to_range = candle.body / candle.range
        range_to_atr = candle.range / max(atr_value, 1e-9)
        close_position = (
            candle.bullish_close_position if expected is SweepMSSFVGDirection.BULLISH else candle.bearish_close_position
        )
        directional = candle.bullish if expected is SweepMSSFVGDirection.BULLISH else candle.bearish
        if directional and body_to_range >= body_floor and range_to_atr >= atr_floor and close_position >= close_floor:
            strength = _clamp(
                body_to_range * 3.0 + min(range_to_atr, 3.0) * 1.5 + close_position * 2.0,
                0,
                10,
            )
            return {
                "displacement_confirmed": True,
                "direction": expected.value,
                "start_index": int(start_index),
                "end_index": candle.index,
                "body_to_range_ratio": round(body_to_range, 3),
                "range_to_atr_ratio": round(range_to_atr, 3),
                "close_position_score": round(close_position, 3),
                "strength_score": round(strength, 2),
                "fvg_created": _fvg_near_displacement(candles, candle.index, expected),
            }
    return None


def detect_fvg(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Detect bullish and bearish three-candle fair value gaps."""

    cfg = _config(config)
    candles = _closed_candles(df)
    if len(candles) < 3:
        return []
    min_size = float(cfg["min_fvg_size"])
    max_size = float(cfg["max_fvg_size"])
    fvgs: list[dict[str, Any]] = []

    for pos in range(2, len(candles)):
        c1 = candles[pos - 2]
        c2 = candles[pos - 1]
        c3 = candles[pos]
        if c1.high < c3.low:
            fvgs.append(_fvg_event(SweepMSSFVGDirection.BULLISH, c1, c2, c3, c1.high, c3.low, min_size, max_size))
        if c1.low > c3.high:
            fvgs.append(_fvg_event(SweepMSSFVGDirection.BEARISH, c1, c2, c3, c3.high, c1.low, min_size, max_size))

    return [fvg for fvg in fvgs if fvg["fvg_size"] >= min_size and (max_size <= 0 or fvg["fvg_size"] <= max_size)]


def detect_fvg_retest(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    fvg: Mapping[str, Any],
    direction: str,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Detect a future candle retracing into the selected FVG zone."""

    cfg = _config(config)
    candles = _closed_candles(df)
    expected = _direction(direction)
    if expected is SweepMSSFVGDirection.NONE:
        return None
    expiry = int(cfg["fvg_retest_expiry_candles"])
    entry_mode = EntryMode(str(cfg["entry_mode"]).lower())
    creation_index = int(fvg["creation_index"])
    zone_low = float(fvg["zone_low"])
    zone_high = float(fvg["zone_high"])
    zone_mid = float(fvg["zone_mid"])

    for candle in candles:
        if candle.index <= creation_index:
            continue
        if candle.index > creation_index + expiry:
            return None
        touches = candle.low <= zone_high and candle.high >= zone_low
        if not touches:
            continue
        if entry_mode is EntryMode.CONSERVATIVE:
            if expected is SweepMSSFVGDirection.BULLISH and not (candle.bullish and candle.close > zone_mid):
                return None
            if expected is SweepMSSFVGDirection.BEARISH and not (candle.bearish and candle.close < zone_mid):
                return None
            entry_price = candle.close
            entry_type = f"{expected.value}_fvg_confirmation_entry"
        else:
            entry_price = zone_mid
            entry_type = f"{expected.value}_fvg_entry"
        return {
            "retest_detected": True,
            "direction": expected.value,
            "retest_index": candle.index,
            "retest_time": candle.timestamp,
            "entry_triggered": True,
            "entry_type": entry_type,
            "entry_price": round(entry_price, 5),
            "retest_status": "price_retraced_into_fvg",
            "confirmation_mode": entry_mode.value,
        }
    return None


def generate_sweep_mss_fvg_signal(
    context: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine all strategy steps into one backtest-safe signal decision."""

    cfg = _config(config)
    setup_df = context.get("m15_df", context.get("df", context.get("candles", [])))
    entry_df = context.get("m5_df", setup_df)
    candles = _closed_candles(setup_df)
    if len(candles) < 3:
        return _no_trade(context, SweepMSSFVGStatus.REJECTED, ["insufficient_closed_candles"])

    hard_filter_reasons = _xauusd_filter_reasons(context, cfg)
    fvg_list = detect_fvg(setup_df, cfg)
    if hard_filter_reasons:
        if _has_oversized_fvg(fvg_list, cfg):
            hard_filter_reasons.append("fvg_too_large_news_spike")
        return _no_trade(context, SweepMSSFVGStatus.REJECTED, hard_filter_reasons)

    sweeps = detect_liquidity_sweep(setup_df, context.get("liquidity_pools", []), cfg)
    if not sweeps:
        return _no_trade(context, SweepMSSFVGStatus.REJECTED, ["missing_required_liquidity_sweep"])

    rejected: list[str] = []
    atr_value = _atr(candles, int(cfg["atr_period"]))
    for sweep in sweeps:
        direction = _direction(sweep["direction_bias"])
        mss = detect_mss(setup_df, context.get("swings", []), sweep, cfg)
        if not mss:
            rejected.append("no_mss_after_sweep")
            continue
        displacement = detect_displacement(setup_df, int(mss["confirmation_index"]), direction.value, atr_value, cfg)
        if not displacement:
            rejected.append("no_displacement_after_mss")
            continue
        fvg = _select_fvg_after_mss(
            fvg_list, direction, int(mss["confirmation_index"]), int(displacement["end_index"]), cfg
        )
        if not fvg:
            rejected.append("no_valid_fvg_after_displacement")
            continue
        retest = detect_fvg_retest(entry_df, fvg, direction.value, cfg)
        if not retest:
            return _no_trade(
                context,
                SweepMSSFVGStatus.WAITING_FOR_RETEST,
                ["waiting_for_fvg_retest"],
                sweep=sweep,
                mss=mss,
                displacement=displacement,
                fvg=fvg,
            )
        risk_plan = _risk_plan(sweep, retest, context.get("liquidity_pools", []), context, direction, atr_value, cfg)
        if risk_plan["target"] is None:
            rejected.append("no_valid_target_liquidity")
            continue
        if risk_plan["rr"] < float(cfg["min_rr"]):
            rejected.append("rr_below_minimum")
            continue
        setup = {
            "direction": direction.value,
            "liquidity_sweep": sweep,
            "mss": mss,
            "displacement": displacement,
            "fvg": fvg,
            "entry": retest,
            "risk": risk_plan,
        }
        score = score_sweep_mss_fvg_setup(setup, context, cfg)
        if not score["trade_allowed"]:
            rejected.append("confirmation_score_below_minimum_threshold")
            continue
        return {
            "strategy": "Liquidity Sweep + MSS + FVG Entry",
            "symbol": str(context.get("symbol", "XAUUSD")),
            "signal_id": f"{str(context.get('symbol', 'XAUUSD'))}_SWEEP_MSS_FVG_{direction.value.upper()}_{retest['retest_index']}",
            "signal_status": SweepMSSFVGStatus.VALID.value,
            "trade_allowed": True,
            "direction": direction.value,
            "setup_time": sweep["sweep_time"],
            "entry_time": retest["retest_time"],
            "liquidity_sweep": sweep,
            "mss": mss,
            "displacement": displacement,
            "fvg": fvg,
            "entry": retest,
            "risk": risk_plan,
            "score": score,
            "filters": _passed_filters(context),
            "rejection_reasons": [],
            "warnings": [
                "Backtest fills must be candle-forward only.",
                "If stop and target hit in the same candle, assume stop first unless lower-timeframe data resolves it.",
            ],
        }

    return _no_trade(context, SweepMSSFVGStatus.REJECTED, rejected or ["no_complete_sweep_mss_fvg_sequence"])


def score_sweep_mss_fvg_setup(
    setup: Mapping[str, Any],
    context: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score the full setup from 0 to 10 after the sequence is complete."""

    cfg = _config(config)
    direction = _direction(setup.get("direction"))
    htf_bias = str(context.get("htf_bias", context.get("higher_timeframe_bias", "neutral"))).lower()
    session = str(_get(context.get("session_context", {}) or {}, "session", "session_name", default="unknown")).lower()
    rr = float(_get(setup.get("risk", {}) or {}, "rr", default=0.0))
    fvg = setup.get("fvg", {}) or {}
    sweep = setup.get("liquidity_sweep", {}) or {}
    mss = setup.get("mss", {}) or {}
    displacement = setup.get("displacement", {}) or {}
    retest = setup.get("entry", {}) or {}

    component_scores = {
        "liquidity_sweep": min(10.0, float(_get(sweep, "sweep_quality_score", default=0.0))),
        "mss": min(10.0, float(_get(mss, "structure_quality_score", default=0.0))),
        "displacement": min(10.0, float(_get(displacement, "strength_score", default=0.0))),
        "fvg_quality": min(10.0, float(_get(fvg, "quality_score", default=0.0))),
        "entry_retest": 8.5 if bool(_get(retest, "entry_triggered", default=False)) else 0.0,
        "session_timing": _session_score(session),
        "xauusd_safety": 9.0 if not _xauusd_filter_reasons(context, cfg) else 1.0,
        "rr_target": min(10.0, rr / max(float(cfg["min_rr"]), 1e-9) * 8.0),
        "htf_alignment": _htf_score(htf_bias, direction),
    }
    weights = {
        "liquidity_sweep": 0.13,
        "mss": 0.13,
        "displacement": 0.13,
        "fvg_quality": 0.10,
        "entry_retest": 0.10,
        "session_timing": 0.08,
        "xauusd_safety": 0.10,
        "rr_target": 0.15,
        "htf_alignment": 0.08,
    }
    total = round(sum(component_scores[key] * weights[key] for key in weights), 2)
    min_score = float(cfg["minimum_setup_score"])
    return {
        "total_score": total,
        "grade": "A" if total >= 8.5 else "B" if total >= 7.0 else "C" if total >= min_score else "REJECT",
        "trade_allowed": total >= min_score,
        "component_scores": {key: round(value, 2) for key, value in component_scores.items()},
        "minimum_required": min_score,
    }


def _mss_event(
    direction: SweepMSSFVGDirection, swing: Mapping[str, Any], candle: _Candle, break_buffer: float
) -> dict[str, Any]:
    broken_kind = "post_sweep_swing_high" if direction is SweepMSSFVGDirection.BULLISH else "post_sweep_swing_low"
    confirmation = (
        "candle_close_above_post_sweep_swing_high"
        if direction is SweepMSSFVGDirection.BULLISH
        else "candle_close_below_post_sweep_swing_low"
    )
    quality = _clamp(7.0 + abs(candle.close - float(swing["price"])) / max(break_buffer, 0.01) * 0.15, 0, 10)
    return {
        "mss_confirmed": True,
        "direction": direction.value,
        "broken_level": float(swing["price"]),
        "broken_swing_id": str(swing["swing_id"]),
        "broken_kind": broken_kind,
        "confirmation_index": candle.index,
        "confirmation_time": candle.timestamp,
        "confirmation_type": confirmation,
        "structure_quality_score": round(quality, 2),
    }


def _fvg_event(
    direction: SweepMSSFVGDirection,
    candle_1: _Candle,
    candle_2: _Candle,
    candle_3: _Candle,
    zone_low: float,
    zone_high: float,
    min_size: float,
    max_size: float,
) -> dict[str, Any]:
    fvg_size = zone_high - zone_low
    body_ratio = candle_2.body / candle_2.range
    quality = 6.0 + min(body_ratio * 3.0, 2.0)
    if fvg_size >= min_size:
        quality += 0.75
    if max_size > 0 and fvg_size > max_size:
        quality -= 1.5
    return {
        "fvg_id": f"{direction.value.upper()}_FVG_{candle_3.index}",
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
        "quality_score": round(_clamp(quality, 0, 10), 2),
    }


def _risk_plan(
    sweep: Mapping[str, Any],
    entry: Mapping[str, Any],
    liquidity_pools: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    direction: SweepMSSFVGDirection,
    atr: float,
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    entry_price = float(entry["entry_price"])
    spread_buffer = float(_get(context.get("spread_status", {}) or {}, "spread_points", "spread", default=0.0))
    spread_buffer *= float(cfg["spread_buffer_multiplier"])
    atr_buffer = atr * float(cfg["stop_atr_buffer_multiplier"])
    if direction is SweepMSSFVGDirection.BULLISH:
        stop = float(sweep["sweep_low"]) - atr_buffer - spread_buffer
    else:
        stop = float(sweep["sweep_high"]) + atr_buffer + spread_buffer
    target = _select_target(entry_price, stop, liquidity_pools, context, direction, float(cfg["min_rr"]))
    if target is None:
        rr = 0.0
    elif direction is SweepMSSFVGDirection.BULLISH:
        rr = (target - entry_price) / max(entry_price - stop, 1e-9)
    else:
        rr = (entry_price - target) / max(stop - entry_price, 1e-9)
    return {
        "stop_loss": round(stop, 5),
        "stop_reference": (
            "below_sweep_low_with_atr_and_spread_buffer"
            if direction is SweepMSSFVGDirection.BULLISH
            else "above_sweep_high_with_atr_and_spread_buffer"
        ),
        "target": round(target, 5) if target is not None else None,
        "target_reference": (
            "opposite_buy_side_liquidity"
            if direction is SweepMSSFVGDirection.BULLISH
            else "opposite_sell_side_liquidity"
        ),
        "rr": round(rr, 2),
        "min_rr_required": float(cfg["min_rr"]),
    }


def _select_target(
    entry: float,
    stop: float,
    pools: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    direction: SweepMSSFVGDirection,
    min_rr: float,
) -> float | None:
    target_context = context.get("target_liquidity")
    if isinstance(target_context, Mapping):
        price = _float(_get(target_context, "price", "target_price", "zone_mid", default=None))
        if price is not None:
            return price
    side = "buy_side" if direction is SweepMSSFVGDirection.BULLISH else "sell_side"
    candidates: list[float] = []
    for pool in pools:
        if str(_get(pool, "direction", "side", default="")).lower() != side or _pool_swept(pool):
            continue
        low, high = _pool_bounds(pool)
        price = high if direction is SweepMSSFVGDirection.BULLISH else low
        if direction is SweepMSSFVGDirection.BULLISH and price > entry:
            candidates.append(price)
        if direction is SweepMSSFVGDirection.BEARISH and price < entry:
            candidates.append(price)
    candidates = sorted(candidates, reverse=direction is SweepMSSFVGDirection.BEARISH)
    for price in candidates:
        risk = abs(entry - stop)
        reward = abs(price - entry)
        if risk > 0 and reward / risk >= min_rr:
            return price
    return None


def _select_fvg_after_mss(
    fvgs: Sequence[Mapping[str, Any]],
    direction: SweepMSSFVGDirection,
    mss_index: int,
    displacement_index: int,
    cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    max_after = int(cfg["max_fvg_after_displacement_candles"])
    candidates = [
        fvg
        for fvg in fvgs
        if fvg["direction"] == direction.value
        and int(fvg["creation_index"]) >= mss_index
        and int(fvg["creation_index"]) <= displacement_index + max_after
        and bool(fvg.get("active_status", True))
    ]
    if not candidates:
        return None
    displacement_created = [fvg for fvg in candidates if int(fvg["creation_index"]) <= displacement_index]
    preferred = displacement_created or candidates
    return sorted(
        preferred,
        key=lambda item: (
            abs(int(item["creation_index"]) - displacement_index),
            -float(item.get("quality_score", 0.0)),
        ),
    )[0]


def _xauusd_filter_reasons(context: Mapping[str, Any], cfg: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    news = context.get("news_status", {}) or {}
    spread = context.get("spread_status", {}) or {}
    session = context.get("session_context", {}) or {}
    if bool(_get(news, "restricted", "news_restricted", default=False)):
        reasons.append("news_restricted")
    if bool(_get(news, "first_news_spike", default=False)):
        reasons.append("first_news_spike_signal")
    spread_value = _float(_get(spread, "spread_points", "spread", default=None))
    if bool(_get(spread, "spread_safe", default=True)) is False or str(
        _get(spread, "status", default="normal")
    ).lower() in {
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
    return _dedupe(reasons)


def _has_oversized_fvg(fvgs: Sequence[Mapping[str, Any]], cfg: Mapping[str, Any]) -> bool:
    limit = float(cfg["news_max_fvg_size"])
    return any(float(fvg.get("fvg_size", 0.0)) > limit for fvg in fvgs)


def _no_trade(
    context: Mapping[str, Any],
    status: SweepMSSFVGStatus,
    reasons: Sequence[str],
    **payload: Any,
) -> dict[str, Any]:
    return {
        "strategy": "Liquidity Sweep + MSS + FVG Entry",
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
        "session_filter": "passed",
        "htf_bias_filter": str(context.get("htf_bias", "neutral")),
        "fvg_size_filter": "passed",
        "chop_filter": "passed",
    }


def _config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    data = dict(config or {})
    return {
        "atr_period": int(data.get("atr_period", 14)),
        "sweep_buffer": data.get("sweep_buffer"),
        "sweep_buffer_atr_multiplier": float(data.get("sweep_buffer_atr_multiplier", 0.02)),
        "break_buffer": data.get("break_buffer"),
        "break_buffer_atr_multiplier": float(data.get("break_buffer_atr_multiplier", 0.01)),
        "minimum_liquidity_quality": float(data.get("minimum_liquidity_quality", 0.0)),
        "max_mss_wait_candles": int(data.get("max_mss_wait_candles", 12)),
        "displacement_min_body_to_range": float(data.get("displacement_min_body_to_range", 0.55)),
        "displacement_min_range_to_atr": float(data.get("displacement_min_range_to_atr", 1.0)),
        "displacement_min_close_position": float(data.get("displacement_min_close_position", 0.70)),
        "max_displacement_wait_candles": int(data.get("max_displacement_wait_candles", 4)),
        "min_fvg_size": float(data.get("min_fvg_size", 0.01)),
        "max_fvg_size": float(data.get("max_fvg_size", 0.0)),
        "news_max_fvg_size": float(data.get("news_max_fvg_size", 8.0)),
        "fvg_retest_expiry_candles": int(data.get("fvg_retest_expiry_candles", 12)),
        "entry_mode": str(data.get("entry_mode", EntryMode.BALANCED.value)).lower(),
        "max_fvg_after_displacement_candles": int(data.get("max_fvg_after_displacement_candles", 2)),
        "stop_atr_buffer_multiplier": float(data.get("stop_atr_buffer_multiplier", 0.05)),
        "spread_buffer_multiplier": float(data.get("spread_buffer_multiplier", 1.0)),
        "max_spread_points": float(data.get("max_spread_points", 1.0)),
        "min_rr": float(data.get("min_rr", 2.0)),
        "minimum_setup_score": float(data.get("minimum_setup_score", 7.5)),
    }


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


def _confirmed_swings(candles: Sequence[_Candle], swings: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if swings:
        normalized = []
        for swing in swings:
            kind = str(_get(swing, "kind", "type", "swing_type", default="")).lower()
            if "high" in kind:
                kind = "high"
            elif "low" in kind:
                kind = "low"
            else:
                continue
            if bool(_get(swing, "confirmed", default=True)) is False:
                continue
            normalized.append(
                {
                    "swing_id": str(
                        _get(swing, "swing_id", "id", default=f"SWING_{kind}_{_get(swing, 'index', default=0)}")
                    ),
                    "kind": kind,
                    "index": int(_get(swing, "index", "candle_index", default=0)),
                    "price": float(_get(swing, "price", "level", default=0.0)),
                }
            )
        return normalized

    derived: list[dict[str, Any]] = []
    for pos in range(1, len(candles) - 1):
        previous, current, following = candles[pos - 1], candles[pos], candles[pos + 1]
        if current.high > previous.high and current.high > following.high:
            derived.append(
                {
                    "swing_id": f"SWING_HIGH_{current.index}",
                    "kind": "high",
                    "index": current.index,
                    "price": current.high,
                }
            )
        if current.low < previous.low and current.low < following.low:
            derived.append(
                {"swing_id": f"SWING_LOW_{current.index}", "kind": "low", "index": current.index, "price": current.low}
            )
    return derived


def _fvg_near_displacement(candles: Sequence[_Candle], index: int, direction: SweepMSSFVGDirection) -> bool:
    by_index = {candle.index: candle for candle in candles}
    c1 = by_index.get(index - 2)
    c3 = by_index.get(index)
    if not c1 or not c3:
        return False
    if direction is SweepMSSFVGDirection.BULLISH:
        return c1.high < c3.low
    return c1.low > c3.high


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


def _atr_from_input(
    atr: float | Sequence[float] | Mapping[int, float] | None,
    start_index: int,
    candles: Sequence[_Candle],
    cfg: Mapping[str, Any],
) -> float:
    if isinstance(atr, Mapping):
        return float(atr.get(start_index, _atr(candles, int(cfg["atr_period"]))))
    if isinstance(atr, Sequence) and not isinstance(atr, (str, bytes)):
        return float(atr[min(len(atr) - 1, max(0, start_index))]) if atr else _atr(candles, int(cfg["atr_period"]))
    if atr is not None:
        return float(atr)
    return _atr(candles, int(cfg["atr_period"]))


def _configured_buffer(cfg: Mapping[str, Any], key: str, default: float) -> float:
    value = cfg.get(key)
    return default if value in {None, ""} else float(value)


def _direction(value: Any) -> SweepMSSFVGDirection:
    text = str(value or "").lower()
    if text in {"bullish", "buy", "long"}:
        return SweepMSSFVGDirection.BULLISH
    if text in {"bearish", "sell", "short"}:
        return SweepMSSFVGDirection.BEARISH
    return SweepMSSFVGDirection.NONE


def _session_score(session: str) -> float:
    if any(name in session for name in ("london", "newyork", "new_york", "ny")):
        return 9.0
    if "asia" in session or "asian" in session:
        return 7.0
    if session in {"unknown", "neutral"}:
        return 6.5
    return 5.0


def _htf_score(htf_bias: str, direction: SweepMSSFVGDirection) -> float:
    if direction.value in htf_bias:
        return 9.0
    if "neutral" in htf_bias or not htf_bias:
        return 7.0
    if ("bullish" in htf_bias and direction is SweepMSSFVGDirection.BEARISH) or (
        "bearish" in htf_bias and direction is SweepMSSFVGDirection.BULLISH
    ):
        return 4.0
    return 6.0


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
