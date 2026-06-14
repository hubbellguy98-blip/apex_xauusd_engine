"""News Liquidity Sweep strategy model for ICT/SMC research.

This layer treats high-impact news as a dangerous liquidity event, not as a
normal displacement signal. A valid setup requires:

news restriction clear -> first spike observed but not traded -> stabilization
-> liquidity sweep -> reclaim/rejection -> MSS -> displacement -> entry POI
-> target and RR validation.

The module is deterministic, closed-candle only, and never places orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence


class NewsSweepStatus(str, Enum):
    VALID = "valid"
    REJECTED = "rejected"
    WAITING = "waiting_for_retest"
    NO_TRADE = "no_trade"


@dataclass(frozen=True, slots=True)
class _Candle:
    position: int
    index: int
    timestamp: datetime | None
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


def is_news_restricted_time(
    timestamp: Any,
    news_calendar: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the active news restriction state for a timestamp."""

    cfg = _config(config)
    current = _parse_time(timestamp)
    if current is None:
        return {
            "restricted": False,
            "restriction_type": "clear",
            "reason": "timestamp_unavailable",
        }

    selected = _select_relevant_news_event(current, news_calendar, cfg)
    if selected is None:
        return {
            "restricted": False,
            "restriction_type": "clear",
            "event": None,
            "reason": "no_relevant_news_event",
        }

    event_time = _event_time(selected)
    before = int(selected.get("restriction_minutes_before", _restriction_before(selected, cfg)))
    active = int(cfg["active_news_minutes"])
    stabilization_minutes = int(selected.get("stabilization_minutes", cfg["post_news_stabilization_minutes"]))
    # Positive when the event is still ahead of the current candle.
    minutes_to_news = _minutes_between(current, event_time)
    # Positive when the event has already happened.
    minutes_since_news = _minutes_between(event_time, current)
    restriction_type = "clear"
    restricted = False
    reason = "post_news_clear"

    if 0 < minutes_to_news <= before:
        restriction_type = "pre_news"
        restricted = True
        reason = "pre_news_restricted"
    elif 0 <= minutes_since_news <= active:
        restriction_type = "active_news"
        restricted = True
        reason = "active_news_restricted"
    elif active < minutes_since_news < stabilization_minutes:
        restriction_type = "post_news_stabilization"
        restricted = True
        reason = "post_news_stabilization_required"

    return {
        "restricted": restricted,
        "restriction_type": restriction_type,
        "event": selected,
        "event_name": selected.get("event_name", selected.get("name")),
        "impact_level": str(selected.get("impact_level", selected.get("impact", "unknown"))).lower(),
        "minutes_to_news": round(minutes_to_news, 2),
        "minutes_since_news": round(minutes_since_news, 2),
        "reason": reason,
    }


def detect_news_spike(
    df: Any,
    news_event: Mapping[str, Any] | None,
    spread_data: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect the immediate high-range news spike from closed candles."""

    cfg = _config(config)
    candles = _candles(df)
    if not candles or news_event is None:
        return _empty_spike("no_news_event_or_candles")

    event_time = _event_time(news_event)
    event_position = _first_position_at_or_after(candles, event_time)
    if event_position is None:
        return _empty_spike("no_closed_candle_at_or_after_news")

    lookahead = int(cfg["news_spike_lookahead_candles"])
    window = candles[event_position : event_position + max(1, lookahead)]
    if not window:
        return _empty_spike("no_spike_window")

    atr = _atr(candles[:event_position] or candles, int(cfg["atr_period"]))
    spike = max(window, key=lambda candle: candle.range)
    range_to_atr = spike.range / max(atr, 1e-9)
    spread = _spread(spread_data, cfg)
    avg_spread = _average_spread(spread_data, cfg)
    spread_status = _spread_status(spread, avg_spread, cfg)
    spike_detected = range_to_atr >= float(cfg["news_spike_atr_multiplier"]) or spread_status == "abnormal"
    candle_abnormal = range_to_atr >= float(cfg["max_news_candle_atr_multiplier"])

    return {
        "spike_detected": spike_detected,
        "spike_index": spike.index,
        "spike_position": spike.position,
        "spike_time": spike.timestamp.isoformat() if spike.timestamp else None,
        "range": round(spike.range, 8),
        "range_to_atr": round(range_to_atr, 4),
        "spread": spread,
        "average_spread": avg_spread,
        "spread_status": spread_status,
        "candle_range_status": "abnormal" if candle_abnormal else "elevated" if spike_detected else "normal",
        "candle_range_abnormal": candle_abnormal,
        "first_news_spike_entry_blocked": True,
        "rejection_reasons": ["first_news_spike_entry_blocked"] if spike_detected else [],
    }


def wait_for_post_news_stabilization(
    df: Any,
    news_event: Mapping[str, Any] | None,
    spread_data: Mapping[str, Any] | None = None,
    atr: float | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Check whether post-news spread and candle ranges have normalized."""

    cfg = _config(config)
    candles = _candles(df)
    if not candles or news_event is None:
        return _stabilization(False, ["no_news_event_or_candles"])

    event_time = _event_time(news_event)
    after = [candle for candle in candles if candle.timestamp is None or candle.timestamp >= event_time]
    if not after:
        return _stabilization(False, ["no_closed_candles_after_news"])

    min_candles = int(cfg["min_candles_after_news"])
    reasons: list[str] = []
    if len(after) < min_candles:
        reasons.append("post_news_not_stabilized")
        reasons.append("not_enough_closed_candles_after_news")

    current_time = candles[-1].timestamp
    minutes_since_news = _minutes_between(event_time, current_time) if current_time else 0.0
    min_minutes = int(news_event.get("stabilization_minutes", cfg["post_news_stabilization_minutes"]))
    if minutes_since_news < min_minutes:
        reasons.append("post_news_not_stabilized")
        reasons.append("minimum_stabilization_minutes_not_met")

    spread = _spread(spread_data, cfg)
    avg_spread = _average_spread(spread_data, cfg)
    spread_status = _spread_status(spread, avg_spread, cfg)
    if spread_status == "abnormal":
        reasons.append("spread_not_normalized")

    baseline_atr = float(atr or _atr(candles, int(cfg["atr_period"])))
    stable_window = after[-int(cfg["stable_range_window"]) :]
    avg_range = mean(candle.range for candle in stable_window)
    range_to_atr = avg_range / max(baseline_atr, 1e-9)
    if range_to_atr > float(cfg["stable_range_atr_multiplier"]):
        reasons.append("range_not_normalized")

    both_side_count = _count_both_side_news_ranges(after, baseline_atr, cfg)
    if both_side_count >= int(cfg["max_both_side_sweep_confusion"]):
        reasons.append("structure_unstable")
        reasons.append("both_side_sweep_confusion")

    stabilized = not reasons
    score = 10.0
    score -= 2.0 if "minimum_stabilization_minutes_not_met" in reasons else 0.0
    score -= 2.0 if "not_enough_closed_candles_after_news" in reasons else 0.0
    score -= 2.0 if "spread_not_normalized" in reasons else 0.0
    score -= 2.0 if "range_not_normalized" in reasons else 0.0
    score -= 2.0 if "structure_unstable" in reasons else 0.0

    return {
        "stabilized": stabilized,
        "stabilization_score": round(_clamp(score, 0, 10), 2),
        "candles_after_news": len(after),
        "minutes_since_news": round(minutes_since_news, 2),
        "spread_normalized": spread_status != "abnormal",
        "range_normalized": "range_not_normalized" not in reasons,
        "structure_stable": "structure_unstable" not in reasons,
        "first_stable_position": after[min(len(after) - 1, max(min_candles - 1, 0))].position,
        "range_to_atr": round(range_to_atr, 4),
        "spread_status": spread_status,
        "rejection_reasons": _unique(reasons),
    }


def detect_post_news_liquidity_sweep(
    df: Any,
    liquidity_pools: Sequence[Mapping[str, Any]],
    news_event: Mapping[str, Any] | None,
    stabilization_status: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect a news-driven sweep with later reclaim/rejection."""

    cfg = _config(config)
    candles = _candles(df)
    if not candles or news_event is None:
        return _empty_sweep("no_news_event_or_candles")
    if not stabilization_status.get("stabilized"):
        return _empty_sweep("post_news_not_stabilized")

    event_time = _event_time(news_event)
    after_news = [candle for candle in candles if candle.timestamp is None or candle.timestamp >= event_time]
    stable_position = int(
        stabilization_status.get("first_stable_position", after_news[0].position if after_news else 0)
    )
    buffer = float(cfg["sweep_buffer"])
    candidates: list[dict[str, Any]] = []

    for pool in liquidity_pools:
        side = _pool_side(pool)
        if side not in {"sell_side", "buy_side"}:
            continue
        zone_low = float(pool.get("zone_low", pool.get("price", 0.0)))
        zone_high = float(pool.get("zone_high", pool.get("price", zone_low)))
        if _target_is_swept(pool):
            continue
        if side == "sell_side":
            sweep_candles = [candle for candle in after_news if candle.low < zone_low - buffer]
            confirmation = next(
                (candle for candle in after_news if candle.position >= stable_position and candle.close > zone_low),
                None,
            )
            if sweep_candles and confirmation:
                extreme = min(candle.low for candle in sweep_candles)
                candidates.append(
                    _sweep_event(pool, "bullish", "sell_side", extreme, confirmation, sweep_candles[0], "reclaimed")
                )
        if side == "buy_side":
            sweep_candles = [candle for candle in after_news if candle.high > zone_high + buffer]
            confirmation = next(
                (candle for candle in after_news if candle.position >= stable_position and candle.close < zone_high),
                None,
            )
            if sweep_candles and confirmation:
                extreme = max(candle.high for candle in sweep_candles)
                candidates.append(
                    _sweep_event(pool, "bearish", "buy_side", extreme, confirmation, sweep_candles[0], "rejected")
                )

    if not candidates:
        return _empty_sweep("no_post_news_liquidity_sweep")
    sides = {item["swept_side"] for item in candidates}
    if len(sides) > 1:
        return _empty_sweep("both_side_sweep_confusion")

    candidates.sort(
        key=lambda item: (
            item["confirmation_position"],
            float(item.get("sweep_quality_score", 0.0)),
        ),
        reverse=True,
    )
    return candidates[0]


def detect_post_news_mss(
    df: Any,
    swings: Sequence[Mapping[str, Any]] | None,
    sweep_event: Mapping[str, Any] | None,
    stabilization_status: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect MSS after stabilization and post-news sweep confirmation."""

    cfg = _config(config)
    candles = _candles(df)
    if not candles or not sweep_event or not sweep_event.get("sweep_detected"):
        return _empty_mss("no_post_news_liquidity_sweep")
    if not stabilization_status.get("stabilized"):
        return _empty_mss("post_news_not_stabilized")

    direction = _direction(sweep_event.get("direction"))
    start_position = int(sweep_event.get("confirmation_position", stabilization_status.get("first_stable_position", 0)))
    break_buffer = float(cfg["mss_break_buffer"])
    max_wait = int(cfg["max_mss_wait_candles"])
    post = [candle for candle in candles if candle.position >= start_position]
    if len(post) < 2:
        return _empty_mss("no_post_news_mss")

    swing_level = _mss_reference_level(candles, swings or [], start_position, direction)
    for candle in post[: max_wait + 1]:
        if direction == "bullish" and candle.close > swing_level + break_buffer:
            return _mss_event("bullish", swing_level, candle, "bullish_mss")
        if direction == "bearish" and candle.close < swing_level - break_buffer:
            return _mss_event("bearish", swing_level, candle, "bearish_mss")
    return _empty_mss("no_post_news_mss")


def generate_news_sweep_signal(
    context: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a protected post-news liquidity sweep decision."""

    cfg = _config(config)
    candles = _candles(context.get("candles", context.get("df")))
    news_calendar = context.get("news_calendar", context.get("news_events", []))
    timestamp = context.get("timestamp") or (candles[-1].timestamp if candles else None)
    news_status = is_news_restricted_time(timestamp, news_calendar, cfg)
    news_event = context.get("news_event") or news_status.get("event")
    spread_data = _spread_data(context)
    spike = detect_news_spike(candles, news_event, spread_data, cfg)
    stabilization = wait_for_post_news_stabilization(candles, news_event, spread_data, None, cfg)

    reasons: list[str] = []
    if news_status.get("restriction_type") == "pre_news":
        reasons.append("pre_news_restricted")
    elif news_status.get("restriction_type") == "active_news":
        reasons.append("active_news_restricted")

    if not stabilization.get("stabilized"):
        reasons.append("post_news_not_stabilized")
        reasons.extend(stabilization.get("rejection_reasons", []))
    if spike.get("candle_range_abnormal") and not stabilization.get("stabilized"):
        reasons.append("candle_range_abnormal")
    if spike.get("spread_status") == "abnormal":
        reasons.append("spread_too_high")

    slippage = float(context.get("expected_slippage", cfg["expected_slippage"]))
    if slippage > float(cfg["max_allowed_slippage"]):
        reasons.append("slippage_too_high")

    sweep = detect_post_news_liquidity_sweep(
        candles,
        context.get("liquidity_pools", []),
        news_event,
        stabilization,
        cfg,
    )
    if not sweep.get("sweep_detected"):
        reasons.extend(sweep.get("rejection_reasons", ["no_post_news_liquidity_sweep"]))

    mss = detect_post_news_mss(candles, context.get("swings", []), sweep, stabilization, cfg)
    if not mss.get("mss_confirmed"):
        reasons.extend(mss.get("rejection_reasons", ["no_post_news_mss"]))

    displacement = _post_news_displacement(candles, mss, spike, context, cfg)
    if not displacement.get("confirmed"):
        reasons.extend(displacement.get("rejection_reasons", ["no_post_news_displacement"]))

    entry_poi = _entry_model(context, candles, mss, displacement, cfg)
    if not entry_poi.get("entry_poi_detected"):
        reasons.extend(entry_poi.get("rejection_reasons", ["no_valid_entry_poi"]))
    elif not entry_poi.get("retest_status") == "retested":
        reasons.append("waiting_for_retest")
    elif not entry_poi.get("reaction_confirmed", False):
        reasons.append("no_entry_confirmation")

    risk = _risk_plan(context, candles, sweep, entry_poi, spread_data, slippage, cfg)
    target = _select_target(
        context.get("liquidity_pools", []),
        mss.get("direction"),
        risk.get("entry_price"),
        context.get("htf_pois", context.get("poi_zones", [])),
        cfg,
    )
    if not target.get("target_valid"):
        reasons.extend(target.get("rejection_reasons", ["no_valid_target"]))
    rr_plan = _rr_plan(risk, target, cfg)
    if not rr_plan.get("rr_valid"):
        reasons.extend(rr_plan.get("rejection_reasons", ["rr_below_minimum"]))

    setup = {
        "news_status": news_status,
        "news_spike": spike,
        "stabilization": stabilization,
        "liquidity_sweep": sweep,
        "mss": mss,
        "displacement": displacement,
        "entry_poi": entry_poi,
        "risk": {**risk, **rr_plan},
        "target": target,
        "rejection_reasons": _unique(reasons),
    }
    score = score_news_sweep_setup(setup, context, cfg)
    reasons = _unique(reasons + score.get("rejection_reasons", []))
    status = _signal_status(reasons)

    return {
        "strategy": "news_liquidity_sweep",
        "symbol": context.get("symbol", "XAUUSD"),
        "signal_status": status,
        "trade_allowed": status == NewsSweepStatus.VALID.value,
        "direction": mss.get("direction", sweep.get("direction", "unknown")),
        "news_event": news_event,
        "news_status": news_status,
        "news_spike": spike,
        "stabilization": stabilization,
        "liquidity_sweep": sweep,
        "mss": mss,
        "displacement": displacement,
        "entry_poi": entry_poi,
        "target": target,
        "risk": {**risk, **rr_plan},
        "score": score,
        "rejection_reasons": reasons,
        "uses_closed_candles_only": True,
        "first_news_spike_entry_blocked": True,
    }


def score_news_sweep_setup(
    setup: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a post-news liquidity sweep setup from 0 to 10."""

    cfg = _config(config)
    context = context or {}
    reasons = list(setup.get("rejection_reasons", []))
    components: dict[str, float] = {}

    if setup.get("news_status", {}).get("restriction_type") in {"clear", "post_news_stabilization"}:
        components["news_restriction_compliance"] = 1.0
    if setup.get("stabilization", {}).get("stabilized"):
        components["post_news_stabilization"] = min(
            1.3, float(setup["stabilization"].get("stabilization_score", 0.0)) / 7
        )
    if setup.get("liquidity_sweep", {}).get("sweep_detected"):
        components["liquidity_sweep"] = min(1.3, float(setup["liquidity_sweep"].get("sweep_quality_score", 0.0)) / 7)
    if setup.get("mss", {}).get("mss_confirmed"):
        components["mss_confirmation"] = 1.2
    if setup.get("displacement", {}).get("confirmed"):
        components["displacement_quality"] = min(1.1, float(setup["displacement"].get("strength_score", 0.0)) / 8)
    if setup.get("entry_poi", {}).get("entry_poi_detected") and setup.get("entry_poi", {}).get("reaction_confirmed"):
        components["entry_poi_quality"] = 1.1
    if setup.get("target", {}).get("target_valid"):
        components["target_clarity"] = min(1.0, float(setup["target"].get("target_quality_score", 0.0)) / 8)
    if setup.get("risk", {}).get("rr_valid"):
        components["target_rr"] = min(1.1, float(setup["risk"].get("rr", 0.0)) / 2.2)
    if not any(reason in reasons for reason in {"spread_too_high", "slippage_too_high"}):
        components["spread_slippage_safety"] = 0.9
    if context.get("htf_bias"):
        components["htf_alignment"] = 0.5

    total = round(_clamp(sum(components.values()), 0, 10), 2)
    if total < float(cfg["minimum_setup_score"]):
        reasons.append("confirmation_score_below_minimum_threshold")

    return {
        "total_score": total,
        "minimum_required_score": float(cfg["minimum_setup_score"]),
        "grade": _grade(total),
        "trade_allowed": total >= float(cfg["minimum_setup_score"]) and not reasons,
        "component_scores": components,
        "rejection_reasons": _unique(reasons),
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
                    timestamp=_parse_time(_value(row, "timestamp", _value(row, "time", None))),
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
        "high_impact_restriction_minutes_before": 30,
        "medium_impact_restriction_minutes_before": 15,
        "active_news_minutes": 1,
        "post_news_stabilization_minutes": 15,
        "min_candles_after_news": 4,
        "news_spike_lookahead_candles": 2,
        "news_spike_atr_multiplier": 2.0,
        "max_news_candle_atr_multiplier": 4.0,
        "atr_period": 14,
        "stable_range_window": 3,
        "stable_range_atr_multiplier": 1.8,
        "max_both_side_sweep_confusion": 2,
        "sweep_buffer": 0.05,
        "mss_break_buffer": 0.05,
        "max_mss_wait_candles": 8,
        "min_body_to_range": 0.45,
        "displacement_min_range_to_atr": 0.65,
        "max_post_news_displacement_atr": 3.2,
        "max_spread": 0.6,
        "average_spread": 0.22,
        "spread_multiplier_limit": 3.0,
        "max_spread_to_target_ratio": 0.22,
        "expected_slippage": 0.25,
        "max_allowed_slippage": 0.7,
        "max_slippage_to_target_ratio": 0.20,
        "post_news_stop_atr_multiplier": 0.25,
        "spread_slippage_buffer_multiplier": 2.0,
        "minimum_target_distance": 1.0,
        "min_rr": 2.0,
        "normal_risk_percent": 1.0,
        "post_news_risk_multiplier": 0.25,
        "minimum_setup_score": 7.5,
        "blocker_quality_threshold": 7.5,
    }
    if config:
        defaults.update(dict(config))
    return defaults


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value is None:
        return None
    else:
        text = str(value).strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _event_time(event: Mapping[str, Any]) -> datetime:
    parsed = _parse_time(event.get("scheduled_time", event.get("time", event.get("timestamp"))))
    if parsed is None:
        raise ValueError("news event requires scheduled_time")
    return parsed


def _select_relevant_news_event(
    current: datetime,
    news_calendar: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    relevant: list[tuple[float, dict[str, Any]]] = []
    max_window = int(cfg["high_impact_restriction_minutes_before"]) + int(cfg["post_news_stabilization_minutes"]) + 120
    for event in news_calendar:
        impact = str(event.get("impact_level", event.get("impact", ""))).lower()
        currency = str(event.get("currency", "USD")).upper()
        if impact not in {"high", "medium", "medium/high", "critical"}:
            continue
        if currency not in {"USD", "XAU", "GLOBAL"}:
            continue
        try:
            event_time = _event_time(event)
        except ValueError:
            continue
        distance = abs(_minutes_between(current, event_time))
        if distance <= max_window:
            relevant.append((distance, dict(event)))
    relevant.sort(key=lambda item: item[0])
    return relevant[0][1] if relevant else None


def _restriction_before(event: Mapping[str, Any], cfg: Mapping[str, Any]) -> int:
    impact = str(event.get("impact_level", event.get("impact", "high"))).lower()
    if impact in {"high", "critical"}:
        return int(cfg["high_impact_restriction_minutes_before"])
    return int(cfg["medium_impact_restriction_minutes_before"])


def _minutes_between(start: datetime | None, end: datetime | None) -> float:
    if start is None or end is None:
        return 0.0
    return (end - start).total_seconds() / 60.0


def _first_position_at_or_after(candles: Sequence[_Candle], event_time: datetime) -> int | None:
    for candle in candles:
        if candle.timestamp is None or candle.timestamp >= event_time:
            return candle.position
    return None


def _spread_data(context: Mapping[str, Any]) -> dict[str, Any]:
    status = dict(context.get("spread_status", {}) or {})
    if "spread_points" in context:
        status["spread_points"] = context["spread_points"]
    if "current_spread" in context:
        status["current_spread"] = context["current_spread"]
    if "average_spread" in context:
        status["average_spread"] = context["average_spread"]
    return status


def _spread(spread_data: Mapping[str, Any] | None, cfg: Mapping[str, Any]) -> float:
    spread_data = spread_data or {}
    return float(spread_data.get("current_spread", spread_data.get("spread_points", cfg["average_spread"])) or 0.0)


def _average_spread(spread_data: Mapping[str, Any] | None, cfg: Mapping[str, Any]) -> float:
    spread_data = spread_data or {}
    return float(spread_data.get("average_spread", cfg["average_spread"]) or cfg["average_spread"])


def _spread_status(spread: float, avg_spread: float, cfg: Mapping[str, Any]) -> str:
    if spread > float(cfg["max_spread"]) or spread > avg_spread * float(cfg["spread_multiplier_limit"]):
        return "abnormal"
    if spread > avg_spread * 1.5:
        return "elevated"
    return "normal"


def _empty_spike(reason: str) -> dict[str, Any]:
    return {
        "spike_detected": False,
        "first_news_spike_entry_blocked": True,
        "range_to_atr": 0.0,
        "spread_status": "unknown",
        "candle_range_abnormal": False,
        "rejection_reasons": [reason],
    }


def _stabilization(stabilized: bool, reasons: Sequence[str]) -> dict[str, Any]:
    return {
        "stabilized": stabilized,
        "stabilization_score": 0.0 if reasons else 10.0,
        "candles_after_news": 0,
        "spread_normalized": False,
        "range_normalized": False,
        "structure_stable": False,
        "first_stable_position": 0,
        "rejection_reasons": _unique(reasons),
    }


def _count_both_side_news_ranges(
    candles: Sequence[_Candle],
    atr: float,
    cfg: Mapping[str, Any],
) -> int:
    threshold = atr * float(cfg["news_spike_atr_multiplier"])
    return sum(1 for candle in candles if candle.range >= threshold and candle.body_to_range < 0.35)


def _pool_side(pool: Mapping[str, Any]) -> str:
    raw = str(pool.get("side", pool.get("direction", ""))).lower()
    if raw in {"sell_side", "sellside", "ssl", "low", "equal_lows", "range_low", "asian_low", "pdl"}:
        return "sell_side"
    if raw in {"buy_side", "buyside", "bsl", "high", "equal_highs", "range_high", "asian_high", "pdh"}:
        return "buy_side"
    return raw


def _direction(value: Any) -> str:
    raw = str(value.value if isinstance(value, Enum) else value or "unknown").lower()
    if raw in {"bull", "buy", "long", "bullish", "buy_side", "bsl"}:
        return "bullish"
    if raw in {"bear", "sell", "short", "bearish", "sell_side", "ssl"}:
        return "bearish"
    return "unknown"


def _target_is_swept(pool: Mapping[str, Any]) -> bool:
    if bool(pool.get("swept", False)):
        return True
    return str(pool.get("swept_status", "unswept")).lower() in {
        "swept",
        "fully_swept",
        "cleared",
        "invalid",
        "consumed",
    }


def _sweep_event(
    pool: Mapping[str, Any],
    direction: str,
    swept_side: str,
    extreme: float,
    confirmation: _Candle,
    sweep_candle: _Candle,
    status: str,
) -> dict[str, Any]:
    return {
        "sweep_detected": True,
        "direction": direction,
        "swept_side": swept_side,
        "swept_liquidity_id": pool.get("liquidity_id", pool.get("id")),
        "liquidity_type": pool.get("liquidity_type", "unknown"),
        "swept_level": float(pool.get("price", (pool.get("zone_low", 0.0) + pool.get("zone_high", 0.0)) / 2)),
        "sweep_extreme": round(extreme, 8),
        "sweep_index": sweep_candle.index,
        "sweep_position": sweep_candle.position,
        "confirmation_index": confirmation.index,
        "confirmation_position": confirmation.position,
        "reclaim_status": f"{status}_after_stabilization" if direction == "bullish" else None,
        "rejection_status": f"{status}_after_stabilization" if direction == "bearish" else None,
        "sweep_quality_score": float(pool.get("quality_score", 8.0)),
        "news_related": True,
        "rejection_reasons": [],
    }


def _empty_sweep(reason: str) -> dict[str, Any]:
    return {
        "sweep_detected": False,
        "direction": "unknown",
        "swept_side": "unknown",
        "rejection_reasons": [reason],
    }


def _mss_reference_level(
    candles: Sequence[_Candle],
    swings: Sequence[Mapping[str, Any]],
    start_position: int,
    direction: str,
) -> float:
    if swings:
        side = "high" if direction == "bullish" else "low"
        candidates = [
            float(swing.get("price", swing.get(side, 0.0)))
            for swing in swings
            if str(swing.get("side", swing.get("type", side))).lower() in {side, f"swing_{side}"}
        ]
        if candidates:
            return max(candidates) if direction == "bullish" else min(candidates)
    window = [candle for candle in candles[max(0, start_position - 5) : start_position + 1]]
    if not window:
        return candles[start_position].close
    return max(candle.high for candle in window) if direction == "bullish" else min(candle.low for candle in window)


def _mss_event(direction: str, level: float, candle: _Candle, mss_type: str) -> dict[str, Any]:
    return {
        "mss_confirmed": True,
        "direction": direction,
        "type": mss_type,
        "broken_level": round(level, 8),
        "confirmation_index": candle.index,
        "confirmation_position": candle.position,
        "confirmed_by_close": True,
        "quality_score": 8.2,
        "rejection_reasons": [],
    }


def _empty_mss(reason: str) -> dict[str, Any]:
    return {
        "mss_confirmed": False,
        "direction": "unknown",
        "quality_score": 0.0,
        "rejection_reasons": [reason],
    }


def _post_news_displacement(
    candles: Sequence[_Candle],
    mss: Mapping[str, Any],
    spike: Mapping[str, Any],
    context: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    provided = context.get("displacement", {}) or {}
    if provided:
        confirmed = bool(provided.get("confirmed", False))
        return {
            **dict(provided),
            "confirmed": confirmed,
            "first_news_spike": bool(provided.get("first_news_spike", False)),
            "strength_score": float(provided.get("strength_score", 8.0 if confirmed else 0.0)),
            "rejection_reasons": [] if confirmed else ["no_post_news_displacement"],
        }
    if not mss.get("mss_confirmed"):
        return {"confirmed": False, "rejection_reasons": ["no_post_news_displacement"]}
    position = int(mss.get("confirmation_position", 0))
    if position >= len(candles):
        return {"confirmed": False, "rejection_reasons": ["no_post_news_displacement"]}
    candle = candles[position]
    atr = _atr(candles[:position] or candles, int(cfg["atr_period"]))
    range_to_atr = candle.range / max(atr, 1e-9)
    direction = _direction(mss.get("direction"))
    directional = candle.bullish if direction == "bullish" else candle.bearish
    is_first_spike = candle.index == spike.get("spike_index")
    reasons: list[str] = []
    if not directional or candle.body_to_range < float(cfg["min_body_to_range"]):
        reasons.append("no_post_news_displacement")
    if range_to_atr < float(cfg["displacement_min_range_to_atr"]):
        reasons.append("no_post_news_displacement")
    if is_first_spike:
        reasons.append("displacement_is_news_spike")
    if range_to_atr > float(cfg["max_post_news_displacement_atr"]):
        reasons.append("candle_range_abnormal")
    return {
        "confirmed": not reasons,
        "direction": direction,
        "confirmation_index": candle.index,
        "range_to_atr_ratio": round(range_to_atr, 4),
        "body_to_range_ratio": round(candle.body_to_range, 4),
        "first_news_spike": is_first_spike,
        "strength_score": round(_clamp(candle.body_to_range * 10, 0, 10), 2),
        "rejection_reasons": _unique(reasons),
    }


def _entry_model(
    context: Mapping[str, Any],
    candles: Sequence[_Candle],
    mss: Mapping[str, Any],
    displacement: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    provided = context.get("entry_poi", context.get("entry_model"))
    if isinstance(provided, Mapping):
        valid = bool(
            provided.get("entry_poi_detected", provided.get("entry_model_valid", provided.get("valid", False)))
        )
        return {
            **dict(provided),
            "entry_poi_detected": valid,
            "entry_price": float(provided.get("entry_price", provided.get("zone_mid", 0.0))),
            "retest_status": provided.get("retest_status", "retested" if valid else "not_retested"),
            "reaction_confirmed": bool(provided.get("reaction_confirmed", valid)),
            "rejection_reasons": [] if valid else ["no_valid_entry_poi"],
        }
    if not mss.get("mss_confirmed") or not displacement.get("confirmed"):
        return {"entry_poi_detected": False, "rejection_reasons": ["no_valid_entry_poi"]}
    position = int(mss.get("confirmation_position", 0))
    if position <= 0 or position >= len(candles):
        return {"entry_poi_detected": False, "rejection_reasons": ["no_valid_entry_poi"]}
    candle = candles[position]
    entry = (candle.open + candle.close) / 2.0
    return {
        "entry_poi_detected": True,
        "poi_type": f"{mss.get('direction')}_post_news_body_retest",
        "zone_low": round(min(candle.open, candle.close), 8),
        "zone_high": round(max(candle.open, candle.close), 8),
        "zone_mid": round(entry, 8),
        "entry_price": round(entry, 8),
        "created_after_stabilization": True,
        "retest_status": "retested",
        "reaction_confirmed": True,
        "rejection_reasons": [],
    }


def _risk_plan(
    context: Mapping[str, Any],
    candles: Sequence[_Candle],
    sweep: Mapping[str, Any],
    entry_poi: Mapping[str, Any],
    spread_data: Mapping[str, Any],
    slippage: float,
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    entry = float(entry_poi.get("entry_price", context.get("entry_price", candles[-1].close if candles else 0.0)))
    direction = _direction(sweep.get("direction"))
    atr = _atr(candles, int(cfg["atr_period"]))
    buffer = atr * float(cfg["post_news_stop_atr_multiplier"])
    buffer += (_spread(spread_data, cfg) + slippage) * float(cfg["spread_slippage_buffer_multiplier"])
    sweep_extreme = float(sweep.get("sweep_extreme", entry))
    stop = sweep_extreme - buffer if direction == "bullish" else sweep_extreme + buffer
    risk_percent = float(context.get("normal_risk_percent", cfg["normal_risk_percent"])) * float(
        cfg["post_news_risk_multiplier"]
    )
    return {
        "risk_mode": "reduced_post_news_risk",
        "risk_percent": round(risk_percent, 4),
        "entry_price": round(entry, 8),
        "stop_loss": round(stop, 8),
        "stop_reference": "beyond_news_sweep_extreme_with_wider_buffer",
        "post_news_buffer": round(buffer, 8),
    }


def _select_target(
    liquidity_pools: Sequence[Mapping[str, Any]],
    direction: str,
    entry: float,
    htf_pois: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    side = "buy_side" if direction == "bullish" else "sell_side" if direction == "bearish" else "unknown"
    candidates: list[dict[str, Any]] = []
    swept_target_seen = False
    for pool in liquidity_pools:
        if _pool_side(pool) != side:
            continue
        if _target_is_swept(pool):
            swept_target_seen = True
            continue
        price = _target_price(pool, direction)
        distance = price - entry if direction == "bullish" else entry - price
        reasons: list[str] = []
        if distance <= 0:
            reasons.append("target_not_in_expected_direction")
        if distance < float(cfg["minimum_target_distance"]):
            reasons.append("target_distance_insufficient")
        blockers = _htf_blockers_between(entry, price, direction, htf_pois, cfg)
        if blockers:
            reasons.append("htf_poi_blocks_target")
        candidate = dict(pool)
        candidate.update(
            {
                "target_valid": not reasons,
                "target_price": round(price, 8),
                "target_side": side,
                "distance_from_entry": round(max(distance, 0.0), 8),
                "target_quality_score": float(pool.get("quality_score", pool.get("target_priority_score", 7.0))),
                "blockers": blockers,
                "rejection_reasons": _unique(reasons),
            }
        )
        candidates.append(candidate)
    valid = [item for item in candidates if item.get("target_valid")]
    valid.sort(key=lambda item: (item["target_quality_score"], item["distance_from_entry"]), reverse=True)
    if valid:
        return valid[0]
    reasons = ["no_valid_target"]
    if swept_target_seen:
        reasons.append("target_already_swept")
    if candidates:
        reasons.extend(candidates[0].get("rejection_reasons", []))
    return {"target_valid": False, "rejection_reasons": _unique(reasons)}


def _target_price(pool: Mapping[str, Any], direction: str) -> float:
    if direction == "bullish":
        return float(pool.get("zone_high", pool.get("price", 0.0)))
    return float(pool.get("zone_low", pool.get("price", 0.0)))


def _htf_blockers_between(
    entry: float,
    target: float,
    direction: str,
    htf_pois: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
) -> list[dict[str, Any]]:
    blockers = []
    low, high = sorted([entry, target])
    opposing = "bearish" if direction == "bullish" else "bullish"
    for poi in htf_pois:
        poi_direction = _direction(poi.get("direction", poi.get("poi_type", poi.get("type"))))
        quality = float(poi.get("quality_score", 0.0) or 0.0)
        zone_low = float(poi.get("zone_low", poi.get("low", poi.get("price", 0.0))))
        zone_high = float(poi.get("zone_high", poi.get("high", poi.get("price", zone_low))))
        overlaps_path = max(low, zone_low) <= min(high, zone_high)
        if poi_direction == opposing and quality >= float(cfg["blocker_quality_threshold"]) and overlaps_path:
            blockers.append(dict(poi))
    return blockers


def _rr_plan(
    risk: Mapping[str, Any],
    target: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    if not target.get("target_valid"):
        return {"rr_valid": False, "rr": 0.0, "rejection_reasons": ["no_valid_target"]}
    entry = float(risk.get("entry_price", 0.0))
    stop = float(risk.get("stop_loss", entry))
    target_price = float(target.get("target_price", entry))
    direction = "bullish" if target.get("target_side") == "buy_side" else "bearish"
    risk_distance = entry - stop if direction == "bullish" else stop - entry
    reward_distance = target_price - entry if direction == "bullish" else entry - target_price
    reasons: list[str] = []
    if risk_distance <= 0 or reward_distance <= 0:
        reasons.append("invalid_risk_reward")
    rr = reward_distance / risk_distance if risk_distance > 0 else 0.0
    if rr < float(cfg["min_rr"]):
        reasons.append("rr_below_minimum")
    if risk_distance > reward_distance:
        reasons.append("stop_too_wide")
        reasons.append("target_distance_insufficient")
    return {
        "target": round(target_price, 8),
        "risk_distance": round(max(risk_distance, 0.0), 8),
        "reward_distance": round(max(reward_distance, 0.0), 8),
        "rr": round(rr, 4),
        "rr_valid": not reasons,
        "min_rr_required": float(cfg["min_rr"]),
        "rejection_reasons": _unique(reasons),
    }


def _signal_status(reasons: Sequence[str]) -> str:
    if not reasons:
        return NewsSweepStatus.VALID.value
    if "waiting_for_retest" in reasons and len(set(reasons)) == 1:
        return NewsSweepStatus.WAITING.value
    if any(reason in reasons for reason in {"pre_news_restricted", "active_news_restricted"}):
        return NewsSweepStatus.NO_TRADE.value
    return NewsSweepStatus.REJECTED.value


def _atr(candles: Sequence[_Candle], period: int = 14) -> float:
    if not candles:
        return 1.0
    window = candles[-max(1, period) :]
    return max(mean(candle.range for candle in window), 1e-9)


def _grade(score: float) -> str:
    if score >= 9:
        return "A+"
    if score >= 8:
        return "A"
    if score >= 7:
        return "B"
    if score >= 6:
        return "C"
    if score >= 5:
        return "D"
    return "F"


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
