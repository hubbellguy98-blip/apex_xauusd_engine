"""Judas Swing / Session Manipulation strategy model.

Judas Swing is treated as a full sequence, not a single wick sweep:

completed session range -> clean range -> one-side sweep -> reclaim/rejection
-> MSS -> displacement -> FVG retracement -> stop/target/RR -> scoring.

The module is intentionally pure Python. It uses closed candles only and returns
plain dictionaries for tests, backtests, and later orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


class JudasDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"


class JudasStatus(str, Enum):
    VALID = "valid"
    REJECTED = "rejected"
    WAITING_FOR_RETEST = "waiting_for_retest"
    RANGE_NOT_READY = "range_not_ready"


class JudasEntryMode(str, Enum):
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
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def bullish_close_position(self) -> float:
        return (self.close - self.low) / self.range

    @property
    def bearish_close_position(self) -> float:
        return (self.high - self.close) / self.range


def calculate_session_range(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    session_config: Mapping[str, Any] | None = None,
    broker_timezone: str | timezone | None = "UTC",
    evaluation_time: Any | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate a completed Asian/London/NY range from closed candles."""

    cfg = _config(config)
    session = _session_config(session_config, cfg)
    candles = _closed_candles(df)
    if not candles:
        return _empty_range(session, ["insufficient_closed_candles"])

    session_tz = _tz(session.get("timezone", broker_timezone or "UTC"))
    reference = _as_datetime(evaluation_time or candles[-1].timestamp, broker_timezone).astimezone(session_tz)
    start_dt, end_dt = _window_bounds(reference, str(session["start_time"]), str(session["end_time"]), session_tz)
    if reference < end_dt:
        start_dt -= timedelta(days=1)
        end_dt -= timedelta(days=1)

    in_range = [
        candle
        for candle in candles
        if start_dt <= _as_datetime(candle.timestamp, broker_timezone).astimezone(session_tz) < end_dt
    ]
    if not in_range:
        return _empty_range(session, ["no_closed_candles_in_session_range"], start_dt, end_dt)

    range_high_candle = max(in_range, key=lambda candle: candle.high)
    range_low_candle = min(in_range, key=lambda candle: candle.low)
    range_high = range_high_candle.high
    range_low = range_low_candle.low
    range_obj = {
        "session_name": str(session.get("session_name", "Asian")),
        "range_type": str(session.get("range_type", "session_range")),
        "range_start": start_dt.isoformat(),
        "range_end": end_dt.isoformat(),
        "range_high": round(range_high, 5),
        "range_low": round(range_low, 5),
        "range_mid": round((range_high + range_low) / 2.0, 5),
        "range_size": round(range_high - range_low, 5),
        "high_time": range_high_candle.timestamp,
        "low_time": range_low_candle.timestamp,
        "high_index": range_high_candle.index,
        "low_index": range_low_candle.index,
        "candle_count": len(in_range),
        "valid_status": "completed" if reference >= end_dt else "developing",
        "range_candle_indices": [candle.index for candle in in_range],
    }
    quality = score_session_range_quality(range_obj, df, config=cfg)
    range_obj.update(
        {
            "quality_score": quality["quality_score"],
            "clean_range": quality["clean_range"],
            "quality_rejection_reasons": quality["rejection_reasons"],
        }
    )
    if len(in_range) < int(session.get("min_candles_required", cfg["min_candles_required"])):
        range_obj["valid_status"] = "invalid"
    return range_obj


def score_session_range_quality(
    session_range: Mapping[str, Any],
    df: Sequence[Mapping[str, Any] | Any] | Any,
    atr: float | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score whether a completed range is usable for Judas manipulation."""

    cfg = _config(config)
    candles = _closed_candles(df)
    indices = set(session_range.get("range_candle_indices") or [])
    range_candles = [candle for candle in candles if candle.index in indices] if indices else candles
    range_size = float(_get(session_range, "range_size", default=0.0))
    candle_count = int(_get(session_range, "candle_count", default=len(range_candles)))
    atr_value = float(atr if atr is not None else _atr(candles, int(cfg["atr_period"])))
    atr_value = max(atr_value, 1e-9)
    range_to_atr = range_size / atr_value

    dominant_spike_ratio = 0.0
    wick_ratio_average = 0.0
    trend_ratio = 0.0
    if range_candles:
        dominant_spike_ratio = max(candle.range for candle in range_candles) / max(range_size, 1e-9)
        wick_ratio_average = mean((candle.upper_wick + candle.lower_wick) / candle.range for candle in range_candles)
        start_close = range_candles[0].close
        end_close = range_candles[-1].close
        trend_ratio = abs(end_close - start_close) / max(range_size, 1e-9)

    too_few = candle_count < int(cfg["min_candles_required"])
    too_narrow = range_to_atr < float(cfg["min_range_atr_multiplier"])
    too_wide = range_to_atr > float(cfg["max_range_atr_multiplier"])
    too_messy = (
        wick_ratio_average > float(cfg["max_average_wick_ratio"])
        or dominant_spike_ratio > float(cfg["max_dominant_spike_ratio"])
        or trend_ratio > float(cfg["max_internal_trend_ratio"])
    )
    reasons: list[str] = []
    if too_few:
        reasons.append("insufficient_session_candles")
    if too_narrow:
        reasons.append("asian_range_too_narrow")
    if too_wide:
        reasons.append("asian_range_too_wide")
    if too_messy:
        reasons.append("asian_range_too_messy")

    score = 10.0
    if too_few:
        score -= 3.0
    if too_narrow:
        score -= 1.8
    if too_wide:
        score -= 2.3
    score -= max(0.0, wick_ratio_average - 0.35) * 5.0
    score -= max(0.0, dominant_spike_ratio - 0.45) * 5.0
    score -= max(0.0, trend_ratio - 0.55) * 3.0
    score = round(_clamp(score, 0, 10), 2)

    return {
        "quality_score": score,
        "clean_range": score >= float(cfg["minimum_range_quality_score"]) and not reasons,
        "too_wide": too_wide,
        "too_messy": too_messy,
        "too_narrow": too_narrow,
        "range_to_atr": round(range_to_atr, 3),
        "dominant_spike_ratio": round(dominant_spike_ratio, 3),
        "wick_ratio_average": round(wick_ratio_average, 3),
        "internal_trend_ratio": round(trend_ratio, 3),
        "rejection_reasons": _dedupe(reasons),
    }


def detect_judas_sweep(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    session_range: Mapping[str, Any],
    manipulation_window: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Detect a one-sided range sweep after the range is completed."""

    cfg = _config(config)
    candles = _closed_candles(df)
    if not candles or str(session_range.get("valid_status", "completed")) not in {"completed", "valid"}:
        return None
    atr = _atr(candles, int(cfg["atr_period"]))
    sweep_buffer = _configured_buffer(cfg, "sweep_buffer", atr * float(cfg["sweep_buffer_atr_multiplier"]))
    min_depth = max(float(cfg["min_sweep_depth"]), sweep_buffer)
    max_depth = atr * float(cfg["max_sweep_atr_multiplier"])
    range_high = float(session_range["range_high"])
    range_low = float(session_range["range_low"])
    range_end = _as_datetime(session_range["range_end"], cfg["broker_timezone"])
    candidates: list[dict[str, Any]] = []

    for candle in candles:
        candle_time = _as_datetime(candle.timestamp, cfg["broker_timezone"])
        if candle_time <= range_end:
            continue
        if manipulation_window and not _inside_any_window(candle_time, manipulation_window, cfg):
            continue
        low_swept = candle.low < range_low - sweep_buffer
        high_swept = candle.high > range_high + sweep_buffer
        if low_swept and high_swept:
            candidates.append(_double_sweep_event(candle, session_range, range_low, range_high))
            continue
        if low_swept:
            depth = range_low - candle.low
            if depth >= min_depth:
                candidates.append(
                    _sweep_event(JudasDirection.BULLISH, candle, session_range, "range_low", depth, max_depth)
                )
        if high_swept:
            depth = candle.high - range_high
            if depth >= min_depth:
                candidates.append(
                    _sweep_event(JudasDirection.BEARISH, candle, session_range, "range_high", depth, max_depth)
                )
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item["sweep_index"], -item["sweep_quality_score"]))[0]


def detect_range_reclaim(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    sweep_event: Mapping[str, Any] | None,
    session_range: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Confirm price reclaimed/rejected back inside the range after the sweep."""

    if sweep_event is None:
        return None
    cfg = _config(config)
    direction = _direction(sweep_event.get("direction_bias"))
    if direction is JudasDirection.NONE:
        return None
    candles = _closed_candles(df)
    sweep_index = int(sweep_event["sweep_index"])
    wait = int(cfg["max_reclaim_wait_candles"])
    range_low = float(session_range["range_low"])
    range_high = float(session_range["range_high"])
    range_mid = float(session_range["range_mid"])
    continuation_closes = 0

    for candle in candles:
        if candle.index <= sweep_index or candle.index > sweep_index + wait:
            continue
        if direction is JudasDirection.BULLISH:
            if candle.close > range_low:
                strength = 7.0 + (2.0 if candle.close > range_mid else 0.5) + min(candle.body / candle.range, 1.0)
                return {
                    "direction": direction.value,
                    "reclaim_confirmed": True,
                    "reclaim_index": candle.index,
                    "reclaim_time": candle.timestamp,
                    "reclaimed_level": range_low,
                    "reclaim_status": "reclaimed_inside_range",
                    "inside_upper_half": candle.close > range_mid,
                    "reclaim_strength_score": round(_clamp(strength, 0, 10), 2),
                }
            if candle.close < range_low:
                continuation_closes += 1
        if direction is JudasDirection.BEARISH:
            if candle.close < range_high:
                strength = 7.0 + (2.0 if candle.close < range_mid else 0.5) + min(candle.body / candle.range, 1.0)
                return {
                    "direction": direction.value,
                    "reclaim_confirmed": True,
                    "reclaim_index": candle.index,
                    "reclaim_time": candle.timestamp,
                    "rejected_level": range_high,
                    "reclaim_status": "rejected_back_inside_range",
                    "inside_lower_half": candle.close < range_mid,
                    "reclaim_strength_score": round(_clamp(strength, 0, 10), 2),
                }
            if candle.close > range_high:
                continuation_closes += 1
    if continuation_closes:
        return {
            "direction": direction.value,
            "reclaim_confirmed": False,
            "rejection_reason": (
                "real_breakdown_not_manipulation"
                if direction is JudasDirection.BULLISH
                else "real_breakout_not_manipulation"
            ),
        }
    return None


def detect_judas_mss(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    swings: Sequence[Mapping[str, Any]] | None,
    reclaim_event: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Detect a candle-close MSS after reclaim/rejection."""

    if reclaim_event is None or not bool(reclaim_event.get("reclaim_confirmed", False)):
        return None
    cfg = _config(config)
    direction = _direction(reclaim_event.get("direction"))
    candles = _closed_candles(df)
    reclaim_index = int(reclaim_event["reclaim_index"])
    atr = _atr(candles, int(cfg["atr_period"]))
    break_buffer = _configured_buffer(cfg, "break_buffer", atr * float(cfg["break_buffer_atr_multiplier"]))
    wait = int(cfg["max_mss_wait_candles"])
    wanted_kind = "high" if direction is JudasDirection.BULLISH else "low"
    candidates = [
        swing
        for swing in _confirmed_swings(candles, swings)
        if swing["kind"] == wanted_kind and reclaim_index <= int(swing["index"]) <= reclaim_index + wait
    ]
    candidates = sorted(candidates, key=lambda item: int(item["index"]))

    for swing in candidates:
        for candle in candles:
            if candle.index <= int(swing["index"]) or candle.index > reclaim_index + wait:
                continue
            if direction is JudasDirection.BULLISH and candle.close > float(swing["price"]) + break_buffer:
                return _mss_event(direction, swing, candle, break_buffer)
            if direction is JudasDirection.BEARISH and candle.close < float(swing["price"]) - break_buffer:
                return _mss_event(direction, swing, candle, break_buffer)
    return None


def generate_judas_swing_signal(
    context: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a complete Judas Swing signal or a deterministic no-trade reason."""

    cfg = _config(config)
    setup_df = context.get("m15_df", context.get("df", context.get("candles", [])))
    entry_df = context.get("m5_df", setup_df)
    candles = _closed_candles(setup_df)
    if len(candles) < 3:
        return _no_trade(context, JudasStatus.REJECTED, ["insufficient_closed_candles"])

    safety_reasons = _safety_filter_reasons(context, cfg)
    if safety_reasons:
        return _no_trade(context, JudasStatus.REJECTED, safety_reasons)

    session_range = context.get("session_range") or calculate_session_range(
        setup_df,
        context.get("session_range_config"),
        cfg["broker_timezone"],
        context.get("timestamp", candles[-1].timestamp),
        cfg,
    )
    if str(session_range.get("valid_status")) not in {"completed", "valid"}:
        return _no_trade(
            context, JudasStatus.RANGE_NOT_READY, ["session_range_not_completed"], session_range=session_range
        )

    range_quality = score_session_range_quality(session_range, setup_df, config=cfg)
    if not range_quality["clean_range"]:
        return _no_trade(
            context,
            JudasStatus.REJECTED,
            ["poor_session_range_quality", *range_quality["rejection_reasons"]],
            session_range=session_range,
            range_quality=range_quality,
        )

    sweep = detect_judas_sweep(setup_df, session_range, context.get("manipulation_window"), cfg)
    if sweep is None:
        return _no_trade(context, JudasStatus.REJECTED, ["no_judas_sweep"], session_range=session_range)
    if bool(sweep.get("double_sweep_chop", False)):
        return _no_trade(context, JudasStatus.REJECTED, ["double_sweep_chop"], session_range=session_range, sweep=sweep)
    if bool(sweep.get("likely_news_or_breakout", False)):
        return _no_trade(context, JudasStatus.REJECTED, ["sweep_too_large_likely_news_or_breakout"], sweep=sweep)

    reclaim = detect_range_reclaim(setup_df, sweep, session_range, cfg)
    if reclaim is None:
        reason = "no_range_reclaim_or_real_breakout"
        return _no_trade(context, JudasStatus.REJECTED, [reason], session_range=session_range, sweep=sweep)
    if not bool(reclaim.get("reclaim_confirmed", False)):
        return _no_trade(
            context, JudasStatus.REJECTED, [str(reclaim["rejection_reason"])], sweep=sweep, reclaim=reclaim
        )

    mss = detect_judas_mss(setup_df, context.get("swings", []), reclaim, cfg)
    if mss is None:
        return _no_trade(context, JudasStatus.REJECTED, ["no_judas_mss"], sweep=sweep, reclaim=reclaim)

    direction = _direction(mss["direction"])
    displacement = _detect_displacement(setup_df, int(mss["confirmation_index"]), direction, cfg)
    if displacement is None:
        return _no_trade(context, JudasStatus.REJECTED, ["no_displacement_after_mss"], mss=mss)

    fvg = _select_fvg_after_displacement(setup_df, direction, int(displacement["end_index"]), cfg)
    if fvg is None:
        return _no_trade(context, JudasStatus.REJECTED, ["no_valid_fvg_or_ob"], mss=mss, displacement=displacement)

    retest = _detect_fvg_retest(entry_df, fvg, direction, cfg)
    if retest is None:
        return _no_trade(
            context,
            JudasStatus.WAITING_FOR_RETEST,
            ["waiting_for_fvg_or_ob_retest"],
            mss=mss,
            displacement=displacement,
            entry_poi=fvg,
        )

    risk = _risk_plan(sweep, retest, session_range, context.get("liquidity_pools", []), context, direction, cfg)
    if risk["final_target"] is None:
        return _no_trade(context, JudasStatus.REJECTED, ["no_valid_target_liquidity"], risk=risk)
    if risk["rr_to_final_target"] < float(cfg["min_rr"]):
        return _no_trade(context, JudasStatus.REJECTED, ["rr_below_minimum"], risk=risk)

    setup = {
        "direction": direction.value,
        "session_range": session_range,
        "manipulation": sweep,
        "reclaim": reclaim,
        "mss": mss,
        "displacement": displacement,
        "entry_poi": fvg,
        "entry": retest,
        "risk": risk,
        "range_quality": range_quality,
    }
    score = score_judas_swing_setup(setup, context, cfg)
    if not score["trade_allowed"]:
        return _no_trade(
            context,
            JudasStatus.REJECTED,
            score.get("hard_filter_failures") or ["score_or_filter_failed"],
            setup=setup,
            score=score,
        )

    symbol = str(context.get("symbol", "XAUUSD"))
    return {
        "strategy": "Judas Swing / Session Manipulation",
        "symbol": symbol,
        "signal_id": f"{symbol}_JUDAS_{direction.value.upper()}_{retest['retest_index']}",
        "signal_status": JudasStatus.VALID.value,
        "trade_allowed": True,
        "direction": direction.value,
        "session_range": session_range,
        "manipulation": sweep,
        "reclaim": reclaim,
        "mss": mss,
        "displacement": displacement,
        "entry_poi": fvg,
        "entry": retest,
        "risk": risk,
        "filters": _passed_filters(context),
        "score": score,
        "rejection_reasons": [],
        "warnings": [
            "Do not trade a Judas sweep without reclaim, MSS, displacement, and POI retest.",
            "Backtests should assume stop first if stop and target occur inside the same candle.",
        ],
    }


def score_judas_swing_setup(
    setup: Mapping[str, Any],
    context: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a complete Judas Swing setup from 0 to 10."""

    cfg = _config(config)
    direction = _direction(setup.get("direction"))
    safety_reasons = _safety_filter_reasons(context, cfg)
    risk = setup.get("risk", {}) or {}
    rr = float(_get(risk, "rr_to_final_target", default=0.0))
    hard_reasons = list(safety_reasons)
    if not setup.get("session_range"):
        hard_reasons.append("no_session_range")
    if not bool(_get(setup.get("range_quality", {}) or {}, "clean_range", default=False)):
        hard_reasons.append("poor_session_range_quality")
    if not setup.get("manipulation"):
        hard_reasons.append("no_judas_sweep")
    if not setup.get("reclaim"):
        hard_reasons.append("no_range_reclaim")
    if not setup.get("mss"):
        hard_reasons.append("no_judas_mss")
    if not setup.get("displacement"):
        hard_reasons.append("no_displacement_after_mss")
    if not setup.get("entry_poi"):
        hard_reasons.append("no_valid_fvg_or_ob")
    if rr < float(cfg["min_rr"]):
        hard_reasons.append("rr_below_minimum")
    if bool(_get(context, "double_sweep_chop", default=False)):
        hard_reasons.append("double_sweep_chop")
    if bool(_get(context.get("htf_context", {}) or {}, "htf_blocker_present", default=False)):
        hard_reasons.append("htf_poi_blocks_target")

    component_scores = {
        "session_range_quality": float(_get(setup.get("range_quality", {}) or {}, "quality_score", default=0.0)),
        "manipulation_sweep": float(_get(setup.get("manipulation", {}) or {}, "sweep_quality_score", default=0.0)),
        "reclaim_rejection": float(_get(setup.get("reclaim", {}) or {}, "reclaim_strength_score", default=0.0)),
        "mss_confirmation": float(_get(setup.get("mss", {}) or {}, "quality_score", default=0.0)),
        "displacement_strength": float(_get(setup.get("displacement", {}) or {}, "strength_score", default=0.0)),
        "fvg_ob_quality": float(_get(setup.get("entry_poi", {}) or {}, "quality_score", default=0.0)),
        "htf_alignment": _htf_score(context.get("htf_bias", context.get("higher_timeframe_bias")), direction),
        "target_rr": min(10.0, rr / max(float(cfg["min_rr"]), 1e-9) * 8.0),
        "xauusd_safety": 9.0 if not safety_reasons else 1.0,
        "session_timing": _session_timing_score(setup.get("manipulation", {}) or {}),
    }
    weights = {
        "session_range_quality": 0.12,
        "manipulation_sweep": 0.11,
        "reclaim_rejection": 0.11,
        "mss_confirmation": 0.12,
        "displacement_strength": 0.11,
        "fvg_ob_quality": 0.09,
        "htf_alignment": 0.07,
        "target_rr": 0.13,
        "xauusd_safety": 0.08,
        "session_timing": 0.06,
    }
    total = round(sum(component_scores[key] * weights[key] for key in weights), 2)
    minimum = float(cfg["minimum_setup_score"])
    return {
        "total_score": total,
        "grade": _grade(total),
        "trade_allowed": total >= minimum and not _dedupe(hard_reasons),
        "component_scores": {key: round(value, 2) for key, value in component_scores.items()},
        "minimum_required": minimum,
        "hard_filter_failures": _dedupe(hard_reasons),
    }


def _sweep_event(
    direction: JudasDirection,
    candle: _Candle,
    session_range: Mapping[str, Any],
    swept_side: str,
    depth: float,
    max_depth: float,
) -> dict[str, Any]:
    quality = _clamp(7.0 + min(depth, max_depth) / max(max_depth, 1e-9) * 2.0, 0, 10)
    return {
        "sweep_id": f"JUDAS_SWEEP_{swept_side.upper()}_{candle.index}",
        "direction_bias": direction.value,
        "swept_side": swept_side,
        "sweep_index": candle.index,
        "sweep_time": candle.timestamp,
        "sweep_extreme": candle.low if direction is JudasDirection.BULLISH else candle.high,
        "sweep_low": candle.low,
        "sweep_high": candle.high,
        "swept_level": float(session_range["range_low"] if swept_side == "range_low" else session_range["range_high"]),
        "trapped_side": "breakdown_sellers" if direction is JudasDirection.BULLISH else "breakout_buyers",
        "sweep_depth": round(depth, 5),
        "likely_news_or_breakout": depth > max_depth,
        "double_sweep_chop": False,
        "sweep_quality_score": round(quality, 2),
    }


def _double_sweep_event(
    candle: _Candle, session_range: Mapping[str, Any], range_low: float, range_high: float
) -> dict[str, Any]:
    return {
        "sweep_id": f"JUDAS_DOUBLE_SWEEP_{candle.index}",
        "direction_bias": JudasDirection.NONE.value,
        "swept_side": "range_high_and_range_low",
        "sweep_index": candle.index,
        "sweep_time": candle.timestamp,
        "sweep_extreme": None,
        "sweep_low": candle.low,
        "sweep_high": candle.high,
        "swept_level": None,
        "trapped_side": "both_sides",
        "sweep_depth": round((range_low - candle.low) + (candle.high - range_high), 5),
        "likely_news_or_breakout": True,
        "double_sweep_chop": True,
        "sweep_quality_score": 0.0,
        "session_range": dict(session_range),
    }


def _mss_event(
    direction: JudasDirection, swing: Mapping[str, Any], candle: _Candle, break_buffer: float
) -> dict[str, Any]:
    quality = _clamp(7.0 + abs(candle.close - float(swing["price"])) / max(break_buffer, 0.01) * 0.15, 0, 10)
    return {
        "mss_confirmed": True,
        "direction": direction.value,
        "broken_level": float(swing["price"]),
        "broken_swing_id": str(swing["swing_id"]),
        "confirmation_index": candle.index,
        "confirmation_time": candle.timestamp,
        "confirmed_by_close": True,
        "quality_score": round(quality, 2),
    }


def _detect_displacement(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    start_index: int,
    direction: JudasDirection,
    cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    candles = _closed_candles(df)
    atr = _atr(candles, int(cfg["atr_period"]))
    for candle in candles:
        if candle.index < start_index or candle.index > start_index + int(cfg["max_displacement_wait_candles"]):
            continue
        body_to_range = candle.body / candle.range
        range_to_atr = candle.range / max(atr, 1e-9)
        close_position = (
            candle.bullish_close_position if direction is JudasDirection.BULLISH else candle.bearish_close_position
        )
        directional = candle.bullish if direction is JudasDirection.BULLISH else candle.bearish
        if (
            directional
            and body_to_range >= float(cfg["displacement_min_body_to_range"])
            and range_to_atr >= float(cfg["displacement_min_range_to_atr"])
            and close_position >= float(cfg["displacement_min_close_position"])
        ):
            strength = _clamp(body_to_range * 3.0 + min(range_to_atr, 3.0) * 1.5 + close_position * 2.0, 0, 10)
            return {
                "direction": direction.value,
                "confirmed": True,
                "start_index": start_index,
                "end_index": candle.index,
                "body_to_range_ratio": round(body_to_range, 3),
                "range_to_atr_ratio": round(range_to_atr, 3),
                "close_position_score": round(close_position, 3),
                "strength_score": round(strength, 2),
            }
    return None


def _select_fvg_after_displacement(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    direction: JudasDirection,
    displacement_index: int,
    cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    fvgs = _detect_fvgs(df, cfg)
    candidates = [
        fvg
        for fvg in fvgs
        if fvg["direction"] == direction.value
        and int(fvg["creation_index"]) >= displacement_index
        and int(fvg["creation_index"]) <= displacement_index + int(cfg["max_fvg_after_displacement_candles"])
    ]
    if not candidates:
        return None
    return sorted(
        candidates, key=lambda item: (abs(int(item["creation_index"]) - displacement_index), -item["quality_score"])
    )[0]


def _detect_fvgs(df: Sequence[Mapping[str, Any] | Any] | Any, cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    candles = _closed_candles(df)
    fvgs: list[dict[str, Any]] = []
    for pos in range(2, len(candles)):
        c1, c2, c3 = candles[pos - 2], candles[pos - 1], candles[pos]
        if c1.high < c3.low:
            fvgs.append(_fvg_event(JudasDirection.BULLISH, c1, c2, c3, c1.high, c3.low, cfg))
        if c1.low > c3.high:
            fvgs.append(_fvg_event(JudasDirection.BEARISH, c1, c2, c3, c3.high, c1.low, cfg))
    return [fvg for fvg in fvgs if fvg["fvg_size"] >= float(cfg["min_fvg_size"])]


def _fvg_event(
    direction: JudasDirection,
    candle_1: _Candle,
    candle_2: _Candle,
    candle_3: _Candle,
    zone_low: float,
    zone_high: float,
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    fvg_size = zone_high - zone_low
    body_ratio = candle_2.body / candle_2.range
    quality = _clamp(6.5 + min(body_ratio * 2.5, 2.0) + min(fvg_size, 2.0) * 0.25, 0, 10)
    return {
        "poi_type": f"{direction.value}_fvg",
        "direction": direction.value,
        "zone_low": round(zone_low, 5),
        "zone_high": round(zone_high, 5),
        "zone_mid": round((zone_low + zone_high) / 2.0, 5),
        "fvg_size": round(fvg_size, 5),
        "creation_index": candle_3.index,
        "creation_time": candle_3.timestamp,
        "retest_status": "active",
        "quality_score": round(quality, 2),
    }


def _detect_fvg_retest(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    fvg: Mapping[str, Any],
    direction: JudasDirection,
    cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    candles = _closed_candles(df)
    mode = JudasEntryMode(str(cfg["entry_mode"]).lower())
    creation_index = int(fvg["creation_index"])
    zone_low = float(fvg["zone_low"])
    zone_high = float(fvg["zone_high"])
    zone_mid = float(fvg["zone_mid"])
    for candle in candles:
        if candle.index <= creation_index:
            continue
        if candle.index > creation_index + int(cfg["fvg_retest_expiry_candles"]):
            return None
        if not (candle.low <= zone_high and candle.high >= zone_low):
            continue
        if mode is JudasEntryMode.CONSERVATIVE:
            if direction is JudasDirection.BULLISH and not (candle.bullish and candle.close > zone_mid):
                continue
            if direction is JudasDirection.BEARISH and not (candle.bearish and candle.close < zone_mid):
                continue
            entry_price = candle.close
        else:
            entry_price = zone_mid
        return {
            "entry_price": round(entry_price, 5),
            "entry_triggered": True,
            "entry_time": candle.timestamp,
            "retest_index": candle.index,
            "entry_type": f"{direction.value}_judas_fvg_midpoint_entry",
        }
    return None


def _risk_plan(
    sweep: Mapping[str, Any],
    entry: Mapping[str, Any],
    session_range: Mapping[str, Any],
    pools: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    direction: JudasDirection,
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    candles = _closed_candles(context.get("df", context.get("candles", [])))
    atr = _atr(candles, int(cfg["atr_period"]))
    entry_price = float(entry["entry_price"])
    spread_buffer = float(_get(context.get("spread_status", {}) or {}, "spread_points", "spread", default=0.0))
    atr_buffer = atr * float(cfg["stop_atr_buffer_multiplier"])
    if direction is JudasDirection.BULLISH:
        stop = float(sweep["sweep_low"]) - atr_buffer - spread_buffer
        target_1 = float(session_range["range_mid"])
        target_2 = float(session_range["range_high"])
    else:
        stop = float(sweep["sweep_high"]) + atr_buffer + spread_buffer
        target_1 = float(session_range["range_mid"])
        target_2 = float(session_range["range_low"])
    final_target = _select_external_target(entry_price, stop, pools, context, direction) or target_2
    rr_to_target_2 = _rr(entry_price, stop, target_2, direction)
    rr_to_final = _rr(entry_price, stop, final_target, direction)
    return {
        "stop_loss": round(stop, 5),
        "stop_reference": (
            "below_manipulation_low_with_atr_and_spread_buffer"
            if direction is JudasDirection.BULLISH
            else "above_manipulation_high_with_atr_and_spread_buffer"
        ),
        "target_1": round(target_1, 5),
        "target_2": round(target_2, 5),
        "final_target": round(final_target, 5) if final_target is not None else None,
        "final_target_reference": "external_liquidity_or_opposite_range",
        "rr_to_target_2": round(rr_to_target_2, 2),
        "rr_to_final_target": round(rr_to_final, 2),
        "min_rr_required": float(cfg["min_rr"]),
    }


def _select_external_target(
    entry: float,
    stop: float,
    pools: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    direction: JudasDirection,
) -> float | None:
    target_context = context.get("target_liquidity")
    if isinstance(target_context, Mapping):
        price = _float(_get(target_context, "price", "target_price", "zone_mid", default=None))
        if price is not None:
            return price
    side = "buy_side" if direction is JudasDirection.BULLISH else "sell_side"
    targets: list[float] = []
    for pool in pools:
        if str(_get(pool, "direction", "side", default="")).lower() != side or _pool_swept(pool):
            continue
        low, high = _pool_bounds(pool)
        price = high if direction is JudasDirection.BULLISH else low
        if direction is JudasDirection.BULLISH and price > entry:
            targets.append(price)
        if direction is JudasDirection.BEARISH and price < entry:
            targets.append(price)
    if not targets:
        return None
    return max(targets) if direction is JudasDirection.BULLISH else min(targets)


def _rr(entry: float, stop: float, target: float, direction: JudasDirection) -> float:
    if direction is JudasDirection.BULLISH:
        return (target - entry) / max(entry - stop, 1e-9)
    return (entry - target) / max(stop - entry, 1e-9)


def _safety_filter_reasons(context: Mapping[str, Any], cfg: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    news = context.get("news_status", {}) or {}
    spread = context.get("spread_status", {}) or {}
    if bool(_get(news, "restricted", "news_restricted", default=False)):
        reasons.append("news_restricted")
    if bool(_get(news, "post_news_stabilized", default=True)) is False:
        reasons.append("post_news_structure_unstable")
    spread_value = _float(_get(spread, "spread_points", "spread", default=None))
    spread_status = str(_get(spread, "status", default="normal")).lower()
    if bool(_get(spread, "spread_safe", default=True)) is False or spread_status in {"high", "wide", "unsafe"}:
        reasons.append("spread_too_high")
    elif spread_value is not None and spread_value > float(cfg["max_spread_points"]):
        reasons.append("spread_too_high")
    if bool(_get(context, "double_sweep_chop", default=False)):
        reasons.append("double_sweep_chop")
    return _dedupe(reasons)


def _no_trade(
    context: Mapping[str, Any], status: JudasStatus, reasons: Sequence[str], **payload: Any
) -> dict[str, Any]:
    return {
        "strategy": "Judas Swing / Session Manipulation",
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
        "double_sweep_filter": "passed",
        "range_quality_filter": "passed",
        "htf_blocker_filter": "passed",
        "htf_bias": str(context.get("htf_bias", "neutral")),
    }


def _config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    data = dict(config or {})
    return {
        "session_range_config": data.get("session_range_config", {}),
        "broker_timezone": data.get("broker_timezone", "UTC"),
        "atr_period": int(data.get("atr_period", 14)),
        "min_candles_required": int(data.get("min_candles_required", 6)),
        "min_range_atr_multiplier": float(data.get("min_range_atr_multiplier", 0.6)),
        "max_range_atr_multiplier": float(data.get("max_range_atr_multiplier", 4.0)),
        "max_average_wick_ratio": float(data.get("max_average_wick_ratio", 0.72)),
        "max_dominant_spike_ratio": float(data.get("max_dominant_spike_ratio", 0.75)),
        "max_internal_trend_ratio": float(data.get("max_internal_trend_ratio", 0.85)),
        "minimum_range_quality_score": float(data.get("minimum_range_quality_score", 6.5)),
        "sweep_buffer": data.get("sweep_buffer"),
        "sweep_buffer_atr_multiplier": float(data.get("sweep_buffer_atr_multiplier", 0.02)),
        "min_sweep_depth": float(data.get("min_sweep_depth", 0.01)),
        "max_sweep_atr_multiplier": float(data.get("max_sweep_atr_multiplier", 3.0)),
        "break_buffer": data.get("break_buffer"),
        "break_buffer_atr_multiplier": float(data.get("break_buffer_atr_multiplier", 0.01)),
        "max_reclaim_wait_candles": int(data.get("max_reclaim_wait_candles", 5)),
        "max_mss_wait_candles": int(data.get("max_mss_wait_candles", 10)),
        "displacement_min_body_to_range": float(data.get("displacement_min_body_to_range", 0.55)),
        "displacement_min_range_to_atr": float(data.get("displacement_min_range_to_atr", 0.9)),
        "displacement_min_close_position": float(data.get("displacement_min_close_position", 0.70)),
        "max_displacement_wait_candles": int(data.get("max_displacement_wait_candles", 4)),
        "max_fvg_after_displacement_candles": int(data.get("max_fvg_after_displacement_candles", 2)),
        "min_fvg_size": float(data.get("min_fvg_size", 0.01)),
        "entry_mode": str(data.get("entry_mode", JudasEntryMode.BALANCED.value)).lower(),
        "fvg_retest_expiry_candles": int(data.get("fvg_retest_expiry_candles", 12)),
        "stop_atr_buffer_multiplier": float(data.get("stop_atr_buffer_multiplier", 0.02)),
        "max_spread_points": float(data.get("max_spread_points", 1.0)),
        "min_rr": float(data.get("min_rr", 2.0)),
        "minimum_setup_score": float(data.get("minimum_setup_score", 7.5)),
    }


def _session_config(session_config: Mapping[str, Any] | None, cfg: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(cfg.get("session_range_config") or {})
    data.update(dict(session_config or {}))
    return {
        "session_name": data.get("session_name", "Asian"),
        "start_time": data.get("start_time", "00:00"),
        "end_time": data.get("end_time", "06:00"),
        "timezone": data.get("timezone", cfg.get("broker_timezone", "UTC")),
        "range_type": data.get("range_type", "asian_range"),
        "min_candles_required": int(data.get("min_candles_required", cfg["min_candles_required"])),
        "enabled": bool(data.get("enabled", True)),
    }


def _empty_range(
    session: Mapping[str, Any], reasons: Sequence[str], start_dt: datetime | None = None, end_dt: datetime | None = None
) -> dict[str, Any]:
    return {
        "session_name": str(session.get("session_name", "Asian")),
        "range_start": start_dt.isoformat() if start_dt else None,
        "range_end": end_dt.isoformat() if end_dt else None,
        "range_high": None,
        "range_low": None,
        "range_mid": None,
        "range_size": 0.0,
        "candle_count": 0,
        "quality_score": 0.0,
        "clean_range": False,
        "valid_status": "invalid",
        "quality_rejection_reasons": list(reasons),
    }


def _inside_any_window(
    candle_time: datetime, windows: Sequence[Mapping[str, Any]] | Mapping[str, Any], cfg: Mapping[str, Any]
) -> bool:
    items = [windows] if isinstance(windows, Mapping) else list(windows)
    for window in items:
        if not bool(window.get("enabled", True)):
            continue
        window_tz = _tz(window.get("timezone", cfg["broker_timezone"]))
        local_time = candle_time.astimezone(window_tz)
        start_dt, end_dt = _window_bounds(local_time, str(window["start_time"]), str(window["end_time"]), window_tz)
        if start_dt <= local_time <= end_dt:
            return True
    return False


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
                    "swing_id": str(_get(swing, "swing_id", "id", default=f"SWING_{kind}_{_get(swing, 'index')}")),
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
    hour, minute = value.split(":")[:2]
    return time(hour=int(hour), minute=int(minute))


def _as_datetime(value: Any, broker_timezone: str | timezone | None = "UTC") -> datetime:
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


def _configured_buffer(cfg: Mapping[str, Any], key: str, default: float) -> float:
    value = cfg.get(key)
    return default if value in {None, ""} else float(value)


def _direction(value: Any) -> JudasDirection:
    text = str(value or "").lower()
    if text in {"bullish", "buy", "long"}:
        return JudasDirection.BULLISH
    if text in {"bearish", "sell", "short"}:
        return JudasDirection.BEARISH
    return JudasDirection.NONE


def _session_timing_score(sweep: Mapping[str, Any]) -> float:
    if not sweep:
        return 0.0
    return 8.5 if sweep.get("sweep_time") is not None else 6.5


def _htf_score(htf_bias: Mapping[str, Any] | str | None, direction: JudasDirection) -> float:
    text = (
        " ".join(str(value).lower() for value in htf_bias.values())
        if isinstance(htf_bias, Mapping)
        else str(htf_bias or "neutral").lower()
    )
    if direction.value in text:
        return 9.0
    if "neutral" in text or not text:
        return 7.0
    if ("bullish" in text and direction is JudasDirection.BEARISH) or (
        "bearish" in text and direction is JudasDirection.BULLISH
    ):
        return 4.0
    return 6.0


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
