"""Higher-timeframe POI + lower-timeframe confirmation strategy model.

HTF POIs provide location and bias only. A trade is valid only after lower
timeframe sweep, MSS, displacement, entry POI, target, RR, and execution
filters align. The module is pure Python and uses closed candles only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence


class HTFPOIDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"


class HTFPOIStatus(str, Enum):
    VALID = "valid"
    REJECTED = "rejected"
    WAITING_FOR_RETEST = "waiting_for_retest"
    CONTEXT_ONLY = "context_only"
    NO_TRADE = "no_trade"


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


def detect_htf_poi_zones(
    htf_df: Sequence[Mapping[str, Any] | Any] | Any,
    htf_swings: Sequence[Mapping[str, Any]] | None = None,
    htf_structure_events: Sequence[Mapping[str, Any]] | None = None,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Detect active HTF POI zones from closed higher-timeframe candles."""

    cfg = _config(config)
    candles = _closed_candles(htf_df)
    pois = _predefined_pois(htf_df, cfg)
    if candles:
        pois.extend(_detect_fvgs(candles, cfg))
        pois.extend(_detect_order_blocks(candles, htf_structure_events or [], cfg))
        pois.extend(_detect_supply_demand(candles, cfg))

    latest_close = candles[-1].close if candles else None
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for poi in pois:
        direction = _direction(_get(poi, "direction", "poi_direction", default="none"))
        zone_low, zone_high = _zone_bounds(poi)
        if direction is HTFPOIDirection.NONE or zone_high <= zone_low:
            continue
        poi_id = str(_get(poi, "poi_id", "id", default=f"HTF_POI_{len(normalized)}"))
        if poi_id in seen:
            continue
        invalidated = bool(poi.get("invalidated", False))
        if latest_close is not None:
            invalidated = invalidated or _poi_invalidated(direction, zone_low, zone_high, latest_close)
        quality = _htf_quality(poi, zone_low, zone_high, cfg)
        if quality < float(cfg["minimum_htf_poi_quality"]):
            continue
        seen.add(poi_id)
        normalized.append(
            {
                "poi_id": poi_id,
                "poi_type": str(_get(poi, "poi_type", "type", default="htf_poi")),
                "timeframe": str(_get(poi, "timeframe", default=cfg["htf_timeframe"])),
                "direction": direction.value,
                "zone_low": round(zone_low, 8),
                "zone_high": round(zone_high, 8),
                "zone_mid": round((zone_low + zone_high) / 2, 8),
                "created_at_index": int(_get(poi, "created_at_index", "index", default=0)),
                "created_at_time": _get(poi, "created_at_time", "timestamp", default=None),
                "created_by_event": str(_get(poi, "created_by_event", default="closed_htf_candle_structure")),
                "fresh_status": str(_get(poi, "fresh_status", default="fresh")),
                "mitigated_count": int(_get(poi, "mitigated_count", default=0)),
                "active_status": bool(_get(poi, "active_status", default=True)) and not invalidated,
                "invalidated": invalidated,
                "quality_score": round(quality, 2),
            }
        )
    return sorted(normalized, key=lambda item: item["quality_score"], reverse=True)


def map_htf_poi_to_ltf(
    htf_pois: Sequence[Mapping[str, Any]],
    ltf_df: Sequence[Mapping[str, Any] | Any] | Any,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Project HTF price zones onto lower-timeframe candles."""

    cfg = _config(config)
    candles = _closed_candles(ltf_df)
    mapped: list[dict[str, Any]] = []
    for poi in htf_pois:
        if bool(poi.get("invalidated", False)) or not bool(poi.get("active_status", True)):
            continue
        zone_low, zone_high = _zone_bounds(poi)
        overlaps = [c for c in candles if _overlaps(c, zone_low, zone_high)]
        width = zone_high - zone_low
        mapped.append(
            {
                "mapped_poi_id": f"MAP_{poi.get('poi_id', 'HTF_POI')}_{cfg['ltf_timeframe']}",
                "original_htf_poi_id": poi.get("poi_id", poi.get("id", "unknown")),
                "htf_poi": dict(poi),
                "htf_timeframe": poi.get("timeframe", cfg["htf_timeframe"]),
                "ltf_timeframe": cfg["ltf_timeframe"],
                "direction": _direction(poi.get("direction")).value,
                "zone_low": round(zone_low, 8),
                "zone_high": round(zone_high, 8),
                "zone_mid": round((zone_low + zone_high) / 2, 8),
                "zone_width": round(width, 8),
                "first_ltf_touch_index": overlaps[0].index if overlaps else None,
                "first_ltf_touch_time": overlaps[0].timestamp if overlaps else None,
                "candles_inside_zone": len(overlaps),
                "ltf_overlap_confirmed": bool(overlaps),
                "price_entered_zone": bool(overlaps),
                "active_status": bool(overlaps),
                "too_wide": width > float(cfg["max_htf_poi_width"]),
                "rejection_reasons": (
                    ["htf_poi_too_wide_without_ltf_refinement"] if width > float(cfg["max_htf_poi_width"]) else []
                ),
            }
        )
    return mapped


def detect_ltf_sweep_inside_htf_poi(
    ltf_df: Sequence[Mapping[str, Any] | Any] | Any,
    ltf_liquidity_pools: Sequence[Mapping[str, Any]],
    mapped_poi: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Detect LTF liquidity sweep inside or near the mapped HTF POI."""

    cfg = _config(config)
    candles = _closed_candles(ltf_df)
    direction = _direction(mapped_poi.get("direction"))
    if direction is HTFPOIDirection.NONE or not mapped_poi.get("price_entered_zone"):
        return None

    atr = _atr(candles, int(cfg["atr_period"]))
    buffer = _buffer(cfg, "sweep_buffer", atr * float(cfg["sweep_buffer_atr_multiplier"]))
    tolerance = _buffer(cfg, "poi_tolerance", atr * float(cfg["poi_tolerance_atr_multiplier"]))
    first_touch = mapped_poi.get("first_ltf_touch_index")
    touch_index = int(first_touch) if first_touch is not None else 0
    zone_low, zone_high = _zone_bounds(mapped_poi)
    wanted_side = "sell_side" if direction is HTFPOIDirection.BULLISH else "buy_side"
    candidates: list[dict[str, Any]] = []

    for pool in ltf_liquidity_pools or []:
        if _pool_side(pool) != wanted_side or _pool_swept(pool):
            continue
        pool_low, pool_high = _zone_bounds(pool)
        pool_id = str(_get(pool, "liquidity_id", "pool_id", "id", default=f"LTF_{wanted_side}"))
        for candle in candles:
            if candle.index < touch_index:
                continue
            reference = candle.low if wanted_side == "sell_side" else candle.high
            near_poi = _price_near_zone(reference, zone_low, zone_high, tolerance) or _overlaps(
                candle, zone_low - tolerance, zone_high + tolerance
            )
            if not near_poi:
                continue
            if direction is HTFPOIDirection.BULLISH and candle.low < pool_low - buffer and candle.close > pool_low:
                candidates.append(_sweep(direction, wanted_side, pool_id, pool_low, pool_high, candle, atr, mapped_poi))
            if direction is HTFPOIDirection.BEARISH and candle.high > pool_high + buffer and candle.close < pool_high:
                candidates.append(_sweep(direction, wanted_side, pool_id, pool_low, pool_high, candle, atr, mapped_poi))
    return max(candidates, key=lambda item: (item["quality_score"], item["sweep_index"]), default=None)


def detect_ltf_mss_inside_htf_poi(
    ltf_df: Sequence[Mapping[str, Any] | Any] | Any,
    ltf_swings: Sequence[Mapping[str, Any]] | None,
    sweep_event: Mapping[str, Any] | None,
    mapped_poi: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Detect LTF MSS after the POI sweep by candle close."""

    if not sweep_event:
        return None
    cfg = _config(config)
    candles = _closed_candles(ltf_df)
    direction = _direction(sweep_event.get("direction"))
    if direction is HTFPOIDirection.NONE or direction is not _direction(mapped_poi.get("direction")):
        return None

    atr = _atr(candles, int(cfg["atr_period"]))
    break_buffer = _buffer(cfg, "break_buffer", atr * float(cfg["break_buffer_atr_multiplier"]))
    sweep_index = int(sweep_event["sweep_index"])
    wait = int(cfg["max_mss_wait_candles"])
    wanted = "high" if direction is HTFPOIDirection.BULLISH else "low"
    swings = [
        s
        for s in _swings(candles, ltf_swings)
        if s["kind"] == wanted and sweep_index < int(s["index"]) <= sweep_index + wait
    ]
    for swing in sorted(swings, key=lambda item: int(item["index"])):
        level = float(swing["price"])
        for candle in candles:
            if candle.index <= int(swing["index"]) or candle.index > sweep_index + wait:
                continue
            broke = (
                candle.close > level + break_buffer
                if direction is HTFPOIDirection.BULLISH
                else candle.close < level - break_buffer
            )
            if broke:
                return {
                    "mss_confirmed": True,
                    "direction": direction.value,
                    "broken_swing_id": swing.get("swing_id", swing.get("id", f"SWING_{swing['index']}")),
                    "broken_level": round(level, 8),
                    "confirmation_index": candle.index,
                    "confirmation_time": candle.timestamp,
                    "confirmed_by_close": True,
                    "inside_or_near_htf_poi": _overlaps(
                        candle,
                        float(mapped_poi["zone_low"]) - atr,
                        float(mapped_poi["zone_high"]) + atr,
                    ),
                    "quality_score": round(_clamp(7 + candle.body_to_range * 1.8, 0, 10), 2),
                }
    return None


def detect_ltf_fvg_or_ob_entry(
    ltf_df: Sequence[Mapping[str, Any] | Any] | Any,
    mss_event: Mapping[str, Any] | None,
    displacement_event: Mapping[str, Any] | None,
    mapped_poi: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Find the best LTF FVG/OB entry zone created by displacement."""

    if not mss_event or not displacement_event or not displacement_event.get("confirmed"):
        return None
    cfg = _config(config)
    candles = _closed_candles(ltf_df)
    direction = _direction(mss_event.get("direction"))
    displacement_index = int(displacement_event["confirmation_index"])
    candidates = _entry_fvgs(candles, direction, displacement_index, mapped_poi, cfg)
    candidates.extend(_entry_obs(candles, direction, displacement_index, mapped_poi, cfg))
    return max(candidates, key=lambda item: (item["quality_score"], item["retest_status"] == "retested"), default=None)


def generate_htf_poi_ltf_confirmation_signal(
    context: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full HTF POI + LTF confirmation sequence."""

    cfg = _config(config)
    symbol = str(context.get("symbol", "XAUUSD"))
    ltf = _closed_candles(_get(context, "ltf_df", "ltf_candles", "df", "candles", default=[]))
    reasons: list[str] = []
    if bool((context.get("news_status") or {}).get("restricted", False)):
        reasons.append("news_restricted")
    if not _spread_safe(context.get("spread_status", {}) or {}, cfg):
        reasons.append("spread_too_high")
    if not ltf:
        return _empty_signal(symbol, ["no_ltf_candles"], HTFPOIStatus.NO_TRADE.value)

    provided_pois = context.get("htf_poi_zones") or context.get("poi_zones")
    htf_source = provided_pois if provided_pois is not None else _get(context, "htf_df", "htf_candles", default=[])
    htf_pois = detect_htf_poi_zones(htf_source, context.get("htf_swings"), context.get("htf_structure_events"), cfg)
    if not htf_pois:
        return _empty_signal(symbol, _unique(reasons + ["no_active_htf_poi"]), HTFPOIStatus.NO_TRADE.value)

    mapped_pois = map_htf_poi_to_ltf(htf_pois, ltf, cfg)
    if not mapped_pois:
        return _empty_signal(symbol, _unique(reasons + ["price_not_inside_htf_poi"]), HTFPOIStatus.CONTEXT_ONLY.value)

    candidates: list[dict[str, Any]] = []
    for mapped in mapped_pois:
        local_reasons = list(reasons) + list(mapped.get("rejection_reasons", []))
        if not mapped.get("price_entered_zone"):
            local_reasons.append("price_not_inside_htf_poi")
            candidates.append(_candidate(symbol, mapped, None, None, None, None, None, local_reasons, context, cfg))
            continue
        sweep = detect_ltf_sweep_inside_htf_poi(ltf, context.get("ltf_liquidity_pools", []), mapped, cfg)
        if not sweep:
            local_reasons.extend(["no_ltf_sweep_inside_htf_poi", "htf_poi_touch_alone_not_tradeable"])
            candidates.append(_candidate(symbol, mapped, None, None, None, None, None, local_reasons, context, cfg))
            continue
        mss = detect_ltf_mss_inside_htf_poi(ltf, context.get("ltf_swings"), sweep, mapped, cfg)
        if not mss:
            local_reasons.append("no_ltf_mss_inside_htf_poi")
            candidates.append(_candidate(symbol, mapped, sweep, None, None, None, None, local_reasons, context, cfg))
            continue
        displacement = _displacement(ltf, mss, cfg)
        if not displacement:
            local_reasons.append("no_ltf_displacement")
            candidates.append(_candidate(symbol, mapped, sweep, mss, None, None, None, local_reasons, context, cfg))
            continue
        entry = detect_ltf_fvg_or_ob_entry(ltf, mss, displacement, mapped, cfg)
        if not entry:
            local_reasons.append("no_ltf_entry_poi")
            candidates.append(
                _candidate(symbol, mapped, sweep, mss, displacement, None, None, local_reasons, context, cfg)
            )
            continue
        if entry.get("retest_status") != "retested":
            local_reasons.append("waiting_for_ltf_retest")
        target = _target(context.get("htf_liquidity_targets", context.get("liquidity_pools", [])), mapped, entry, cfg)
        if not target:
            local_reasons.append("no_valid_htf_target")
        risk = _risk(mapped, sweep, entry, target, ltf, context.get("spread_status", {}) or {}, cfg)
        if risk["risk_distance"] <= 0 or risk["reward_distance"] <= 0:
            local_reasons.append("invalid_risk_reward")
        if risk["rr"] < float(cfg["min_rr"]):
            local_reasons.append("rr_below_minimum")
        if _direction_conflict(context, mapped, mss):
            local_reasons.extend(["ltf_signal_conflicts_with_htf_poi", "htf_poi_override"])
        candidates.append(
            _candidate(symbol, mapped, sweep, mss, displacement, entry, target, local_reasons, context, cfg, risk)
        )

    best = max(candidates, key=lambda item: (item["score"]["total_score"], item.get("risk", {}).get("rr", 0)))
    if best["trade_allowed"]:
        best["signal_status"] = HTFPOIStatus.VALID.value
    elif "waiting_for_ltf_retest" in best["rejection_reasons"] and len(best["rejection_reasons"]) == 1:
        best["signal_status"] = HTFPOIStatus.WAITING_FOR_RETEST.value
    elif "htf_poi_touch_alone_not_tradeable" in best["rejection_reasons"]:
        best["signal_status"] = HTFPOIStatus.CONTEXT_ONLY.value
    else:
        best["signal_status"] = HTFPOIStatus.REJECTED.value
    return best


def score_htf_poi_ltf_setup(
    setup: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score HTF POI quality plus LTF execution confirmation from 0 to 10."""

    cfg = _config(config)
    context = context or {}
    reasons = list(setup.get("rejection_reasons", []))
    htf_poi = setup.get("htf_poi", {}) or {}
    sweep = setup.get("ltf_sweep", {}) or {}
    mss = setup.get("ltf_mss", {}) or {}
    displacement = setup.get("ltf_displacement", {}) or {}
    entry = setup.get("ltf_entry_poi", {}) or {}
    risk = setup.get("risk", {}) or {}
    direction = _direction(setup.get("direction"))
    htf_bias = _direction(_context_bias(context))
    location = str(_get(context, "price_location", "premium_discount", default="unknown")).lower()

    component_scores = {
        "htf_poi_quality": _clamp(float(htf_poi.get("quality_score", 0.0)), 0, 10),
        "htf_bias_premium_discount": _clamp(
            6.5
            + (1.8 if htf_bias is direction else 0)
            + (1.0 if (direction is HTFPOIDirection.BULLISH and location == "discount") else 0)
            + (1.0 if (direction is HTFPOIDirection.BEARISH and location == "premium") else 0),
            0,
            10,
        ),
        "price_interaction": 8.5 if setup.get("mapped_poi", {}).get("price_entered_zone") else 0.0,
        "ltf_sweep": _clamp(float(sweep.get("quality_score", 0.0)), 0, 10),
        "ltf_mss": _clamp(float(mss.get("quality_score", 0.0)), 0, 10),
        "ltf_displacement": _clamp(float(displacement.get("strength_score", 0.0)), 0, 10),
        "ltf_entry_poi": _clamp(float(entry.get("quality_score", 0.0)), 0, 10),
        "target_rr": _clamp(5.5 + min(float(risk.get("rr", 0.0)), 5.0), 0, 10) if setup.get("target") else 0.0,
        "xauusd_safety": 0.0 if {"news_restricted", "spread_too_high"} & set(reasons) else 9.0,
        "nested_confluence": 7.0 + min(len(context.get("nested_pois") or []), 3) * 0.6,
    }
    weights = {
        "htf_poi_quality": 1.1,
        "htf_bias_premium_discount": 0.9,
        "price_interaction": 0.9,
        "ltf_sweep": 1.1,
        "ltf_mss": 1.1,
        "ltf_displacement": 1.0,
        "ltf_entry_poi": 1.0,
        "target_rr": 1.2,
        "xauusd_safety": 0.9,
        "nested_confluence": 0.8,
    }
    total = sum(component_scores[k] * weights[k] for k in weights) / sum(weights.values())
    hard_filters = [
        r
        for r in _unique(reasons)
        if r
        in {
            "no_active_htf_poi",
            "htf_poi_invalidated",
            "no_ltf_sweep_inside_htf_poi",
            "no_ltf_mss_inside_htf_poi",
            "no_ltf_displacement",
            "no_ltf_entry_poi",
            "no_valid_htf_target",
            "target_already_swept",
            "rr_below_minimum",
            "invalid_risk_reward",
            "spread_too_high",
            "news_restricted",
            "target_blocked_by_htf_poi",
            "ltf_signal_conflicts_with_htf_poi",
            "htf_poi_override",
            "htf_poi_too_wide_without_ltf_refinement",
        }
    ]
    trade_allowed = total >= float(cfg["minimum_setup_score"]) and not hard_filters
    return {
        "total_score": round(_clamp(total, 0, 10), 2),
        "component_scores": {k: round(v, 2) for k, v in component_scores.items()},
        "grade": _grade(total),
        "trade_allowed": trade_allowed,
        "hard_filter_failures": hard_filters,
        "warnings": ["institutional_quality_below_a_grade"] if total < 8 else [],
    }


def _candidate(
    symbol: str,
    mapped: Mapping[str, Any],
    sweep: Mapping[str, Any] | None,
    mss: Mapping[str, Any] | None,
    displacement: Mapping[str, Any] | None,
    entry: Mapping[str, Any] | None,
    target: Mapping[str, Any] | None,
    reasons: Sequence[str],
    context: Mapping[str, Any],
    cfg: Mapping[str, Any],
    risk: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    direction = _direction(mapped.get("direction"))
    payload = {
        "strategy": "HTF POI + LTF Confirmation",
        "symbol": symbol,
        "signal_id": f"{symbol}_HTF_POI_LTF_{direction.value.upper()}_{mapped.get('original_htf_poi_id', 'POI')}",
        "signal_status": HTFPOIStatus.REJECTED.value,
        "direction": direction.value,
        "timeframe_stack": {
            "htf_poi_timeframe": mapped.get("htf_timeframe", cfg["htf_timeframe"]),
            "ltf_confirmation_timeframe": mapped.get("ltf_timeframe", cfg["ltf_timeframe"]),
            "entry_timeframe": mapped.get("ltf_timeframe", cfg["ltf_timeframe"]),
        },
        "htf_context": {
            "htf_bias": _context_bias(context),
            "price_location": _get(context, "price_location", "premium_discount", default="unknown"),
            "draw_on_liquidity": "buy_side" if direction is HTFPOIDirection.BULLISH else "sell_side",
        },
        "htf_poi": dict(mapped.get("htf_poi", {})),
        "mapped_poi": dict(mapped),
        "ltf_sweep": dict(sweep or {}),
        "ltf_mss": dict(mss or {}),
        "ltf_displacement": dict(displacement or {}),
        "ltf_entry_poi": dict(entry or {}),
        "entry": _entry_payload(entry, direction),
        "target": dict(target or {}),
        "risk": dict(risk or _empty_risk(cfg)),
        "filters": {
            "news_filter": "failed" if "news_restricted" in reasons else "passed",
            "spread_filter": "failed" if "spread_too_high" in reasons else "passed",
            "htf_poi_width_filter": "failed" if "htf_poi_too_wide_without_ltf_refinement" in reasons else "passed",
            "target_swept_filter": "failed" if "target_already_swept" in reasons else "passed",
        },
        "rejection_reasons": _unique(reasons),
    }
    payload["score"] = score_htf_poi_ltf_setup(payload, context, cfg)
    payload["trade_allowed"] = payload["score"]["trade_allowed"]
    return payload


def _empty_signal(symbol: str, reasons: Sequence[str], status: str) -> dict[str, Any]:
    return {
        "strategy": "HTF POI + LTF Confirmation",
        "symbol": symbol,
        "signal_status": status,
        "direction": "none",
        "trade_allowed": False,
        "rejection_reasons": _unique(reasons),
        "score": {"total_score": 0.0, "grade": "F", "trade_allowed": False, "component_scores": {}},
    }


def _displacement(candles: Sequence[_Candle], mss: Mapping[str, Any], cfg: Mapping[str, Any]) -> dict[str, Any] | None:
    direction = _direction(mss.get("direction"))
    atr = _atr(candles, int(cfg["atr_period"]))
    start = int(mss["confirmation_index"])
    for candle in candles:
        if candle.index < start or candle.index > start + int(cfg["max_displacement_wait_candles"]):
            continue
        directional = candle.bullish if direction is HTFPOIDirection.BULLISH else candle.bearish
        close_pos = (
            candle.bullish_close_position if direction is HTFPOIDirection.BULLISH else candle.bearish_close_position
        )
        range_to_atr = candle.range / max(atr, 1e-9)
        if (
            directional
            and candle.body_to_range >= float(cfg["displacement_min_body_to_range"])
            and range_to_atr >= float(cfg["displacement_min_range_to_atr"])
            and close_pos >= float(cfg["displacement_min_close_position"])
        ):
            return {
                "direction": direction.value,
                "confirmed": True,
                "confirmation_index": candle.index,
                "confirmation_time": candle.timestamp,
                "body_to_range_ratio": round(candle.body_to_range, 4),
                "range_to_atr_ratio": round(range_to_atr, 4),
                "close_position_score": round(close_pos, 4),
                "strength_score": round(
                    _clamp(6.5 + candle.body_to_range * 1.7 + min(range_to_atr, 3) * 0.7, 0, 10), 2
                ),
            }
    return None


def _entry_fvgs(
    candles: Sequence[_Candle],
    direction: HTFPOIDirection,
    displacement_index: int,
    mapped: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> list[dict[str, Any]]:
    out = []
    for i in range(2, len(candles)):
        c1, c3 = candles[i - 2], candles[i]
        if c3.index < displacement_index or c3.index > displacement_index + int(cfg["max_entry_scan_candles"]):
            continue
        if direction is HTFPOIDirection.BULLISH and c1.high < c3.low:
            entry = _entry("bullish_fvg", direction, c1.high, c3.low, c3, mapped, candles, cfg)
            if entry:
                out.append(entry)
        if direction is HTFPOIDirection.BEARISH and c1.low > c3.high:
            entry = _entry("bearish_fvg", direction, c3.high, c1.low, c3, mapped, candles, cfg)
            if entry:
                out.append(entry)
    return out


def _entry_obs(
    candles: Sequence[_Candle],
    direction: HTFPOIDirection,
    displacement_index: int,
    mapped: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> list[dict[str, Any]]:
    opposite = [
        c
        for c in candles
        if c.index < displacement_index and (c.bearish if direction is HTFPOIDirection.BULLISH else c.bullish)
    ]
    if not opposite:
        return []
    c = opposite[-1]
    poi_type = "bullish_order_block" if direction is HTFPOIDirection.BULLISH else "bearish_order_block"
    entry = _entry(
        poi_type, direction, min(c.open, c.close, c.low), max(c.open, c.close, c.high), c, mapped, candles, cfg
    )
    return [entry] if entry else []


def _entry(
    poi_type: str,
    direction: HTFPOIDirection,
    zone_low: float,
    zone_high: float,
    created_by: _Candle,
    mapped: Mapping[str, Any],
    candles: Sequence[_Candle],
    cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    low, high = sorted((zone_low, zone_high))
    width = high - low
    if width < float(cfg["min_ltf_entry_poi_size"]) or width > float(cfg["max_ltf_entry_poi_width"]):
        return None
    mid = (low + high) / 2
    retest = next((c for c in candles if c.index > created_by.index and _overlaps(c, low, high)), None)
    reaction = bool(retest and ((retest.close > mid) if direction is HTFPOIDirection.BULLISH else (retest.close < mid)))
    overlap_bonus = 1.0 if _zones_overlap(low, high, float(mapped["zone_low"]), float(mapped["zone_high"])) else 0.0
    quality = _clamp(7 + overlap_bonus + (0.8 if retest else 0) + (0.7 if reaction else 0), 0, 10)
    return {
        "entry_poi_detected": True,
        "poi_type": poi_type,
        "direction": direction.value,
        "zone_low": round(low, 8),
        "zone_high": round(high, 8),
        "zone_mid": round(mid, 8),
        "mean_threshold": round(mid, 8),
        "created_by_displacement": True,
        "created_at_index": created_by.index,
        "created_at_time": created_by.timestamp,
        "retest_status": "retested" if retest else "waiting_for_retest",
        "retest_index": retest.index if retest else None,
        "reaction_confirmed": reaction,
        "entry_price": round(mid, 8),
        "quality_score": round(quality, 2),
    }


def _target(
    targets: Sequence[Mapping[str, Any]],
    mapped: Mapping[str, Any],
    entry: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    direction = _direction(mapped.get("direction"))
    wanted = "buy_side" if direction is HTFPOIDirection.BULLISH else "sell_side"
    entry_price = float(entry.get("entry_price", 0.0))
    candidates = []
    for target in targets or []:
        if _pool_side(target) != wanted or _pool_swept(target):
            continue
        low, high = _zone_bounds(target)
        price = high if direction is HTFPOIDirection.BULLISH else low
        if direction is HTFPOIDirection.BULLISH and price <= entry_price + float(cfg["minimum_target_distance"]):
            continue
        if direction is HTFPOIDirection.BEARISH and price >= entry_price - float(cfg["minimum_target_distance"]):
            continue
        enriched = dict(target)
        enriched.update(
            {
                "target_id": target.get("target_id", target.get("liquidity_id", target.get("id", "target"))),
                "target_side": wanted,
                "target_price": round(price, 8),
                "target_reference": target.get("liquidity_type", f"htf_{wanted}_liquidity"),
            }
        )
        candidates.append(enriched)
    return min(candidates, key=lambda item: abs(float(item["target_price"]) - entry_price), default=None)


def _risk(
    mapped: Mapping[str, Any],
    sweep: Mapping[str, Any],
    entry: Mapping[str, Any],
    target: Mapping[str, Any] | None,
    candles: Sequence[_Candle],
    spread_status: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    direction = _direction(mapped.get("direction"))
    entry_price = float(entry.get("entry_price", 0.0))
    target_price = float(target.get("target_price", entry_price)) if target else entry_price
    atr = _atr(candles, int(cfg["atr_period"]))
    buffer = atr * float(cfg["stop_atr_buffer_multiplier"]) + _spread(spread_status, cfg) * float(
        cfg["spread_buffer_multiplier"]
    )
    if direction is HTFPOIDirection.BULLISH:
        stop = float(sweep.get("sweep_extreme", entry_price)) - buffer
        risk_distance = entry_price - stop
        reward_distance = target_price - entry_price
    elif direction is HTFPOIDirection.BEARISH:
        stop = float(sweep.get("sweep_extreme", entry_price)) + buffer
        risk_distance = stop - entry_price
        reward_distance = entry_price - target_price
    else:
        stop = entry_price
        risk_distance = 0.0
        reward_distance = 0.0
    return {
        "stop_loss": round(stop, 8),
        "stop_reference": "ltf_sweep_with_atr_and_spread_buffer",
        "target": round(target_price, 8),
        "target_reference": target.get("target_reference", "none") if target else "none",
        "risk_distance": round(risk_distance, 8),
        "reward_distance": round(reward_distance, 8),
        "rr": round(reward_distance / risk_distance, 4) if risk_distance > 0 else 0.0,
        "min_rr_required": float(cfg["min_rr"]),
    }


def _empty_risk(cfg: Mapping[str, Any]) -> dict[str, Any]:
    return {"stop_loss": 0.0, "target": 0.0, "risk_distance": 0.0, "reward_distance": 0.0, "rr": 0.0}


def _sweep(
    direction: HTFPOIDirection,
    side: str,
    pool_id: str,
    pool_low: float,
    pool_high: float,
    candle: _Candle,
    atr: float,
    mapped: Mapping[str, Any],
) -> dict[str, Any]:
    extreme = candle.low if side == "sell_side" else candle.high
    level = pool_low if side == "sell_side" else pool_high
    depth = abs(extreme - level)
    return {
        "sweep_detected": True,
        "direction": direction.value,
        "swept_side": side,
        "swept_liquidity_id": pool_id,
        "swept_level": round(level, 8),
        "sweep_extreme": round(extreme, 8),
        "sweep_low": candle.low,
        "sweep_high": candle.high,
        "sweep_index": candle.index,
        "sweep_time": candle.timestamp,
        "inside_htf_poi": _price_near_zone(extreme, float(mapped["zone_low"]), float(mapped["zone_high"]), atr),
        "reclaim_status": "reclaimed" if direction is HTFPOIDirection.BULLISH else None,
        "rejection_status": "rejected" if direction is HTFPOIDirection.BEARISH else None,
        "quality_score": round(_clamp(6.8 + min(depth / max(atr, 1e-9), 2.0) + candle.body_to_range, 0, 10), 2),
    }


def _direction_conflict(context: Mapping[str, Any], mapped: Mapping[str, Any], mss: Mapping[str, Any] | None) -> bool:
    if not mss:
        return False
    htf_bias = _direction(_context_bias(context))
    direction = _direction(mapped.get("direction"))
    return htf_bias is not HTFPOIDirection.NONE and direction is not HTFPOIDirection.NONE and htf_bias is not direction


def _entry_payload(entry: Mapping[str, Any] | None, direction: HTFPOIDirection) -> dict[str, Any]:
    if not entry:
        return {"entry_triggered": False}
    return {
        "entry_triggered": entry.get("retest_status") == "retested" and bool(entry.get("reaction_confirmed")),
        "entry_type": f"ltf_{entry.get('poi_type', 'poi')}_midpoint_reaction_entry",
        "entry_price": entry.get("entry_price"),
        "entry_time": entry.get("retest_index"),
        "direction": direction.value,
    }


def _predefined_pois(source: Any, cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(source, Sequence) or isinstance(source, (str, bytes, dict)):
        return []
    out = []
    for item in source:
        if _get(item, "poi_id", "poi_type", default=None) is None:
            continue
        if _get(item, "zone_low", default=None) is None or _get(item, "zone_high", default=None) is None:
            continue
        poi = dict(item) if isinstance(item, Mapping) else dict(vars(item))
        poi.setdefault("timeframe", cfg["htf_timeframe"])
        out.append(poi)
    return out


def _detect_fvgs(candles: Sequence[_Candle], cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i in range(2, len(candles)):
        c1, c3 = candles[i - 2], candles[i]
        if c1.high < c3.low and c3.low - c1.high >= float(cfg["min_htf_fvg_size"]):
            out.append(
                _poi(
                    f"{cfg['htf_timeframe']}_BULLISH_FVG_{c3.index}", "bullish_fvg", "bullish", c1.high, c3.low, c3, cfg
                )
            )
        if c1.low > c3.high and c1.low - c3.high >= float(cfg["min_htf_fvg_size"]):
            out.append(
                _poi(
                    f"{cfg['htf_timeframe']}_BEARISH_FVG_{c3.index}", "bearish_fvg", "bearish", c3.high, c1.low, c3, cfg
                )
            )
    return out


def _detect_order_blocks(
    candles: Sequence[_Candle],
    events: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
) -> list[dict[str, Any]]:
    out = []
    for event in events:
        direction = _direction(_get(event, "direction", "bias", default="none"))
        index = int(_get(event, "index", "confirmation_index", default=-1))
        opposite = [
            c for c in candles if c.index < index and (c.bearish if direction is HTFPOIDirection.BULLISH else c.bullish)
        ]
        if opposite:
            c = opposite[-1]
            out.append(
                _poi(
                    f"{cfg['htf_timeframe']}_{direction.value.upper()}_OB_{c.index}",
                    f"{direction.value}_order_block",
                    direction.value,
                    c.low,
                    c.high,
                    c,
                    cfg,
                    quality=8.2,
                )
            )
    return out


def _detect_supply_demand(candles: Sequence[_Candle], cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    atr = _atr(candles, int(cfg["atr_period"]))
    for left, right in zip(candles, candles[1:]):
        if right.range / max(atr, 1e-9) < float(cfg["htf_departure_range_to_atr"]):
            continue
        if left.bearish and right.bullish:
            out.append(
                _poi(
                    f"{cfg['htf_timeframe']}_DEMAND_{left.index}",
                    "demand_zone",
                    "bullish",
                    left.low,
                    left.open,
                    left,
                    cfg,
                    quality=6.8,
                )
            )
        if left.bullish and right.bearish:
            out.append(
                _poi(
                    f"{cfg['htf_timeframe']}_SUPPLY_{left.index}",
                    "supply_zone",
                    "bearish",
                    left.open,
                    left.high,
                    left,
                    cfg,
                    quality=6.8,
                )
            )
    return out


def _poi(
    poi_id: str,
    poi_type: str,
    direction: str,
    low: float,
    high: float,
    candle: _Candle,
    cfg: Mapping[str, Any],
    quality: float = 7.4,
) -> dict[str, Any]:
    low, high = sorted((float(low), float(high)))
    return {
        "poi_id": poi_id,
        "poi_type": poi_type,
        "timeframe": cfg["htf_timeframe"],
        "direction": direction,
        "zone_low": low,
        "zone_high": high,
        "created_at_index": candle.index,
        "created_at_time": candle.timestamp,
        "created_by_event": "detected_from_closed_htf_candles",
        "fresh_status": "fresh",
        "mitigated_count": 0,
        "active_status": True,
        "invalidated": False,
        "quality_score": quality,
    }


def _closed_candles(df: Sequence[Mapping[str, Any] | Any] | Any) -> list[_Candle]:
    if df is None:
        return []
    if hasattr(df, "to_dict"):
        rows = df.to_dict("records")
    elif isinstance(df, Sequence) and not isinstance(df, (str, bytes, dict)):
        rows = list(df)
    else:
        rows = []
    candles = []
    for position, row in enumerate(rows):
        if _get(row, "is_closed", "closed", default=True) is False:
            continue
        if _get(row, "open", "o", default=None) is None:
            continue
        candles.append(
            _Candle(
                position=position,
                index=int(_get(row, "index", default=position)),
                timestamp=_get(row, "timestamp", "time", default=None),
                open=float(_get(row, "open", "o", default=0.0)),
                high=float(_get(row, "high", "h", default=0.0)),
                low=float(_get(row, "low", "l", default=0.0)),
                close=float(_get(row, "close", "c", default=0.0)),
                volume=float(_get(row, "volume", "tick_volume", default=0.0)),
            )
        )
    return candles


def _swings(candles: Sequence[_Candle], swings: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if swings:
        return [
            {
                "swing_id": _get(s, "swing_id", "id", default=f"SWING_{i}"),
                "kind": _swing_kind(_get(s, "kind", "type", "swing_type", default="")),
                "index": int(_get(s, "index", default=0)),
                "price": float(_get(s, "price", default=0.0)),
            }
            for i, s in enumerate(swings)
            if _swing_kind(_get(s, "kind", "type", "swing_type", default="")) in {"high", "low"}
        ]
    out = []
    for i in range(1, len(candles) - 1):
        if candles[i].high > candles[i - 1].high and candles[i].high > candles[i + 1].high:
            out.append(
                {
                    "swing_id": f"AUTO_HIGH_{candles[i].index}",
                    "kind": "high",
                    "index": candles[i].index,
                    "price": candles[i].high,
                }
            )
        if candles[i].low < candles[i - 1].low and candles[i].low < candles[i + 1].low:
            out.append(
                {
                    "swing_id": f"AUTO_LOW_{candles[i].index}",
                    "kind": "low",
                    "index": candles[i].index,
                    "price": candles[i].low,
                }
            )
    return out


def _swing_kind(value: Any) -> str:
    raw = str(value).lower()
    if raw in {"swing_high", "high", "hh", "lh"}:
        return "high"
    if raw in {"swing_low", "low", "ll", "hl"}:
        return "low"
    return raw


def _config(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    defaults = {
        "htf_timeframe": "1H",
        "ltf_timeframe": "5M",
        "atr_period": 5,
        "minimum_htf_poi_quality": 5.8,
        "min_htf_fvg_size": 0.05,
        "max_htf_poi_width": 12.0,
        "htf_departure_range_to_atr": 1.4,
        "sweep_buffer": 0.05,
        "sweep_buffer_atr_multiplier": 0.02,
        "poi_tolerance": 0.0,
        "poi_tolerance_atr_multiplier": 0.35,
        "break_buffer": 0.05,
        "break_buffer_atr_multiplier": 0.02,
        "max_mss_wait_candles": 8,
        "max_displacement_wait_candles": 5,
        "displacement_min_body_to_range": 0.45,
        "displacement_min_range_to_atr": 0.65,
        "displacement_min_close_position": 0.6,
        "max_entry_scan_candles": 6,
        "min_ltf_entry_poi_size": 0.01,
        "max_ltf_entry_poi_width": 4.0,
        "minimum_target_distance": 1.0,
        "stop_atr_buffer_multiplier": 0.05,
        "spread_buffer_multiplier": 1.2,
        "max_spread_points": 0.7,
        "min_rr": 2.0,
        "minimum_setup_score": 7.5,
    }
    if config:
        defaults.update(dict(config))
    return defaults


def _get(obj: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if isinstance(obj, Mapping) and key in obj:
            return obj[key]
        if hasattr(obj, key):
            return getattr(obj, key)
    return default


def _direction(value: Any) -> HTFPOIDirection:
    raw = str(value.value if isinstance(value, Enum) else value or "").lower()
    if raw in {"bull", "buy", "long", "bullish", "demand", "support"}:
        return HTFPOIDirection.BULLISH
    if raw in {"bear", "sell", "short", "bearish", "supply", "resistance"}:
        return HTFPOIDirection.BEARISH
    return HTFPOIDirection.NONE


def _context_bias(context: Mapping[str, Any]) -> str:
    bias = _get(context, "htf_bias", "bias", default="unknown")
    if isinstance(bias, Mapping):
        bias = _get(bias, "bias_direction", "direction", default="unknown")
    return str(bias)


def _zone_bounds(obj: Mapping[str, Any]) -> tuple[float, float]:
    low = _get(obj, "zone_low", "low", default=None)
    high = _get(obj, "zone_high", "high", default=None)
    if low is None or high is None:
        price = float(_get(obj, "price", "target_price", default=0.0))
        low = price
        high = price
    return tuple(sorted((float(low), float(high))))


def _pool_side(pool: Mapping[str, Any]) -> str:
    raw = str(_get(pool, "side", "direction", default="")).lower()
    if raw in {"sell_side", "sellside", "ssl", "low", "equal_lows", "pdl"}:
        return "sell_side"
    if raw in {"buy_side", "buyside", "bsl", "high", "equal_highs", "pdh"}:
        return "buy_side"
    return raw


def _pool_swept(pool: Mapping[str, Any]) -> bool:
    return bool(pool.get("swept", False)) or str(pool.get("swept_status", "active")).lower() in {
        "swept",
        "cleared",
        "consumed",
        "invalid",
    }


def _overlaps(candle: _Candle, low: float, high: float) -> bool:
    return candle.low <= high and candle.high >= low


def _zones_overlap(a_low: float, a_high: float, b_low: float, b_high: float) -> bool:
    return a_low <= b_high and a_high >= b_low


def _price_near_zone(price: float, low: float, high: float, tolerance: float) -> bool:
    return low - tolerance <= price <= high + tolerance


def _poi_invalidated(direction: HTFPOIDirection, low: float, high: float, latest_close: float) -> bool:
    if direction is HTFPOIDirection.BULLISH:
        return latest_close < low
    if direction is HTFPOIDirection.BEARISH:
        return latest_close > high
    return True


def _htf_quality(poi: Mapping[str, Any], low: float, high: float, cfg: Mapping[str, Any]) -> float:
    score = float(poi.get("quality_score", 6.5))
    poi_type = str(_get(poi, "poi_type", "type", default="")).lower()
    score += 0.8 if "order_block" in poi_type else 0.0
    score += 0.5 if "fvg" in poi_type else 0.0
    score += 0.6 if str(poi.get("fresh_status", "fresh")).lower() in {"fresh", "first_mitigation"} else 0.0
    score -= max(0, int(poi.get("mitigated_count", 0)) - 1) * 0.5
    score -= 1.2 if high - low > float(cfg["max_htf_poi_width"]) else 0.0
    return _clamp(score, 0, 10)


def _spread(status: Mapping[str, Any], cfg: Mapping[str, Any]) -> float:
    return float(_get(status, "spread_points", "current_spread", "spread", default=0.0) or 0.0)


def _spread_safe(status: Mapping[str, Any], cfg: Mapping[str, Any]) -> bool:
    if not status:
        return True
    if status.get("spread_safe") is False:
        return False
    if str(status.get("status", "normal")).lower() in {"wide", "abnormal", "unsafe"}:
        return False
    return _spread(status, cfg) <= float(cfg["max_spread_points"])


def _atr(candles: Sequence[_Candle], period: int = 14) -> float:
    if not candles:
        return 1.0
    return max(mean(c.range for c in candles[-max(1, period) :]), 1e-9)


def _buffer(cfg: Mapping[str, Any], key: str, fallback: float) -> float:
    return float(cfg[key]) if cfg.get(key) is not None else fallback


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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


def _unique(items: Sequence[str]) -> list[str]:
    out = []
    seen = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out
