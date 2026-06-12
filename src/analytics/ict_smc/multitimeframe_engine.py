"""Multi-timeframe ICT/SMC context engine.

This module coordinates already-detected ICT/SMC context across Daily, 1H,
15M, and 5M data. It is deliberately rule-based and closed-candle only: the
engine does not submit orders and it does not treat forming HTF candles as
confirmed evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Sequence


class MTFTimeframe(str, Enum):
    DAILY = "1D"
    H1 = "1H"
    M15 = "15M"
    M5 = "5M"


class MTFDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    NONE = "none"


class MTFStatus(str, Enum):
    TRADE_ALLOWED = "trade_allowed"
    TRADE_BLOCKED = "trade_blocked"
    INSUFFICIENT_DATA = "insufficient_data"
    LOOKAHEAD_BLOCKED = "lookahead_blocked"


@dataclass(frozen=True, slots=True)
class _Candle:
    index: int
    timestamp: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True


_TIMEFRAME_MINUTES = {
    MTFTimeframe.DAILY.value: 1440,
    MTFTimeframe.H1.value: 60,
    MTFTimeframe.M15.value: 15,
    MTFTimeframe.M5.value: 5,
}


def prepare_timeframe_data(
    rows: Sequence[Mapping[str, Any] | Any] | Any,
    timeframe: str | MTFTimeframe,
) -> list[dict[str, Any]]:
    """Normalize OHLCV rows, sort them, and attach deterministic close times."""

    tf = _timeframe_value(timeframe)
    candles = [_normalize_candle(row, tf, index) for index, row in enumerate(_as_list(rows))]
    unique: dict[datetime, _Candle] = {}
    for candle in candles:
        unique[candle.timestamp] = candle
    return [_candle_output(candle, tf) for candle in sorted(unique.values(), key=lambda c: c.timestamp)]


def get_closed_candles_asof(
    rows: Sequence[Mapping[str, Any] | Any] | Any,
    timeframe: str | MTFTimeframe,
    eval_time: str | datetime | None,
) -> list[dict[str, Any]]:
    """Return only candles fully closed at or before ``eval_time``."""

    eval_dt = _parse_time(eval_time)
    if eval_dt is None:
        prepared = prepare_timeframe_data(rows, timeframe)
        return [row for row in prepared if bool(row.get("is_closed", True))]
    return [
        row
        for row in prepare_timeframe_data(rows, timeframe)
        if bool(row.get("is_closed", True)) and _parse_time(row.get("close_time")) <= eval_dt
    ]


def align_timeframes(
    all_timeframes: Mapping[str, Sequence[Mapping[str, Any] | Any] | Any],
    eval_time: str | datetime | None,
) -> dict[str, Any]:
    """Slice Daily, 1H, 15M, and 5M data to the same closed-candle clock."""

    normalized = _normalize_timeframe_inputs(all_timeframes)
    eval_dt = _resolve_eval_time(normalized, eval_time)
    slices: dict[str, list[dict[str, Any]]] = {}
    closed_status: dict[str, Any] = {}
    warnings: list[str] = []

    for tf in (MTFTimeframe.DAILY, MTFTimeframe.H1, MTFTimeframe.M15, MTFTimeframe.M5):
        candles = get_closed_candles_asof(normalized.get(tf.value, []), tf, eval_dt)
        slices[tf.value] = candles
        latest = candles[-1] if candles else None
        closed_status[_status_key(tf)] = latest.get("close_time") if latest else None
        if not candles:
            warnings.append(f"no_closed_{tf.value.lower()}_candles")

    return {
        "eval_time": _iso(eval_dt),
        "slices": slices,
        "closed_candle_status": closed_status,
        "warnings": _dedupe(warnings),
    }


def build_daily_context(
    daily_df: Sequence[Mapping[str, Any] | Any] | Any,
    override_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the Daily map from completed Daily candles and optional detector output."""

    candles = [_mapping(row) for row in _as_list(daily_df)]
    override = dict(override_context or {})
    latest = candles[-1] if candles else {}
    previous = candles[-2] if len(candles) >= 2 else latest
    context = {
        "role": "liquidity_map_and_major_poi",
        "bias": _direction_value(override.get("bias", "neutral")),
        "pdh": override.get("pdh")
        or _level("previous_day_high", "buy_side", previous.get("high")),
        "pdl": override.get("pdl")
        or _level("previous_day_low", "sell_side", previous.get("low")),
        "poi_zones": _normalize_zones(
            override.get("poi_zones", override.get("daily_poi_zones", [])),
            MTFTimeframe.DAILY.value,
        ),
        "liquidity_pools": _as_list(override.get("liquidity_pools", [])),
        "source_latest_closed": latest.get("close_time"),
    }
    context.update({k: v for k, v in override.items() if k not in {"poi_zones", "daily_poi_zones"}})
    context["poi_zones"] = _normalize_zones(
        context.get("poi_zones", context.get("daily_poi_zones", [])),
        MTFTimeframe.DAILY.value,
    )
    return context


def build_h1_bias_context(
    h1_df: Sequence[Mapping[str, Any] | Any] | Any,
    daily_context: Mapping[str, Any] | None = None,
    override_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build 1H directional bias and draw-on-liquidity context."""

    candles = [_mapping(row) for row in _as_list(h1_df)]
    override = dict(override_context or {})
    latest = candles[-1] if candles else {}
    inferred = _infer_bias(candles)
    h1_bias = _direction_value(override.get("h1_bias", override.get("bias", inferred)))
    context = {
        "role": "bias_and_draw_on_liquidity",
        "h1_bias": h1_bias,
        "structure_state": override.get("structure_state", f"{h1_bias}_structure"),
        "expected_draw": override.get(
            "expected_draw",
            "buy_side" if h1_bias == MTFDirection.BULLISH.value else "sell_side"
            if h1_bias == MTFDirection.BEARISH.value
            else "neutral",
        ),
        "poi_zones": _normalize_zones(
            override.get("poi_zones", override.get("active_h1_pois", [])),
            MTFTimeframe.H1.value,
        ),
        "liquidity_pools": _as_list(override.get("liquidity_pools", [])),
        "daily_bias_reference": (daily_context or {}).get("bias"),
        "source_latest_closed": latest.get("close_time"),
    }
    context.update({k: v for k, v in override.items() if k not in {"poi_zones", "active_h1_pois"}})
    context["poi_zones"] = _normalize_zones(
        context.get("poi_zones", context.get("active_h1_pois", [])),
        MTFTimeframe.H1.value,
    )
    return context


def detect_m15_setup(
    m15_df: Sequence[Mapping[str, Any] | Any] | Any,
    daily_context: Mapping[str, Any] | None = None,
    h1_context: Mapping[str, Any] | None = None,
    override_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Represent the 15M setup layer from closed candles and detector output."""

    candles = [_mapping(row) for row in _as_list(m15_df)]
    override = dict(override_context or {})
    latest = candles[-1] if candles else {}
    detected = bool(override.get("setup_detected", override.get("confirmed", False)))
    direction = _direction_value(override.get("direction", MTFDirection.NONE.value))
    context = {
        "role": "setup_and_sweep",
        "setup_detected": detected,
        "confirmed": bool(override.get("confirmed", detected)),
        "setup_type": override.get("setup_type"),
        "direction": direction,
        "poi_zones": _normalize_zones(
            override.get("poi_zones", _maybe_single_zone(override.get("setup_poi"))),
            MTFTimeframe.M15.value,
        ),
        "liquidity_pools": _as_list(override.get("liquidity_pools", [])),
        "target_liquidity": override.get("target_liquidity", {}),
        "setup_valid_from_time": override.get("setup_valid_from_time") or latest.get("close_time"),
        "daily_context_bias": (daily_context or {}).get("bias"),
        "h1_context_bias": (h1_context or {}).get("h1_bias"),
        "source_latest_closed": latest.get("close_time"),
    }
    context.update({k: v for k, v in override.items() if k not in {"poi_zones", "setup_poi"}})
    context["poi_zones"] = _normalize_zones(
        context.get("poi_zones", _maybe_single_zone(context.get("setup_poi"))),
        MTFTimeframe.M15.value,
    )
    return context


def map_htf_zones_to_ltf(
    htf_zones: Sequence[Mapping[str, Any] | Any] | Any,
    ltf_df: Sequence[Mapping[str, Any] | Any] | Any,
    target_timeframe: str | MTFTimeframe = MTFTimeframe.M5,
) -> list[dict[str, Any]]:
    """Project valid HTF POI zones onto the target lower timeframe."""

    tf = _timeframe_value(target_timeframe)
    ltf_rows = [_mapping(row) for row in _as_list(ltf_df)]
    latest_ltf_close = _latest_close_time(ltf_rows)
    mapped: list[dict[str, Any]] = []
    if latest_ltf_close is None:
        return mapped

    for zone in _normalize_zones(htf_zones, "unknown"):
        valid_from = _parse_time(zone.get("valid_from_time") or zone.get("confirmed_at"))
        if valid_from is None or valid_from > latest_ltf_close:
            continue
        if not bool(zone.get("active_status", True)) or bool(zone.get("invalidated", False)):
            continue
        zone_low = _float(zone.get("zone_low"))
        zone_high = _float(zone.get("zone_high"))
        if zone_low is None or zone_high is None:
            continue
        low, high = sorted((zone_low, zone_high))
        touches = [
            row
            for row in ltf_rows
            if (_parse_time(row.get("close_time")) or _parse_time(row.get("timestamp"))) >= valid_from
            and _float(row.get("low")) is not None
            and _float(row.get("high")) is not None
            and _float(row.get("low")) <= high
            and _float(row.get("high")) >= low
        ]
        mapped_zone = dict(zone)
        mapped_zone.update(
            {
                "mapped_to_timeframe": tf,
                "zone_low": low,
                "zone_high": high,
                "zone_mid": round((low + high) / 2.0, 5),
                "touched": bool(touches),
                "touch_count": len(touches),
                "first_touch_time": touches[0].get("close_time") if touches else None,
                "retest_status": "touched" if touches else "untouched",
            }
        )
        mapped.append(mapped_zone)
    return mapped


def detect_ltf_confirmation(
    m5_df: Sequence[Mapping[str, Any] | Any] | Any,
    mapped_zones: Sequence[Mapping[str, Any] | Any] | Any,
    m15_setup: Mapping[str, Any],
    h1_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Confirm whether 5M timing exists after price enters the HTF/15M POI."""

    if not bool(m15_setup.get("setup_detected")):
        return _ltf_result(False, MTFDirection.NONE.value, "no_15m_setup")

    direction = _direction_value(m15_setup.get("direction"))
    if direction == MTFDirection.NONE.value:
        return _ltf_result(False, direction, "invalid_m15_direction")

    m5_rows = [_mapping(row) for row in _as_list(m5_df)]
    latest_m5_close = _latest_close_time(m5_rows)
    relevant_zones = [
        _mapping(zone)
        for zone in _as_list(mapped_zones)
        if _direction_value(_field(zone, "direction")) == direction
    ]
    entered_zones = [zone for zone in relevant_zones if bool(zone.get("touched"))]

    confirmation = _mapping(
        m15_setup.get("ltf_confirmation")
        or m15_setup.get("m5_confirmation")
        or {}
    )
    confirmation_time = _parse_time(
        confirmation.get("entry_valid_from_time")
        or confirmation.get("confirmation_time")
        or confirmation.get("valid_from_time")
    )
    if confirmation_time is not None and latest_m5_close is not None and confirmation_time > latest_m5_close:
        return _ltf_result(
            False,
            direction,
            "ltf_confirmation_after_latest_closed_5m",
            entered_zones,
            confirmation,
        )

    if not entered_zones:
        return _ltf_result(False, direction, "waiting_for_price_to_enter_poi", [], confirmation)

    confirmed = bool(confirmation.get("confirmed", confirmation.get("ltf_confirmed", False)))
    sweep_key = "sell_side_sweep_inside_poi" if direction == MTFDirection.BULLISH.value else "buy_side_sweep_inside_poi"
    has_sweep = bool(
        confirmation.get(sweep_key)
        or confirmation.get("ltf_sweep")
        or confirmation.get("sweep_inside_poi")
    )
    has_mss = bool(confirmation.get("mss_confirmed") or confirmation.get("ltf_mss"))
    has_displacement = bool(
        confirmation.get("displacement_confirmed") or confirmation.get("ltf_displacement")
    )
    has_entry_zone = bool(confirmation.get("entry_zone") or confirmation.get("ltf_entry_zone"))
    ready = confirmed and has_sweep and has_mss and has_displacement and has_entry_zone
    reason = "ltf_confirmation_ready" if ready else "waiting_for_5m_confirmation"

    result = _ltf_result(ready, direction, reason, entered_zones, confirmation)
    result["h1_bias_reference"] = (h1_context or {}).get("h1_bias")
    return result


def build_multitimeframe_context(
    all_timeframes: Mapping[str, Sequence[Mapping[str, Any] | Any] | Any],
    eval_time: str | datetime | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one unified closed-candle MTF context object."""

    cfg = dict(config or {})
    aligned = align_timeframes(all_timeframes, eval_time)
    slices = aligned["slices"]
    eval_dt = _parse_time(aligned["eval_time"])
    warnings = list(aligned.get("warnings", []))

    daily_context = build_daily_context(slices[MTFTimeframe.DAILY.value], cfg.get("daily_context"))
    h1_context = build_h1_bias_context(
        slices[MTFTimeframe.H1.value],
        daily_context,
        cfg.get("h1_context"),
    )
    m15_context = detect_m15_setup(
        slices[MTFTimeframe.M15.value],
        daily_context,
        h1_context,
        cfg.get("m15_setup"),
    )
    if cfg.get("m5_confirmation"):
        m15_context["ltf_confirmation"] = cfg["m5_confirmation"]

    htf_zones = (
        _as_list(daily_context.get("poi_zones"))
        + _as_list(h1_context.get("poi_zones"))
        + _as_list(m15_context.get("poi_zones"))
    )
    mapped_zones = map_htf_zones_to_ltf(
        htf_zones,
        slices[MTFTimeframe.M5.value],
        MTFTimeframe.M5,
    )
    m5_context = detect_ltf_confirmation(
        slices[MTFTimeframe.M5.value],
        mapped_zones,
        m15_context,
        h1_context,
    )
    lookahead = _lookahead_audit(
        eval_dt,
        aligned["closed_candle_status"],
        daily_context,
        h1_context,
        m15_context,
        m5_context,
    )
    warnings.extend(lookahead["warnings"])
    combined_bias = _combined_bias(daily_context, h1_context, m15_context, m5_context)
    readiness = _trade_readiness(
        lookahead["lookahead_safe"],
        bool(m15_context.get("setup_detected")),
        bool(m5_context.get("ltf_confirmed")),
        m15_context,
        combined_bias,
        bool(cfg.get("require_ltf_confirmation", True)),
    )

    return {
        "function": "build_multitimeframe_context",
        "concept_name": "Multi-Timeframe ICT/SMC Engine",
        "symbol": cfg.get("symbol", "XAUUSD"),
        "eval_time": _iso(eval_dt),
        "timezone": cfg.get("timezone", "UTC"),
        "lookahead_safe": lookahead["lookahead_safe"],
        "closed_candle_status": aligned["closed_candle_status"],
        "daily_context": daily_context,
        "h1_context": h1_context,
        "m15_context": m15_context,
        "mapped_zones_to_m5": mapped_zones,
        "m5_context": m5_context,
        "combined_bias": combined_bias,
        "trade_readiness": readiness,
        "all_liquidity_pools": (
            _as_list(daily_context.get("liquidity_pools"))
            + _as_list(h1_context.get("liquidity_pools"))
            + _as_list(m15_context.get("liquidity_pools"))
            + _as_list(m5_context.get("liquidity_pools"))
        ),
        "all_poi_zones": htf_zones + _as_list(m5_context.get("poi_zones")),
        "warnings": _dedupe(warnings),
    }


def run_multitimeframe_engine(
    all_timeframes: Mapping[str, Sequence[Mapping[str, Any] | Any] | Any],
    eval_time: str | datetime | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the MTF decision gate before entry, stop, target, and scoring layers."""

    cfg = dict(config or {})
    context = build_multitimeframe_context(all_timeframes, eval_time, cfg)
    readiness = context["trade_readiness"]
    score_result = _mapping(cfg.get("score_result", {}))
    warnings = list(context.get("warnings", []))

    if not context["lookahead_safe"]:
        status = MTFStatus.LOOKAHEAD_BLOCKED
        trade_allowed = False
        reason = "unclosed_htf_candle_used"
    elif any(value is None for value in context["closed_candle_status"].values()):
        status = MTFStatus.INSUFFICIENT_DATA
        trade_allowed = False
        reason = "insufficient_closed_candle_data"
    elif not readiness["setup_ready"]:
        status = MTFStatus.TRADE_BLOCKED
        trade_allowed = False
        reason = readiness["reason"]
    elif bool(readiness["entry_confirmation_required"]) and not readiness["entry_confirmation_ready"]:
        status = MTFStatus.TRADE_BLOCKED
        trade_allowed = False
        reason = readiness["reason"]
    elif readiness["reason"] == "target_blocked_by_htf_poi":
        status = MTFStatus.TRADE_BLOCKED
        trade_allowed = False
        reason = readiness["reason"]
    else:
        score_allows = bool(score_result.get("trade_allowed", True))
        status = MTFStatus.TRADE_ALLOWED if score_allows else MTFStatus.TRADE_BLOCKED
        trade_allowed = score_allows
        reason = "multi_timeframe_alignment_ready" if score_allows else "score_result_blocked"

    direction = _direction_value(context["m15_context"].get("direction"))
    return {
        "function": "run_multitimeframe_engine",
        "concept_name": "Multi-Timeframe ICT/SMC Engine",
        "symbol": context.get("symbol"),
        "eval_time": context.get("eval_time"),
        "decision": status.value,
        "trade_allowed": trade_allowed,
        "direction": direction,
        "timeframe_stack": {
            "daily": "liquidity_map",
            "h1": context["h1_context"].get("h1_bias"),
            "m15": "setup_confirmed"
            if context["m15_context"].get("setup_detected")
            else "no_setup",
            "m5": "entry_confirmed"
            if context["m5_context"].get("ltf_confirmed")
            else "entry_waiting",
        },
        "multi_timeframe_context": context,
        "setup_context": {
            "direction": direction,
            "daily_context": context["daily_context"],
            "h1_context": context["h1_context"],
            "m15_context": context["m15_context"],
            "m5_context": context["m5_context"],
            "mapped_zones": context["mapped_zones_to_m5"],
        },
        "entry_signal": {
            "entry_signal": trade_allowed,
            "direction": direction,
            "reason": reason,
        },
        "score_result": score_result,
        "trade_decision": {
            "status": status.value,
            "trade_allowed": trade_allowed,
            "reason": reason,
        },
        "warnings": _dedupe(warnings),
    }


def _trade_readiness(
    lookahead_safe: bool,
    setup_ready: bool,
    ltf_ready: bool,
    m15_context: Mapping[str, Any],
    combined_bias: Mapping[str, Any],
    require_ltf_confirmation: bool,
) -> dict[str, Any]:
    reason = "multi_timeframe_alignment_ready"
    if not lookahead_safe:
        reason = "unclosed_htf_candle_used"
    elif not setup_ready:
        reason = "no_15m_setup"
    elif combined_bias.get("alignment_status") == "conflict":
        reason = "timeframe_bias_conflict"
    elif _target_blocked(m15_context):
        reason = "target_blocked_by_htf_poi"
    elif require_ltf_confirmation and not ltf_ready:
        reason = "waiting_for_5m_confirmation"
    return {
        "setup_ready": setup_ready,
        "entry_confirmation_ready": ltf_ready,
        "entry_confirmation_required": require_ltf_confirmation,
        "needs_rr_check": True,
        "needs_news_filter_check": True,
        "trade_allowed_before_scoring": reason == "multi_timeframe_alignment_ready",
        "reason": reason,
    }


def _combined_bias(
    daily_context: Mapping[str, Any],
    h1_context: Mapping[str, Any],
    m15_context: Mapping[str, Any],
    m5_context: Mapping[str, Any],
) -> dict[str, Any]:
    daily_bias = _direction_value(daily_context.get("bias", MTFDirection.NEUTRAL.value))
    h1_bias = _direction_value(h1_context.get("h1_bias", MTFDirection.NEUTRAL.value))
    m15_direction = _direction_value(m15_context.get("direction", MTFDirection.NONE.value))
    m5_direction = _direction_value(m5_context.get("direction", MTFDirection.NONE.value))
    notes: list[str] = []
    directional = [v for v in (h1_bias, m15_direction, m5_direction) if v in {"bullish", "bearish"}]
    if len(set(directional)) > 1:
        alignment = "conflict"
        notes.append("Directional timeframes disagree; do not force a trade.")
    elif directional:
        alignment = directional[0]
    else:
        alignment = MTFDirection.NEUTRAL.value

    if daily_bias in {"bullish", "bearish"} and directional and daily_bias != directional[0]:
        notes.append("Daily bias opposes intraday setup; target quality should be reduced.")

    return {
        "daily_bias": daily_bias,
        "h1_bias": h1_bias,
        "m15_setup_direction": m15_direction,
        "m5_entry_direction": m5_direction,
        "alignment_status": alignment,
        "conflict_notes": notes,
    }


def _lookahead_audit(
    eval_time: datetime | None,
    closed_status: Mapping[str, Any],
    *contexts: Mapping[str, Any],
) -> dict[str, Any]:
    warnings: list[str] = []
    if eval_time is None:
        return {"lookahead_safe": False, "warnings": ["missing_eval_time"]}

    latest_by_tf = {
        MTFTimeframe.DAILY.value: _parse_time(closed_status.get("daily_latest_closed")),
        MTFTimeframe.H1.value: _parse_time(closed_status.get("h1_latest_closed")),
        MTFTimeframe.M15.value: _parse_time(closed_status.get("m15_latest_closed")),
        MTFTimeframe.M5.value: _parse_time(closed_status.get("m5_latest_closed")),
    }
    for context in contexts:
        for item in _walk_mappings(context):
            source_tf = _timeframe_from_value(
                item.get("source_timeframe")
                or item.get("timeframe")
                or item.get("source_tf")
            )
            for key in ("confirmation_time", "valid_from_time", "confirmed_at", "setup_valid_from_time"):
                event_time = _parse_time(item.get(key))
                if event_time is None:
                    continue
                if event_time > eval_time:
                    warnings.append("future_context_time_used")
                if source_tf and latest_by_tf.get(source_tf) and event_time > latest_by_tf[source_tf]:
                    warnings.append("unclosed_htf_candle_used")
    return {"lookahead_safe": not warnings, "warnings": _dedupe(warnings)}


def _ltf_result(
    confirmed: bool,
    direction: str,
    reason: str,
    entered_zones: Sequence[Mapping[str, Any]] | None = None,
    confirmation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    confirmation = _mapping(confirmation or {})
    return {
        "role": "entry_confirmation",
        "ltf_confirmed": confirmed,
        "confirmed": confirmed,
        "direction": direction,
        "price_inside_poi": bool(entered_zones),
        "entered_zones": [dict(zone) for zone in (entered_zones or [])],
        "ltf_sweep": bool(
            confirmation.get("ltf_sweep")
            or confirmation.get("sell_side_sweep_inside_poi")
            or confirmation.get("buy_side_sweep_inside_poi")
            or confirmation.get("sweep_inside_poi")
        ),
        "ltf_mss": bool(confirmation.get("ltf_mss") or confirmation.get("mss_confirmed")),
        "ltf_displacement": bool(
            confirmation.get("ltf_displacement") or confirmation.get("displacement_confirmed")
        ),
        "ltf_entry_zone": confirmation.get("entry_zone") or confirmation.get("ltf_entry_zone"),
        "entry_valid_from_time": confirmation.get("entry_valid_from_time"),
        "reason": reason,
        "liquidity_pools": _as_list(confirmation.get("liquidity_pools", [])),
        "poi_zones": _as_list(confirmation.get("poi_zones", [])),
    }


def _target_blocked(m15_context: Mapping[str, Any]) -> bool:
    target = _mapping(m15_context.get("target_liquidity", {}))
    return bool(
        target.get("blocked")
        or target.get("blocked_by_daily_poi")
        or target.get("final_target_blocked")
    ) and not bool(target.get("closer_target_meets_rr", False))


def _normalize_candle(row: Mapping[str, Any] | Any, timeframe: str, index: int) -> _Candle:
    data = _mapping(row)
    timestamp = _parse_time(data.get("timestamp") or data.get("time") or data.get("open_time"))
    if timestamp is None:
        raise ValueError("Each candle must include a timestamp/time/open_time field.")
    close_time = _parse_time(data.get("close_time"))
    if close_time is None:
        close_time = timestamp + timedelta(minutes=_TIMEFRAME_MINUTES[timeframe])
    return _Candle(
        index=int(data.get("index", index)),
        timestamp=timestamp,
        close_time=close_time,
        open=float(data.get("open", data.get("o", 0.0))),
        high=float(data.get("high", data.get("h", 0.0))),
        low=float(data.get("low", data.get("l", 0.0))),
        close=float(data.get("close", data.get("c", 0.0))),
        volume=float(data.get("volume", data.get("tick_volume", data.get("v", 0.0)))),
        is_closed=bool(data.get("is_closed", True)),
    )


def _candle_output(candle: _Candle, timeframe: str) -> dict[str, Any]:
    return {
        "index": candle.index,
        "timestamp": _iso(candle.timestamp),
        "close_time": _iso(candle.close_time),
        "timeframe": timeframe,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
        "is_closed": candle.is_closed,
    }


def _resolve_eval_time(
    normalized: Mapping[str, Sequence[Mapping[str, Any] | Any] | Any],
    eval_time: str | datetime | None,
) -> datetime | None:
    explicit = _parse_time(eval_time)
    if explicit is not None:
        return explicit
    m5 = get_closed_candles_asof(normalized.get(MTFTimeframe.M5.value, []), MTFTimeframe.M5, None)
    if not m5:
        return None
    return _parse_time(m5[-1].get("close_time"))


def _normalize_timeframe_inputs(
    all_timeframes: Mapping[str, Sequence[Mapping[str, Any] | Any] | Any],
) -> dict[str, Sequence[Mapping[str, Any] | Any] | Any]:
    normalized: dict[str, Sequence[Mapping[str, Any] | Any] | Any] = {}
    for key, value in all_timeframes.items():
        normalized[_timeframe_value(key)] = value
    return normalized


def _normalize_zones(
    zones: Sequence[Mapping[str, Any] | Any] | Any,
    default_source_timeframe: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, zone in enumerate(_as_list(zones)):
        data = dict(_mapping(zone))
        if not data:
            continue
        low = _float(data.get("zone_low", data.get("low")))
        high = _float(data.get("zone_high", data.get("high")))
        if low is None or high is None:
            continue
        zone_low, zone_high = sorted((low, high))
        data.setdefault("zone_id", f"{default_source_timeframe}_ZONE_{index + 1}")
        data.setdefault("source_timeframe", default_source_timeframe)
        data.setdefault("zone_type", data.get("type", "poi_zone"))
        data.setdefault("direction", _direction_value(data.get("direction", MTFDirection.NEUTRAL.value)))
        data.setdefault("active_status", True)
        data.setdefault("invalidated", False)
        data["zone_low"] = zone_low
        data["zone_high"] = zone_high
        data.setdefault("zone_mid", round((zone_low + zone_high) / 2.0, 5))
        data.setdefault(
            "valid_from_time",
            data.get("confirmed_at") or data.get("confirmation_time") or data.get("created_at"),
        )
        normalized.append(data)
    return normalized


def _maybe_single_zone(zone: Any) -> list[Any]:
    return [] if not zone else [zone]


def _level(level_type: str, direction: str, price: Any) -> dict[str, Any] | None:
    value = _float(price)
    if value is None:
        return None
    return {"level_type": level_type, "direction": direction, "price": value, "swept_status": "unknown"}


def _infer_bias(candles: Sequence[Mapping[str, Any]]) -> str:
    if len(candles) < 2:
        return MTFDirection.NEUTRAL.value
    previous = _float(candles[-2].get("close"))
    latest = _float(candles[-1].get("close"))
    if previous is None or latest is None or latest == previous:
        return MTFDirection.NEUTRAL.value
    return MTFDirection.BULLISH.value if latest > previous else MTFDirection.BEARISH.value


def _latest_close_time(rows: Sequence[Mapping[str, Any]]) -> datetime | None:
    times = [_parse_time(row.get("close_time") or row.get("timestamp")) for row in rows]
    times = [value for value in times if value is not None]
    return max(times) if times else None


def _status_key(timeframe: MTFTimeframe) -> str:
    return {
        MTFTimeframe.DAILY: "daily_latest_closed",
        MTFTimeframe.H1: "h1_latest_closed",
        MTFTimeframe.M15: "m15_latest_closed",
        MTFTimeframe.M5: "m5_latest_closed",
    }[timeframe]


def _walk_mappings(value: Any) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        out.append(value)
        for inner in value.values():
            out.extend(_walk_mappings(inner))
    elif isinstance(value, list | tuple):
        for inner in value:
            out.extend(_walk_mappings(inner))
    return out


def _timeframe_from_value(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return _timeframe_value(value)
    except ValueError:
        return None


def _timeframe_value(value: str | MTFTimeframe) -> str:
    raw = value.value if isinstance(value, MTFTimeframe) else str(value)
    key = raw.strip().lower().replace("_", "").replace("-", "")
    aliases = {
        "daily": MTFTimeframe.DAILY.value,
        "d1": MTFTimeframe.DAILY.value,
        "1d": MTFTimeframe.DAILY.value,
        "day": MTFTimeframe.DAILY.value,
        "h1": MTFTimeframe.H1.value,
        "1h": MTFTimeframe.H1.value,
        "hourly": MTFTimeframe.H1.value,
        "m15": MTFTimeframe.M15.value,
        "15m": MTFTimeframe.M15.value,
        "15min": MTFTimeframe.M15.value,
        "m5": MTFTimeframe.M5.value,
        "5m": MTFTimeframe.M5.value,
        "5min": MTFTimeframe.M5.value,
    }
    if key not in aliases:
        raise ValueError(f"Unsupported timeframe: {value!r}")
    return aliases[key]


def _direction_value(value: Any) -> str:
    raw = value.value if isinstance(value, MTFDirection) else str(value or "").strip().lower()
    aliases = {
        "buy": MTFDirection.BULLISH.value,
        "long": MTFDirection.BULLISH.value,
        "bull": MTFDirection.BULLISH.value,
        "bullish": MTFDirection.BULLISH.value,
        "sell": MTFDirection.BEARISH.value,
        "short": MTFDirection.BEARISH.value,
        "bear": MTFDirection.BEARISH.value,
        "bearish": MTFDirection.BEARISH.value,
        "neutral": MTFDirection.NEUTRAL.value,
        "range": MTFDirection.NEUTRAL.value,
        "ranging": MTFDirection.NEUTRAL.value,
        "none": MTFDirection.NONE.value,
        "": MTFDirection.NONE.value,
    }
    return aliases.get(raw, MTFDirection.NONE.value)


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if hasattr(value, "to_pydatetime"):
        parsed = value.to_pydatetime()
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if hasattr(value, "to_dict") and not isinstance(value, Mapping):
        try:
            return list(value.to_dict("records"))
        except TypeError:
            return list(value.to_dict())
    return [value]


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
