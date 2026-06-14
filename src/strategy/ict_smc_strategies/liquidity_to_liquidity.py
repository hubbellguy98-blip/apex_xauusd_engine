"""Liquidity-to-Liquidity strategy model for ICT/SMC research.

This layer maps where price may be drawing after a confirmed liquidity event.
It is deliberately not an entry model by itself. A valid output requires:

starting liquidity taken -> structure/displacement confirmation -> ranked
opposite-side target -> no strong HTF blocker -> usable entry model -> RR and
XAUUSD safety checks.

The module is deterministic, closed-candle only, and never places orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence


class LiquidityToLiquidityStatus(str, Enum):
    VALID = "valid"
    REJECTED = "rejected"
    CONTEXT_ONLY = "context_only"


class LiquidityPathBias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    UNCLEAR = "unclear"


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


def detect_liquidity_pools(
    df: Any,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Detect basic swing and equal-high/low liquidity pools from closed candles."""

    cfg = _config(config)
    candles = _candles(df)
    if len(candles) < int(cfg["min_total_candles"]):
        return []

    pools: list[dict[str, Any]] = []
    swing_depth = int(cfg["swing_depth"])
    equal_tolerance = float(cfg["equal_level_tolerance"])
    for position in range(swing_depth, len(candles) - swing_depth):
        left = candles[position - swing_depth : position]
        right = candles[position + 1 : position + 1 + swing_depth]
        candle = candles[position]
        if all(candle.high > item.high for item in left + right):
            pools.append(
                _pool(
                    f"SWING_HIGH_{candle.index}",
                    "swing_high",
                    "buy_side",
                    candle.high,
                    candle,
                    quality=6.5,
                    timeframe=str(cfg["timeframe"]),
                )
            )
        if all(candle.low < item.low for item in left + right):
            pools.append(
                _pool(
                    f"SWING_LOW_{candle.index}",
                    "swing_low",
                    "sell_side",
                    candle.low,
                    candle,
                    quality=6.5,
                    timeframe=str(cfg["timeframe"]),
                )
            )

    for first, second in zip(pools, pools[1:]):
        if first["side"] == second["side"] and abs(float(first["price"]) - float(second["price"])) <= equal_tolerance:
            price = (float(first["price"]) + float(second["price"])) / 2.0
            side = str(first["side"])
            pools.append(
                {
                    "liquidity_id": f"EQUAL_{side.upper()}_{first['created_index']}_{second['created_index']}",
                    "id": f"EQUAL_{side.upper()}_{first['created_index']}_{second['created_index']}",
                    "liquidity_type": "equal_highs" if side == "buy_side" else "equal_lows",
                    "side": side,
                    "price": round(price, 8),
                    "zone_low": round(price - equal_tolerance, 8),
                    "zone_high": round(price + equal_tolerance, 8),
                    "quality_score": 7.5,
                    "target_priority_score": 7.5,
                    "timeframe": str(cfg["timeframe"]),
                    "swept_status": "unswept",
                    "internal_or_external": "unknown",
                    "created_index": second["created_index"],
                    "created_position": second["created_position"],
                    "is_closed_candle_pool": True,
                }
            )
    return sorted(pools, key=lambda item: (item["created_position"], item["quality_score"]))


def classify_internal_external_liquidity(
    liquidity_pools: Sequence[Mapping[str, Any]],
    dealing_range: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Classify pools as internal or external to the current dealing range."""

    cfg = _config(config)
    if dealing_range is None:
        prices = [float(_value(pool, "price", 0.0)) for pool in liquidity_pools]
        if not prices:
            return []
        dealing_range = {
            "range_low": min(prices),
            "range_high": max(prices),
        }
    low = float(dealing_range.get("range_low", dealing_range.get("low")))
    high = float(dealing_range.get("range_high", dealing_range.get("high")))
    boundary = float(cfg["external_boundary_buffer"])
    classified: list[dict[str, Any]] = []
    for pool in liquidity_pools:
        enriched = dict(pool)
        price = float(_value(pool, "price", _zone_mid(pool)))
        if price <= low + boundary or price >= high - boundary:
            label = "external"
        elif low < price < high:
            label = "internal"
        else:
            label = "external"
        enriched["internal_or_external"] = str(_value(pool, "internal_or_external", label))
        if enriched["internal_or_external"] == "unknown":
            enriched["internal_or_external"] = label
        enriched["dealing_range_low"] = round(low, 8)
        enriched["dealing_range_high"] = round(high, 8)
        classified.append(enriched)
    return classified


def rank_liquidity_targets(
    liquidity_pools: Sequence[Mapping[str, Any]],
    direction: str,
    current_price: float,
    entry_price: float | None = None,
    stop_loss: float | None = None,
    htf_pois: Sequence[Mapping[str, Any]] | None = None,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Rank valid opposite-side target liquidity by reachability and quality."""

    cfg = _config(config)
    direction = _direction(direction)
    entry = current_price if entry_price is None else float(entry_price)
    side = "buy_side" if direction == "bullish" else "sell_side" if direction == "bearish" else "unknown"
    ranked: list[dict[str, Any]] = []
    for pool in liquidity_pools:
        pool_side = _pool_side(pool)
        if pool_side != side:
            continue
        if _swept_status(pool) in _invalid_target_statuses():
            ranked.append(
                {
                    **dict(pool),
                    "target_valid": False,
                    "rejection_reasons": ["target_liquidity_already_swept"],
                }
            )
            continue
        target_price = _target_price(pool, direction)
        distance = target_price - entry if direction == "bullish" else entry - target_price
        reasons: list[str] = []
        if distance <= 0:
            reasons.append("target_not_in_expected_direction")
        if distance < float(cfg["minimum_target_distance"]):
            reasons.append("target_distance_too_small")
        blockers = _htf_blockers_between(entry, target_price, direction, htf_pois or [], cfg)
        if blockers:
            reasons.append("target_blocked_by_htf_poi")
        rr = _rr_to_target(entry, target_price, stop_loss, direction, cfg)
        if rr["rr_to_target"] < float(cfg["min_rr"]):
            reasons.append("rr_below_minimum")
        spread = float(cfg["spread_points"])
        if distance > 0 and spread / max(distance, 1e-9) > float(cfg["max_spread_to_target_ratio"]):
            reasons.append("spread_too_large_relative_to_target")
        if spread > float(cfg["max_spread"]):
            reasons.append("spread_too_high")

        quality = float(_value(pool, "quality_score", 6.0))
        priority = float(_value(pool, "target_priority_score", quality))
        internal_external = str(_value(pool, "internal_or_external", "unknown"))
        external_bonus = 1.0 if internal_external == "external" else 0.25
        distance_score = min(2.0, max(0.0, distance / max(float(cfg["minimum_target_distance"]), 1e-9)))
        blocker_penalty = 4.0 if blockers else 0.0
        target_score = _clamp(
            quality * 0.35 + priority * 0.35 + external_bonus + distance_score - blocker_penalty, 0, 10
        )
        enriched = dict(pool)
        enriched.update(
            {
                "target_valid": not reasons,
                "target_side": side,
                "target_price": round(target_price, 8),
                "distance_from_entry": round(max(distance, 0.0), 8),
                "blocked_by_poi": bool(blockers),
                "blockers": blockers,
                "rr_to_target": round(rr["rr_to_target"], 4),
                "risk_to_reward": rr,
                "target_priority_score": round(target_score, 2),
                "target_role": "final_target" if internal_external == "external" else "partial_target",
                "rejection_reasons": _unique(reasons),
            }
        )
        ranked.append(enriched)
    return sorted(
        ranked,
        key=lambda item: (
            bool(item.get("target_valid")),
            float(item.get("target_priority_score", 0.0)),
            float(item.get("quality_score", 0.0)),
            float(item.get("distance_from_entry", 0.0)),
        ),
        reverse=True,
    )


def determine_draw_on_liquidity(
    start_liquidity: Mapping[str, Any] | None,
    structure_shift: Mapping[str, Any] | str | None = None,
    htf_bias: Mapping[str, Any] | str | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Infer likely draw after starting liquidity is swept."""

    _ = _config(config)
    if not start_liquidity:
        return {
            "path_bias": LiquidityPathBias.UNCLEAR.value,
            "draw_side": "unknown",
            "confidence": 0.0,
            "rejection_reasons": ["no_start_liquidity"],
        }
    start_side = _pool_side(start_liquidity)
    shift_direction = _direction(
        structure_shift.get("direction") if isinstance(structure_shift, Mapping) else structure_shift
    )
    htf_direction = _direction(htf_bias.get("bias_direction") if isinstance(htf_bias, Mapping) else htf_bias)
    expected = "bullish" if start_side == "sell_side" else "bearish" if start_side == "buy_side" else "unknown"
    reasons: list[str] = []
    confidence = 4.0
    if shift_direction == expected:
        confidence += 3.0
    else:
        reasons.append("no_structure_shift_after_starting_liquidity")
    if htf_direction == expected:
        confidence += 1.0
    elif htf_direction in {"bullish", "bearish"} and htf_direction != expected:
        reasons.append("htf_bias_conflict")
        confidence -= 1.0
    path_bias = expected if shift_direction == expected else "unclear"
    return {
        "path_bias": path_bias,
        "draw_side": "buy_side" if path_bias == "bullish" else "sell_side" if path_bias == "bearish" else "unknown",
        "expected_after_start_side": expected,
        "structure_shift_direction": shift_direction,
        "htf_bias": htf_direction,
        "confidence": round(_clamp(confidence, 0, 10), 2),
        "rejection_reasons": _unique(reasons),
    }


def detect_liquidity_to_liquidity_path(
    context: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map the path from recently taken liquidity to an opposite target pool."""

    cfg = _config(config)
    cfg["spread_points"] = _context_spread(context, cfg)
    candles = _candles(context.get("candles", context.get("df")))
    pools = list(context.get("liquidity_pools") or detect_liquidity_pools(candles, cfg))
    if not pools:
        return _empty_path("no_liquidity_pools_detected")
    pools = classify_internal_external_liquidity(pools, context.get("dealing_range"), cfg)
    start = _select_start_liquidity(pools, context, cfg)
    if start is None:
        return _empty_path("no_valid_start_liquidity")
    shift = _structure_shift(context, candles, start, cfg)
    draw = determine_draw_on_liquidity(start, shift, context.get("htf_bias"), cfg)
    direction = draw["path_bias"]
    current_price = float(context.get("current_price", candles[-1].close if candles else _zone_mid(start)))
    entry_model = _entry_model(context, candles, direction, cfg)
    entry_price = float(entry_model.get("entry_price", context.get("entry_price", current_price)))
    stop_loss = _optional_float(context.get("stop_loss", entry_model.get("stop_loss")))
    htf_pois = context.get("htf_pois", context.get("poi_zones", []))
    targets = rank_liquidity_targets(pools, direction, current_price, entry_price, stop_loss, htf_pois, cfg)
    valid_targets = [target for target in targets if target.get("target_valid")]
    best_target = valid_targets[0] if valid_targets else (targets[0] if targets else None)
    reasons: list[str] = []
    reasons.extend(draw["rejection_reasons"])
    reasons.extend(entry_model.get("rejection_reasons", []))
    if not targets:
        reasons.append("no_target_liquidity")
    elif best_target and not best_target.get("target_valid"):
        reasons.extend(best_target.get("rejection_reasons", []))
        if any("target_liquidity_already_swept" in target.get("rejection_reasons", []) for target in targets):
            reasons.append("target_liquidity_already_swept")
    reasons.extend(_environment_rejections(candles, context, cfg))
    return {
        "path_detected": not reasons and bool(best_target),
        "path_bias": direction,
        "draw_on_liquidity": draw,
        "start_liquidity": start,
        "target_liquidity": best_target,
        "target_ladder": targets,
        "entry_model": entry_model,
        "structure_shift": shift,
        "blockers": best_target.get("blockers", []) if best_target else [],
        "confidence": _path_confidence(draw, best_target, entry_model, reasons),
        "rejection_reasons": _unique(reasons),
    }


def generate_liquidity_to_liquidity_signal(
    context: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a complete liquidity-to-liquidity path decision."""

    cfg = _config(config)
    path = detect_liquidity_to_liquidity_path(context, cfg)
    score = score_liquidity_to_liquidity_setup(path, context, cfg)
    reasons = _unique(path.get("rejection_reasons", []) + score.get("rejection_reasons", []))
    if reasons:
        status = LiquidityToLiquidityStatus.CONTEXT_ONLY.value
        if any(
            reason
            in {
                "target_liquidity_already_swept",
                "target_distance_too_small",
                "spread_too_large_relative_to_target",
                "rr_below_minimum",
                "target_blocked_by_htf_poi",
                "spread_too_high",
                "max_candle_size_exceeded",
            }
            for reason in reasons
        ):
            status = LiquidityToLiquidityStatus.REJECTED.value
    else:
        status = LiquidityToLiquidityStatus.VALID.value

    return {
        "strategy": "liquidity_to_liquidity",
        "symbol": context.get("symbol", "XAUUSD"),
        "signal_status": status,
        "trade_allowed": status == LiquidityToLiquidityStatus.VALID.value,
        "entry_allowed_from_liquidity_path_alone": False,
        "path_bias": path.get("path_bias", "unclear"),
        "direction_candidate": path.get("path_bias", "unclear"),
        "start_liquidity": path.get("start_liquidity"),
        "target_liquidity": path.get("target_liquidity"),
        "target_ladder": path.get("target_ladder", []),
        "entry_model": path.get("entry_model"),
        "draw_on_liquidity": path.get("draw_on_liquidity"),
        "structure_shift": path.get("structure_shift"),
        "blockers": path.get("blockers", []),
        "score": score,
        "confidence": path.get("confidence", 0.0),
        "rejection_reasons": reasons,
        "uses_closed_candles_only": True,
    }


def score_liquidity_to_liquidity_setup(
    setup: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a liquidity-to-liquidity setup from 0 to 10."""

    cfg = _config(config)
    context = context or {}
    reasons = list(setup.get("rejection_reasons", []))
    components: dict[str, float] = {}
    if setup.get("start_liquidity"):
        components["starting_liquidity_taken"] = min(1.8, float(setup["start_liquidity"].get("quality_score", 6)) / 5)
    draw = setup.get("draw_on_liquidity", {})
    if draw.get("path_bias") in {"bullish", "bearish"}:
        components["draw_on_liquidity"] = min(1.5, float(draw.get("confidence", 0)) / 6)
    shift = setup.get("structure_shift", {})
    if shift.get("confirmed"):
        components["structure_shift"] = 1.5
    entry = setup.get("entry_model", {})
    if entry.get("entry_model_valid"):
        components["entry_model"] = 1.2
    target = setup.get("target_liquidity") or {}
    if target.get("target_valid"):
        components["ranked_target"] = min(1.6, float(target.get("target_priority_score", 0)) / 6)
        if target.get("target_role") == "final_target":
            components["external_target"] = 0.7
    if target.get("rr_to_target", 0) >= float(cfg["min_rr"]):
        components["risk_reward"] = min(1.2, float(target["rr_to_target"]) / 2.5)
    if context.get("htf_bias") and not any(reason == "htf_bias_conflict" for reason in reasons):
        components["htf_alignment"] = 0.5
    if target.get("blocked_by_poi"):
        reasons.append("target_blocked_by_htf_poi")
    total = round(_clamp(sum(components.values()), 0, 10), 2)
    if total < float(cfg["minimum_setup_score"]):
        reasons.append("confirmation_score_below_minimum_threshold")
    return {
        "total_score": total,
        "minimum_required_score": float(cfg["minimum_setup_score"]),
        "components": components,
        "trade_allowed": total >= float(cfg["minimum_setup_score"]) and not reasons,
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
        "timeframe": "5m",
        "min_total_candles": 8,
        "swing_depth": 1,
        "equal_level_tolerance": 0.08,
        "external_boundary_buffer": 0.10,
        "minimum_start_quality": 5.0,
        "minimum_target_distance": 0.90,
        "min_rr": 2.0,
        "minimum_setup_score": 7.0,
        "spread_points": 0.0,
        "max_spread": 0.45,
        "max_spread_to_target_ratio": 0.25,
        "slippage_points": 0.05,
        "stop_buffer": 0.10,
        "max_candle_range": 9.0,
        "min_body_to_range": 0.45,
        "displacement_min_range_to_atr": 0.50,
        "atr_period": 14,
        "blocker_quality_threshold": 7.5,
        "strong_blocker_quality": 8.5,
    }
    if config:
        defaults.update(dict(config))
    return defaults


def _pool(
    pool_id: str,
    liquidity_type: str,
    side: str,
    price: float,
    candle: _Candle,
    *,
    quality: float,
    timeframe: str,
) -> dict[str, Any]:
    return {
        "liquidity_id": pool_id,
        "id": pool_id,
        "liquidity_type": liquidity_type,
        "side": side,
        "price": round(price, 8),
        "zone_low": round(price - 0.02, 8),
        "zone_high": round(price + 0.02, 8),
        "quality_score": quality,
        "target_priority_score": quality,
        "timeframe": timeframe,
        "swept_status": "unswept",
        "internal_or_external": "unknown",
        "created_index": candle.index,
        "created_position": candle.position,
        "is_closed_candle_pool": True,
    }


def _pool_side(pool: Mapping[str, Any]) -> str:
    raw = str(_value(pool, "side", _value(pool, "direction", ""))).lower()
    if raw in {"sell_side", "sellside", "ssl", "low", "equal_lows", "range_low", "asian_low", "pdl"}:
        return "sell_side"
    if raw in {"buy_side", "buyside", "bsl", "high", "equal_highs", "range_high", "asian_high", "pdh"}:
        return "buy_side"
    return raw


def _swept_status(pool: Mapping[str, Any]) -> str:
    if bool(_value(pool, "swept", False)):
        return str(_value(pool, "reclaim_status", "swept")).lower()
    return str(_value(pool, "swept_status", "unswept")).lower()


def _valid_start_statuses() -> set[str]:
    return {
        "swept",
        "swept_reclaimed",
        "swept_rejected",
        "swept_and_reclaimed",
        "swept_and_rejected",
        "raid_reclaimed",
        "raid_rejected",
        "sell_side_sweep_reclaimed",
        "buy_side_sweep_rejected",
    }


def _invalid_target_statuses() -> set[str]:
    return {"swept", "fully_swept", "invalid", "invalidated", "cleared", "consumed"}


def _select_start_liquidity(
    pools: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> dict[str, Any] | None:
    event = context.get("latest_sweep_event", context.get("starting_liquidity_event", {})) or {}
    event_id = str(event.get("swept_liquidity_id", event.get("liquidity_id", event.get("id", ""))))
    candidates = []
    for pool in pools:
        status = _swept_status(pool)
        quality = float(_value(pool, "quality_score", 0.0))
        if quality < float(cfg["minimum_start_quality"]):
            continue
        if event_id and str(_value(pool, "liquidity_id", _value(pool, "id", ""))) == event_id:
            candidates.append(dict(pool))
            continue
        if status in _valid_start_statuses():
            candidates.append(dict(pool))
    if not candidates:
        return None
    candidates.sort(
        key=lambda pool: (
            str(_value(pool, "liquidity_id", _value(pool, "id", ""))) == event_id,
            int(_value(pool, "last_touched_index", _value(pool, "created_index", 0)) or 0),
            float(_value(pool, "quality_score", 0.0)),
        ),
        reverse=True,
    )
    start = candidates[0]
    start["start_liquidity_confirmed"] = True
    start["starting_liquidity_quality_score"] = float(_value(start, "quality_score", 0.0))
    return start


def _direction(value: Any) -> str:
    raw = str(value.value if isinstance(value, Enum) else value or "unknown").lower()
    if raw in {"bull", "buy", "long", "bullish", "buy_side", "bsl"}:
        return "bullish"
    if raw in {"bear", "sell", "short", "bearish", "sell_side", "ssl"}:
        return "bearish"
    return "unknown"


def _structure_shift(
    context: Mapping[str, Any],
    candles: Sequence[_Candle],
    start: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    expected = "bullish" if _pool_side(start) == "sell_side" else "bearish"
    for key in ("latest_mss_event", "latest_bos_event", "structure_shift"):
        event = context.get(key, {}) or {}
        direction = _direction(event.get("direction"))
        confirmed = bool(event.get("confirmed", event.get("mss_confirmed", event.get("bos_confirmed", False))))
        if direction == expected and confirmed:
            return {"confirmed": True, "direction": direction, "source": key, "rejection_reasons": []}
    displacement = context.get("displacement", {}) or {}
    if _direction(displacement.get("direction")) == expected and bool(displacement.get("confirmed", False)):
        return {
            "confirmed": True,
            "direction": expected,
            "source": "displacement",
            "rejection_reasons": [],
        }
    if len(candles) >= 4:
        atr = _atr(candles, int(cfg["atr_period"]))
        candle = candles[-1]
        if expected == "bullish" and candle.bullish and candle.body_to_range >= float(cfg["min_body_to_range"]):
            if candle.range >= atr * float(cfg["displacement_min_range_to_atr"]):
                return {
                    "confirmed": True,
                    "direction": expected,
                    "source": "closed_candle_displacement",
                    "rejection_reasons": [],
                }
        if expected == "bearish" and candle.bearish and candle.body_to_range >= float(cfg["min_body_to_range"]):
            if candle.range >= atr * float(cfg["displacement_min_range_to_atr"]):
                return {
                    "confirmed": True,
                    "direction": expected,
                    "source": "closed_candle_displacement",
                    "rejection_reasons": [],
                }
    return {
        "confirmed": False,
        "direction": "unknown",
        "source": None,
        "rejection_reasons": ["no_structure_shift_after_starting_liquidity"],
    }


def _entry_model(
    context: Mapping[str, Any],
    candles: Sequence[_Candle],
    direction: str,
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    provided = context.get("entry_model") or context.get("entry_signal")
    if isinstance(provided, Mapping):
        valid = bool(provided.get("entry_model_valid", provided.get("valid", provided.get("confirmed", False))))
        return {
            **dict(provided),
            "entry_model_valid": valid,
            "entry_price": float(
                provided.get("entry_price", context.get("entry_price", candles[-1].close if candles else 0.0))
            ),
            "stop_loss": _optional_float(provided.get("stop_loss", context.get("stop_loss"))),
            "rejection_reasons": [] if valid else ["missing_valid_entry_model"],
        }
    if not candles or direction == "unknown":
        return {"entry_model_valid": False, "rejection_reasons": ["missing_valid_entry_model"]}
    entry = candles[-1].close
    stop = (
        min(c.low for c in candles[-4:]) - float(cfg["stop_buffer"])
        if direction == "bullish"
        else max(c.high for c in candles[-4:]) + float(cfg["stop_buffer"])
    )
    return {
        "entry_model_valid": False,
        "entry_model_type": "context_only_no_entry_model",
        "entry_price": round(entry, 8),
        "stop_loss": round(stop, 8),
        "rejection_reasons": ["missing_valid_entry_model"],
    }


def _target_price(pool: Mapping[str, Any], direction: str) -> float:
    if direction == "bullish":
        return float(_value(pool, "zone_high", _value(pool, "price", _zone_mid(pool))))
    return float(_value(pool, "zone_low", _value(pool, "price", _zone_mid(pool))))


def _zone_mid(pool: Mapping[str, Any]) -> float:
    low = float(_value(pool, "zone_low", _value(pool, "price", 0.0)))
    high = float(_value(pool, "zone_high", _value(pool, "price", low)))
    return (low + high) / 2.0


def _rr_to_target(
    entry: float,
    target: float,
    stop_loss: float | None,
    direction: str,
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    if stop_loss is None:
        return {"rr_to_target": 0.0, "rr_valid": False, "risk_distance": 0.0, "reward_distance": 0.0}
    spread_slippage = float(cfg["spread_points"]) + float(cfg["slippage_points"])
    risk = entry - stop_loss if direction == "bullish" else stop_loss - entry
    reward = target - entry if direction == "bullish" else entry - target
    risk = max(risk + spread_slippage, 0.0)
    reward = max(reward - spread_slippage, 0.0)
    rr = reward / risk if risk > 0 else 0.0
    return {
        "rr_to_target": round(rr, 4),
        "rr_valid": rr >= float(cfg["min_rr"]),
        "risk_distance": round(risk, 8),
        "reward_distance": round(reward, 8),
    }


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
        poi_direction = _direction(poi.get("direction", poi.get("type", poi.get("poi_type"))))
        quality = float(poi.get("quality_score", 0.0) or 0.0)
        zone_low = float(poi.get("zone_low", poi.get("low", poi.get("price", 0.0))))
        zone_high = float(poi.get("zone_high", poi.get("high", poi.get("price", zone_low))))
        overlaps_path = max(low, zone_low) <= min(high, zone_high)
        if poi_direction == opposing and quality >= float(cfg["blocker_quality_threshold"]) and overlaps_path:
            item = dict(poi)
            item["blocker_strength"] = "strong" if quality >= float(cfg["strong_blocker_quality"]) else "moderate"
            blockers.append(item)
    return blockers


def _environment_rejections(
    candles: Sequence[_Candle], context: Mapping[str, Any], cfg: Mapping[str, Any]
) -> list[str]:
    reasons: list[str] = []
    spread = _context_spread(context, cfg)
    if spread > float(cfg["max_spread"]):
        reasons.append("spread_too_high")
    news = context.get("news_status", {}) or {}
    if bool(news.get("restricted", news.get("news_restricted", False))):
        reasons.append("news_restricted")
    if candles and max(c.range for c in candles) > float(cfg["max_candle_range"]):
        reasons.append("max_candle_size_exceeded")
    return reasons


def _context_spread(context: Mapping[str, Any], cfg: Mapping[str, Any]) -> float:
    spread_status = context.get("spread_status", {}) or {}
    return float(
        context.get(
            "spread_points",
            context.get("spread", spread_status.get("spread_points", cfg["spread_points"])),
        )
        or 0.0
    )


def _path_confidence(
    draw: Mapping[str, Any],
    target: Mapping[str, Any] | None,
    entry_model: Mapping[str, Any],
    reasons: Sequence[str],
) -> float:
    value = float(draw.get("confidence", 0.0))
    if target and target.get("target_valid"):
        value += min(2.0, float(target.get("target_priority_score", 0.0)) / 5.0)
    if entry_model.get("entry_model_valid"):
        value += 1.0
    if reasons:
        value -= min(4.0, len(set(reasons)) * 0.75)
    return round(_clamp(value, 0, 10), 2)


def _empty_path(reason: str) -> dict[str, Any]:
    return {
        "path_detected": False,
        "path_bias": LiquidityPathBias.UNCLEAR.value,
        "draw_on_liquidity": {"path_bias": "unclear", "draw_side": "unknown", "confidence": 0.0},
        "start_liquidity": None,
        "target_liquidity": None,
        "target_ladder": [],
        "entry_model": None,
        "structure_shift": {"confirmed": False},
        "blockers": [],
        "confidence": 0.0,
        "rejection_reasons": [reason],
    }


def _atr(candles: Sequence[_Candle], period: int = 14) -> float:
    if not candles:
        return 1.0
    window = candles[-max(1, period) :]
    return max(mean(c.range for c in window), 1e-9)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
