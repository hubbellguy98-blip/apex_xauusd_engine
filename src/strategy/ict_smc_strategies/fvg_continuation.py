"""FVG Continuation strategy model for ICT/SMC research.

This is not a plain "find any fair value gap" detector. A valid continuation
setup must prove the chain:

HTF bias -> BOS by close -> displacement -> FVG -> retracement -> reaction ->
target liquidity -> RR/safety validation -> score.

The module is pure Python and returns dictionaries for research, testing,
backtesting, and future orchestration. It does not place broker orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence


class FVGContinuationDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class FVGContinuationStatus(str, Enum):
    VALID = "valid"
    REJECTED = "rejected"
    WAITING_FOR_RETRACEMENT = "waiting_for_retracement"


class FVGEntryMode(str, Enum):
    AGGRESSIVE = "aggressive"
    BALANCED = "balanced"
    CONSERVATIVE = "conservative"


class FVGConfirmationMode(str, Enum):
    AGGRESSIVE = "aggressive"
    CANDLE_REACTION = "candle_reaction"
    LTF_MSS = "ltf_mss"


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


def _cfg(config: Mapping[str, Any] | None, key: str, default: Any) -> Any:
    return default if config is None else config.get(key, default)


def _direction(value: Any) -> str:
    raw = str(value.value if isinstance(value, Enum) else value or "unknown").lower()
    if raw in {"bull", "buy", "long", "buy_side", "buyside", "bullish", "bullish_fvg"}:
        return "bullish"
    if raw in {"bear", "sell", "short", "sell_side", "sellside", "bearish", "bearish_fvg"}:
        return "bearish"
    if raw in {"neutral", "range", "ranging"}:
        return "neutral"
    return "unknown"


def _target_side(direction: str) -> str:
    return "buy_side" if direction == "bullish" else "sell_side"


def _atr(candles: Sequence[_Candle], period: int = 14, end_position: int | None = None) -> float:
    if not candles:
        return 1.0
    end = len(candles) if end_position is None else max(1, min(len(candles), end_position + 1))
    window = candles[max(0, end - max(1, period)) : end]
    return max(mean([c.range for c in window]), 1e-9) if window else 1.0


def detect_htf_bias(
    htf_df: Any = None,
    htf_swings: Sequence[Mapping[str, Any]] | None = None,
    htf_liquidity: Sequence[Mapping[str, Any]] | None = None,
    htf_pois: Sequence[Mapping[str, Any]] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify higher-timeframe bias for continuation trading."""
    if isinstance(htf_df, Mapping) and (htf_df.get("bias_direction") or htf_df.get("direction")):
        direction = _direction(htf_df.get("bias_direction", htf_df.get("direction")))
        return {
            "bias_direction": direction,
            "structure_state": htf_df.get("structure_state", f"{direction}_structure"),
            "draw_on_liquidity": htf_df.get("draw_on_liquidity", _target_side(direction)),
            "latest_htf_bos": htf_df.get("latest_htf_bos"),
            "active_htf_poi": htf_df.get("active_htf_poi"),
            "target_side": htf_df.get("target_side", _target_side(direction)),
            "confidence_score": float(
                htf_df.get("confidence_score", 8.0 if direction in {"bullish", "bearish"} else 4.0)
            ),
            "blockers": list(htf_df.get("blockers", [])),
        }

    swings = list(htf_swings or [])
    highs = [float(s["price"]) for s in swings if str(s.get("kind", s.get("type", ""))).lower() == "high"]
    lows = [float(s["price"]) for s in swings if str(s.get("kind", s.get("type", ""))).lower() == "low"]
    direction = "neutral"
    confidence = 4.0
    structure_state = "ranging"
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            direction, structure_state, confidence = "bullish", "bullish_structure", 7.5
        elif highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            direction, structure_state, confidence = "bearish", "bearish_structure", 7.5
    else:
        candles = _candles(htf_df)
        if len(candles) >= 5:
            movement = candles[-1].close - candles[-5].close
            avg_range = _atr(candles, int(_cfg(config, "atr_period", 14)))
            if movement > avg_range:
                direction, structure_state, confidence = "bullish", "bullish_structure", 6.5
            elif movement < -avg_range:
                direction, structure_state, confidence = "bearish", "bearish_structure", 6.5

    blockers = []
    for poi in htf_pois or []:
        if _direction(poi.get("direction", poi.get("type"))) in {"bullish", "bearish"}:
            if _direction(poi.get("direction", poi.get("type"))) != direction:
                blockers.append(poi.get("id", "opposing_htf_poi"))
    return {
        "bias_direction": direction,
        "structure_state": structure_state,
        "draw_on_liquidity": _target_side(direction) if direction in {"bullish", "bearish"} else "unclear",
        "latest_htf_bos": None,
        "active_htf_poi": None,
        "target_side": _target_side(direction) if direction in {"bullish", "bearish"} else "unclear",
        "confidence_score": round(max(0.0, confidence), 2),
        "blockers": blockers,
    }


def _confirmed_swings(candles: Sequence[_Candle], swings: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if swings:
        out = []
        for i, swing in enumerate(swings):
            try:
                out.append(
                    {
                        "id": swing.get("id", f"SWING_{i}"),
                        "kind": str(swing.get("kind", swing.get("type", ""))).lower(),
                        "index": int(swing.get("index", i)),
                        "price": float(swing.get("price", swing.get("level"))),
                    }
                )
            except (TypeError, ValueError):
                continue
        return out
    out = []
    for pos in range(1, len(candles) - 1):
        prev_c, current, next_c = candles[pos - 1], candles[pos], candles[pos + 1]
        if current.high > prev_c.high and current.high > next_c.high:
            out.append(
                {"id": f"SWING_HIGH_{current.index}", "kind": "high", "index": current.index, "price": current.high}
            )
        if current.low < prev_c.low and current.low < next_c.low:
            out.append(
                {"id": f"SWING_LOW_{current.index}", "kind": "low", "index": current.index, "price": current.low}
            )
    return out


def detect_bos(
    df: Any,
    swings: Sequence[Mapping[str, Any]] | None = None,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Detect BOS events confirmed by a close beyond a valid swing."""
    candles = _candles(df)
    break_buffer = float(_cfg(config, "break_buffer", 0.0))
    events = []
    for swing in _confirmed_swings(candles, swings):
        level = float(swing["price"])
        wick_only_candidate: dict[str, Any] | None = None
        for candle in candles:
            if candle.index <= int(swing["index"]):
                continue
            if swing["kind"] == "high":
                wick_broke = candle.high > level + break_buffer
                close_broke = candle.close > level + break_buffer
                direction = "bullish"
            elif swing["kind"] == "low":
                wick_broke = candle.low < level - break_buffer
                close_broke = candle.close < level - break_buffer
                direction = "bearish"
            else:
                continue
            if not wick_broke and not close_broke:
                continue
            distance = abs(candle.close - level) if close_broke else 0.0
            strength = 5.0 + min(
                3.5, distance / _atr(candles, int(_cfg(config, "atr_period", 14)), candle.position) * 2.0
            )
            event = {
                "bos_confirmed": bool(close_broke),
                "confirmed": bool(close_broke),
                "direction": direction,
                "broken_level": level,
                "broken_swing_id": swing["id"],
                "confirmation_index": candle.index,
                "confirmation_position": candle.position,
                "confirmation_time": candle.timestamp,
                "confirmed_by_close": bool(close_broke),
                "wick_broke": bool(wick_broke),
                "strength_score": round(max(0.0, min(10.0, strength)), 2),
            }
            if close_broke:
                events.append(event)
                break
            wick_only_candidate = event
        else:
            if wick_only_candidate is not None:
                events.append(wick_only_candidate)
    return sorted(events, key=lambda event: event["confirmation_position"])


def _fvg_exists_near(candles: Sequence[_Candle], direction: str, position: int) -> bool:
    for pos in range(max(2, position - 1), min(len(candles), position + 3)):
        c1, c3 = candles[pos - 2], candles[pos]
        if direction == "bullish" and c1.high < c3.low:
            return True
        if direction == "bearish" and c1.low > c3.high:
            return True
    return False


def detect_displacement(
    df: Any,
    bos_event: Mapping[str, Any] | int | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect directional displacement around/after BOS."""
    candles = _candles(df)
    if not candles:
        return _empty_displacement()
    if isinstance(bos_event, Mapping):
        direction = _direction(bos_event.get("direction"))
        start = int(bos_event.get("confirmation_position", 0))
    else:
        direction = _direction(_cfg(config, "direction", "unknown"))
        start = max(0, int(bos_event or 0))
    wait = int(_cfg(config, "max_displacement_wait_candles", 4))
    min_body = float(_cfg(config, "min_body_to_range", 0.55))
    min_range_atr = float(_cfg(config, "displacement_min_range_to_atr", _cfg(config, "min_range_to_atr", 0.75)))
    min_close_position = float(_cfg(config, "min_close_position", 0.68))
    best: dict[str, Any] | None = None
    for pos in range(max(0, start - 1), min(len(candles), start + wait + 1)):
        candle = candles[pos]
        if direction == "bullish" and not candle.bullish:
            continue
        if direction == "bearish" and not candle.bearish:
            continue
        if direction not in {"bullish", "bearish"}:
            continue
        range_to_atr = candle.range / _atr(candles, int(_cfg(config, "atr_period", 14)), pos)
        close_position = candle.bullish_close_position if direction == "bullish" else candle.bearish_close_position
        confirmed = (
            candle.body_to_range >= min_body and range_to_atr >= min_range_atr and close_position >= min_close_position
        )
        fvg_created = _fvg_exists_near(candles, direction, pos)
        strength = min(3.0, candle.body_to_range / min_body * 3.0)
        strength += min(3.0, range_to_atr / min_range_atr * 3.0)
        strength += min(2.0, close_position / min_close_position * 2.0)
        strength += 1.0 + (1.0 if fvg_created else 0.0)
        candidate = {
            "displacement_confirmed": confirmed,
            "confirmed": confirmed,
            "direction": direction,
            "start_index": candle.index,
            "end_index": candle.index,
            "start_position": pos,
            "end_position": pos,
            "body_to_range_ratio": round(candle.body_to_range, 4),
            "range_to_atr_ratio": round(range_to_atr, 4),
            "close_position_score": round(close_position, 4),
            "strength_score": round(max(0.0, min(10.0, strength)), 2),
            "fvg_created": fvg_created,
        }
        if best is None or candidate["strength_score"] > best["strength_score"]:
            best = candidate
    return best or _empty_displacement(direction)


def _empty_displacement(direction: str = "unknown") -> dict[str, Any]:
    return {
        "displacement_confirmed": False,
        "confirmed": False,
        "direction": direction,
        "start_index": None,
        "end_index": None,
        "start_position": None,
        "end_position": None,
        "body_to_range_ratio": 0.0,
        "range_to_atr_ratio": 0.0,
        "close_position_score": 0.0,
        "strength_score": 0.0,
        "fvg_created": False,
    }


def detect_fvg(
    df: Any,
    bos_event: Mapping[str, Any] | None = None,
    displacement: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Detect three-candle FVG zones and annotate continuation validity."""
    candles = _candles(df)
    if len(candles) < 3:
        return []
    bos_position = None if not bos_event else int(bos_event.get("confirmation_position", -1))
    displacement_position = None if not displacement else displacement.get("end_position")
    displacement_direction = _direction(displacement.get("direction")) if displacement else "unknown"
    min_mult = float(_cfg(config, "min_fvg_atr_multiplier", 0.03))
    max_mult = float(_cfg(config, "max_fvg_atr_multiplier", 3.0))
    results = []
    for pos in range(2, len(candles)):
        c1, c2, c3 = candles[pos - 2], candles[pos - 1], candles[pos]
        if c1.high < c3.low:
            direction, zone_low, zone_high, fvg_type = "bullish", c1.high, c3.low, "bullish_fvg"
        elif c1.low > c3.high:
            direction, zone_low, zone_high, fvg_type = "bearish", c3.high, c1.low, "bearish_fvg"
        else:
            continue
        size = max(zone_high - zone_low, 0.0)
        size_to_atr = size / _atr(candles, int(_cfg(config, "atr_period", 14)), pos)
        created_after_bos = bos_position is not None and pos >= bos_position
        created_by_displacement = (
            displacement_position is not None
            and displacement_direction == direction
            and abs(pos - int(displacement_position)) <= int(_cfg(config, "displacement_fvg_window", 2))
        )
        quality = 5.0
        quality += 1.0 if created_after_bos else 0.0
        quality += 1.5 if created_by_displacement else 0.0
        quality += 1.0 if min_mult <= size_to_atr <= max_mult else 0.0
        quality += 1.0 if c2.body_to_range >= float(_cfg(config, "min_body_to_range", 0.55)) else 0.0
        quality += 0.5 if (direction == "bullish" and c2.bullish) or (direction == "bearish" and c2.bearish) else 0.0
        results.append(
            {
                "fvg_id": f"FVG_CONT_{direction.upper()}_{c3.index}",
                "fvg_type": fvg_type,
                "direction": direction,
                "zone_low": round(zone_low, 8),
                "zone_high": round(zone_high, 8),
                "zone_mid": round((zone_low + zone_high) / 2.0, 8),
                "creation_index": c3.index,
                "creation_position": pos,
                "creation_time": c3.timestamp,
                "source_candles": [c1.index, c2.index, c3.index],
                "size": round(size, 8),
                "size_to_atr_ratio": round(size_to_atr, 4),
                "created_after_bos": created_after_bos,
                "created_by_displacement": created_by_displacement,
                "fill_status": "untouched",
                "filled_percent": 0.0,
                "active_status": True,
                "invalidated": False,
                "quality_score": round(max(0.0, min(10.0, quality)), 2),
                "too_large": size_to_atr > max_mult,
                "too_small": size_to_atr < min_mult,
            }
        )
    return results


def detect_fvg_retracement(
    df: Any,
    fvg: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
    ltf_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect fill into the FVG and require continuation reaction."""
    candles = _candles(df)
    direction = _direction(fvg.get("direction", fvg.get("fvg_type")))
    zone_low = float(fvg["zone_low"])
    zone_high = float(fvg["zone_high"])
    zone_mid = float(fvg["zone_mid"])
    creation_position = int(fvg.get("creation_position", -1))
    max_wait = int(_cfg(config, "max_retracement_wait_candles", 12))
    search_end = min(len(candles), creation_position + max_wait + 1) if creation_position >= 0 else len(candles)
    best_fill = 0.0
    fill_status = "untouched"

    for pos in range(max(0, creation_position + 1), search_end):
        candle = candles[pos]
        if direction == "bullish":
            if candle.close < zone_low:
                return _retracement_result(fvg, candle, "invalidated", 100.0, False, False, True, None)
            if candle.low > zone_high:
                continue
            best_fill = max(
                best_fill, (zone_high - max(candle.low, zone_low)) / max(zone_high - zone_low, 1e-9) * 100.0
            )
            fill_status = "full_fill" if candle.low <= zone_low else "partial_fill"
            touched_midpoint = candle.low <= zone_mid
            entry_price = zone_mid if touched_midpoint else zone_high
        elif direction == "bearish":
            if candle.close > zone_high:
                return _retracement_result(fvg, candle, "invalidated", 100.0, False, False, True, None)
            if candle.high < zone_low:
                continue
            best_fill = max(
                best_fill, (min(candle.high, zone_high) - zone_low) / max(zone_high - zone_low, 1e-9) * 100.0
            )
            fill_status = "full_fill" if candle.high >= zone_high else "partial_fill"
            touched_midpoint = candle.high >= zone_mid
            entry_price = zone_mid if touched_midpoint else zone_low
        else:
            continue

        reaction = _find_reaction_candle(candles, pos, direction, config)
        reaction_confirmed = _reaction_confirmed(
            direction,
            reaction,
            ltf_context or {},
            str(_cfg(config, "confirmation_mode", FVGConfirmationMode.CANDLE_REACTION.value)).lower(),
            str(_cfg(config, "entry_mode", FVGEntryMode.BALANCED.value)).lower(),
        )
        return _retracement_result(
            fvg,
            reaction or candle,
            fill_status,
            max(best_fill, 50.0 if touched_midpoint else best_fill),
            touched_midpoint,
            reaction_confirmed,
            False,
            entry_price,
        )

    return {
        "retracement_detected": False,
        "retracement_time": None,
        "retracement_index": None,
        "fill_status": fill_status,
        "filled_percent": round(best_fill, 2),
        "touched_midpoint": False,
        "reaction_confirmed": False,
        "reaction_type": "none",
        "entry_triggered": False,
        "entry_price": None,
        "invalidated": False,
    }


def _find_reaction_candle(
    candles: Sequence[_Candle], touch_position: int, direction: str, config: Mapping[str, Any] | None
) -> _Candle | None:
    wait = int(_cfg(config, "reaction_wait_candles", 2))
    for pos in range(touch_position, min(len(candles), touch_position + wait + 1)):
        candle = candles[pos]
        if direction == "bullish" and candle.bullish and candle.bullish_close_position >= 0.55:
            return candle
        if direction == "bearish" and candle.bearish and candle.bearish_close_position >= 0.55:
            return candle
    return None


def _reaction_confirmed(
    direction: str,
    reaction_candle: _Candle | None,
    ltf_context: Mapping[str, Any],
    confirmation_mode: str,
    entry_mode: str,
) -> bool:
    if confirmation_mode == FVGConfirmationMode.AGGRESSIVE.value or entry_mode == FVGEntryMode.AGGRESSIVE.value:
        return True
    if confirmation_mode == FVGConfirmationMode.LTF_MSS.value or entry_mode == FVGEntryMode.CONSERVATIVE.value:
        return bool(
            ltf_context.get(f"{direction}_mss_confirmed")
            or ltf_context.get(f"ltf_{direction}_mss")
            or ltf_context.get(f"{direction}_reaction_confirmed")
        )
    return reaction_candle is not None or bool(ltf_context.get(f"{direction}_reaction_confirmed"))


def _retracement_result(
    fvg: Mapping[str, Any],
    candle: _Candle,
    fill_status: str,
    filled_percent: float,
    touched_midpoint: bool,
    reaction_confirmed: bool,
    invalidated: bool,
    entry_price: float | None,
) -> dict[str, Any]:
    direction = _direction(fvg.get("direction", fvg.get("fvg_type")))
    return {
        "retracement_detected": not invalidated,
        "retracement_time": candle.timestamp,
        "retracement_index": candle.index,
        "fill_status": fill_status,
        "filled_percent": round(max(0.0, min(100.0, filled_percent)), 2),
        "touched_midpoint": touched_midpoint,
        "reaction_confirmed": reaction_confirmed,
        "reaction_type": f"{direction}_reaction_candle" if reaction_confirmed else "none",
        "entry_triggered": bool(entry_price is not None and not invalidated and reaction_confirmed),
        "entry_price": round(entry_price, 8) if entry_price is not None else None,
        "invalidated": invalidated,
    }


def _pool_side(pool: Mapping[str, Any]) -> str:
    side = str(pool.get("side", pool.get("type", ""))).lower()
    if side in {"buy", "buy_side", "buyside", "bsl", "high", "equal_highs"}:
        return "buy_side"
    if side in {"sell", "sell_side", "sellside", "ssl", "low", "equal_lows"}:
        return "sell_side"
    return side


def _pool_level(pool: Mapping[str, Any], direction: str) -> float | None:
    try:
        if "price" in pool:
            return float(pool["price"])
        if direction == "bullish":
            return float(pool.get("zone_high", pool.get("high", pool.get("zone_low"))))
        return float(pool.get("zone_low", pool.get("low", pool.get("zone_high"))))
    except (TypeError, ValueError):
        return None


def _select_target(direction: str, entry: float, pools: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    side = _target_side(direction)
    candidates: list[tuple[float, Mapping[str, Any]]] = []
    for pool in pools:
        if _pool_side(pool) != side:
            continue
        level = _pool_level(pool, direction)
        if level is None:
            continue
        if direction == "bullish" and level > entry:
            candidates.append((level, pool))
        if direction == "bearish" and level < entry:
            candidates.append((level, pool))
    if not candidates:
        return None
    level, pool = min(candidates, key=lambda item: abs(item[0] - entry))
    return {
        "id": pool.get("id", f"{side}_target"),
        "side": side,
        "price": level,
        "swept_status": pool.get("swept_status", pool.get("status", "active")),
        "reference": f"next_{side}_liquidity",
    }


def _recent_structure(candles: Sequence[_Candle], direction: str, before_position: int) -> float | None:
    window = candles[max(0, before_position - 6) : max(0, before_position + 1)]
    if not window:
        return None
    return min(c.low for c in window) if direction == "bullish" else max(c.high for c in window)


def _risk(
    direction: str,
    entry_price: float,
    fvg: Mapping[str, Any],
    candles: Sequence[_Candle],
    config: Mapping[str, Any] | None,
    spread_status: Mapping[str, Any] | None,
) -> dict[str, Any]:
    creation_position = int(fvg.get("creation_position", 0))
    atr_buffer = _atr(candles, int(_cfg(config, "atr_period", 14)), creation_position) * float(
        _cfg(config, "stop_atr_buffer", 0.08)
    )
    spread_buffer = float((spread_status or {}).get("spread_points", (spread_status or {}).get("spread", 0.0)) or 0.0)
    structure = _recent_structure(candles, direction, creation_position)
    if direction == "bullish":
        stop = (
            min(float(fvg["zone_low"]), structure if structure is not None else float(fvg["zone_low"]))
            - atr_buffer
            - spread_buffer
        )
        distance = entry_price - stop
    else:
        stop = (
            max(float(fvg["zone_high"]), structure if structure is not None else float(fvg["zone_high"]))
            + atr_buffer
            + spread_buffer
        )
        distance = stop - entry_price
    return {
        "stop_loss": round(stop, 8),
        "stop_reference": "beyond_fvg_and_recent_structure_with_buffer",
        "risk_distance": round(max(distance, 0.0), 8),
    }


def _rr(direction: str, entry: float, stop: float, target: float) -> tuple[float, float, float]:
    risk = entry - stop if direction == "bullish" else stop - entry
    reward = target - entry if direction == "bullish" else entry - target
    return max(risk, 0.0), max(reward, 0.0), reward / risk if risk > 0 and reward > 0 else 0.0


def validate_fvg_continuation(
    setup: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply hard continuation filters to a candidate setup."""
    context = context or {}
    reasons: list[str] = list(setup.get("rejection_reasons", []))

    def add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    direction = _direction(setup.get("direction"))
    htf_bias = setup.get("htf_bias", context.get("htf_bias", {})) or {}
    bos = setup.get("bos", {}) or {}
    displacement = setup.get("displacement", {}) or {}
    fvg = setup.get("fvg", {}) or {}
    retracement = setup.get("retracement", {}) or {}
    target = setup.get("target")
    news_status = context.get("news_status", {}) or {}
    spread_status = context.get("spread_status", {}) or {}

    if not setup.get("uses_closed_candles", True):
        add("uses_unclosed_candle")
    if direction not in {"bullish", "bearish"} or _direction(htf_bias.get("bias_direction")) != direction:
        add("htf_bias_not_aligned")
    if not bos.get("bos_confirmed", bos.get("confirmed", False)):
        add("no_bos_for_continuation")
    if bos and not bos.get("confirmed_by_close", False):
        add("wick_only_bos")
    if not displacement.get("confirmed", displacement.get("displacement_confirmed", False)):
        add("no_displacement")
    if float(displacement.get("strength_score", 0.0) or 0.0) < float(_cfg(config, "min_displacement_score", 6.0)):
        add("weak_displacement")
    if not fvg:
        add("no_fvg")
    else:
        if _direction(fvg.get("direction", fvg.get("fvg_type"))) != direction:
            add("fvg_direction_mismatch")
        if not fvg.get("created_after_bos", False):
            add("fvg_not_created_after_bos")
        if not fvg.get("created_by_displacement", False):
            add("random_fvg_no_displacement")
        if float(fvg.get("size_to_atr_ratio", 0.0) or 0.0) > float(_cfg(config, "max_fvg_atr_multiplier", 3.0)):
            add("fvg_too_large")
        if float(fvg.get("size_to_atr_ratio", 0.0) or 0.0) < float(_cfg(config, "min_fvg_atr_multiplier", 0.03)):
            add("fvg_too_small")
        if fvg.get("invalidated") or retracement.get("invalidated"):
            add("fvg_invalidated")
    if str(context.get("market_condition", "")).lower() in {"choppy", "range", "ranging", "equilibrium"}:
        add("choppy_market_random_fvg_risk")
    if not target:
        add("no_valid_target")
    elif str(target.get("swept_status", "active")).lower() in {"fully_swept", "swept", "inactive"}:
        add("target_already_swept")
    if htf_bias.get("blockers") or context.get("htf_blocker_before_target"):
        add("htf_poi_blocks_target")
    if float(setup.get("rr", 0.0) or 0.0) < float(_cfg(config, "min_rr", 2.0)):
        add("rr_below_minimum")
    if news_status.get("restricted", news_status.get("is_restricted", False)):
        add("news_restricted")
        if not news_status.get("post_news_stabilized", False):
            add("random_fvg_no_stabilized_structure")
    if spread_status.get("spread_safe") is False or str(spread_status.get("status", "")).lower() == "unsafe":
        add("spread_too_high")
    return {"valid": not reasons, "rejection_reasons": reasons, "warnings": [], "trade_allowed": not reasons}


def score_fvg_continuation_setup(
    setup: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score an FVG continuation setup from 0 to 10."""
    context = context or {}
    validation = setup.get("validation") or validate_fvg_continuation(setup, context, config)
    htf_bias = setup.get("htf_bias", {}) or {}
    bos = setup.get("bos", {}) or {}
    displacement = setup.get("displacement", {}) or {}
    fvg = setup.get("fvg", {}) or {}
    retracement = setup.get("retracement", {}) or {}
    components = {
        "htf_bias_alignment": min(10.0, float(htf_bias.get("confidence_score", 0.0) or 0.0) + 1.0),
        "bos_confirmation": float(bos.get("strength_score", 0.0) or 0.0),
        "displacement_strength": float(displacement.get("strength_score", 0.0) or 0.0),
        "fvg_quality": float(fvg.get("quality_score", 0.0) or 0.0),
        "retracement_reaction": (
            8.5 if retracement.get("entry_triggered") else 4.0 if retracement.get("retracement_detected") else 0.0
        ),
        "trend_clarity": (
            4.0 if str(context.get("market_condition", "")).lower() in {"choppy", "ranging", "range"} else 8.0
        ),
        "target_rr": min(
            10.0, float(setup.get("rr", 0.0) or 0.0) / max(float(_cfg(config, "min_rr", 2.0)), 1e-9) * 7.5
        ),
        "xauusd_safety": 9.0,
        "session_timing": float(context.get("session_score", _cfg(config, "default_session_score", 8.0))),
    }
    if context.get("news_status", {}).get("restricted"):
        components["xauusd_safety"] -= 4.0
    if (
        context.get("spread_status", {}).get("spread_safe") is False
        or str(context.get("spread_status", {}).get("status", "")).lower() == "unsafe"
    ):
        components["xauusd_safety"] -= 3.0
    components = {key: round(max(0.0, min(10.0, value)), 2) for key, value in components.items()}
    weights = {
        "htf_bias_alignment": 1.2,
        "bos_confirmation": 1.0,
        "displacement_strength": 1.2,
        "fvg_quality": 1.1,
        "retracement_reaction": 1.1,
        "trend_clarity": 0.8,
        "target_rr": 1.1,
        "xauusd_safety": 0.9,
        "session_timing": 0.6,
    }
    total = sum(components[key] * weights[key] for key in components) / sum(weights.values())
    failures = list(validation.get("rejection_reasons", []))
    trade_allowed = total >= float(_cfg(config, "minimum_setup_score", _cfg(config, "min_score", 7.0))) and not failures
    grade = (
        "A+"
        if total >= 9
        else "A" if total >= 8 else "B" if total >= 7 else "C" if total >= 6 else "D" if total >= 5 else "F"
    )
    return {
        "total_score": round(total, 2),
        "component_scores": components,
        "grade": grade,
        "trade_allowed": trade_allowed,
        "hard_filter_failures": failures,
        "warnings": list(validation.get("warnings", [])),
    }


def _rejected(symbol: str, reasons: Sequence[str], details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    clean = []
    for reason in reasons:
        if reason not in clean:
            clean.append(reason)
    return {
        "strategy": "FVG Continuation Model",
        "symbol": symbol,
        "signal_status": FVGContinuationStatus.REJECTED.value,
        "trade_allowed": False,
        "rejection_reasons": clean,
        "details": dict(details or {}),
    }


def generate_fvg_continuation_signal(
    context: Mapping[str, Any], config: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Generate a full FVG continuation signal or a no-trade decision."""
    symbol = str(context.get("symbol", "XAUUSD"))
    setup_df = context.get("setup_df", context.get("m15_df", context.get("candles", context.get("df", []))))
    entry_df = context.get("entry_df", context.get("m5_df", setup_df))
    candles = _candles(setup_df)
    if len(candles) < 5:
        return _rejected(symbol, ["insufficient_closed_candles"])

    htf_bias = context.get("htf_bias") or detect_htf_bias(
        context.get("htf_df"),
        context.get("htf_swings"),
        context.get("htf_liquidity", context.get("liquidity_pools")),
        context.get("htf_pois"),
        config,
    )
    bias_direction = _direction(htf_bias.get("bias_direction"))
    safety_reasons = []
    if bias_direction not in {"bullish", "bearish"}:
        safety_reasons.append("htf_bias_not_aligned")
    if (context.get("news_status", {}) or {}).get(
        "restricted", (context.get("news_status", {}) or {}).get("is_restricted", False)
    ):
        safety_reasons.append("news_restricted")
    spread_status = context.get("spread_status", {}) or {}
    if spread_status.get("spread_safe") is False or str(spread_status.get("status", "")).lower() == "unsafe":
        safety_reasons.append("spread_too_high")

    matching_bos = [
        event
        for event in detect_bos(setup_df, context.get("swings"), config)
        if event.get("confirmed_by_close") and event.get("direction") == bias_direction
    ]
    if not matching_bos:
        fvgs = detect_fvg(setup_df, None, None, config)
        reasons = safety_reasons + ["no_bos_for_continuation", "random_fvg_no_displacement"]
        if str(context.get("market_condition", "")).lower() in {"choppy", "range", "ranging", "equilibrium"}:
            reasons.append("choppy_market_random_fvg_risk")
        return _rejected(
            symbol,
            reasons,
            {
                "fvg_detected": bool(fvgs),
                "fvg_type": fvgs[-1]["fvg_type"] if fvgs else None,
                "htf_bias": bias_direction,
                "bos_confirmed": False,
            },
        )

    best_rejected: dict[str, Any] | None = None
    rejection_reasons: list[str] = []
    for bos in matching_bos:
        displacement = detect_displacement(setup_df, bos, config)
        fvg_list = [
            fvg for fvg in detect_fvg(setup_df, bos, displacement, config) if fvg["direction"] == bias_direction
        ]
        if not displacement.get("confirmed"):
            rejection_reasons.extend(["no_displacement", "weak_displacement"])
        if not fvg_list:
            rejection_reasons.append("no_fvg")
            continue
        fvg_list.sort(key=lambda item: (not item["created_by_displacement"], item["creation_position"]))
        for fvg in fvg_list:
            retracement = detect_fvg_retracement(entry_df, fvg, config, context.get("ltf_context"))
            fvg = dict(fvg)
            fvg["fill_status"] = retracement.get("fill_status", fvg["fill_status"])
            fvg["filled_percent"] = retracement.get("filled_percent", fvg["filled_percent"])
            fvg["invalidated"] = bool(retracement.get("invalidated"))
            if fvg["invalidated"]:
                best_rejected = _rejected(symbol, safety_reasons + ["fvg_invalidated"], {"fvg": fvg})
                rejection_reasons.append("fvg_invalidated")
                continue
            if retracement.get("entry_price") is None:
                best_rejected = _rejected(symbol, safety_reasons + ["waiting_for_fvg_retracement"], {"fvg": fvg})
                continue
            entry_price = float(retracement["entry_price"])
            risk = _risk(bias_direction, entry_price, fvg, candles, config, spread_status)
            target = _select_target(bias_direction, entry_price, context.get("liquidity_pools", []))
            risk_distance, reward_distance, rr_value = _rr(
                bias_direction,
                entry_price,
                float(risk["stop_loss"]),
                float(target["price"]) if target else entry_price,
            )
            setup = {
                "strategy": "FVG Continuation Model",
                "symbol": symbol,
                "signal_id": f"{symbol}_FVG_CONT_{bias_direction.upper()}_{fvg['creation_index']}",
                "signal_status": FVGContinuationStatus.VALID.value,
                "uses_closed_candles": all(c.is_closed for c in candles),
                "direction": bias_direction,
                "timeframe_stack": {
                    "htf_bias_timeframe": context.get("htf_timeframe", "1H"),
                    "setup_timeframe": context.get("setup_timeframe", "15M"),
                    "entry_timeframe": context.get("entry_timeframe", "5M"),
                },
                "htf_bias": htf_bias,
                "bos": bos,
                "displacement": displacement,
                "fvg": fvg,
                "retracement": retracement,
                "entry": {"entry_type": "fvg_midpoint_reaction_entry", "entry_price": round(entry_price, 8)},
                "target": target,
                "risk": {
                    **risk,
                    "target": round(float(target["price"]), 8) if target else None,
                    "target_reference": target.get("reference") if target else None,
                    "risk_distance": round(risk_distance, 8),
                    "reward_distance": round(reward_distance, 8),
                    "rr": round(rr_value, 4),
                    "min_rr_required": float(_cfg(config, "min_rr", 2.0)),
                },
                "rr": rr_value,
                "filters": {
                    "news_filter": "failed" if "news_restricted" in safety_reasons else "passed",
                    "spread_filter": "failed" if "spread_too_high" in safety_reasons else "passed",
                    "chop_filter": (
                        "failed"
                        if str(context.get("market_condition", "")).lower() in {"choppy", "range", "ranging"}
                        else "passed"
                    ),
                    "fvg_size_filter": "failed" if fvg.get("too_large") or fvg.get("too_small") else "passed",
                    "htf_blocker_filter": "failed" if htf_bias.get("blockers") else "passed",
                },
                "rejection_reasons": list(safety_reasons),
            }
            validation = validate_fvg_continuation(setup, context, config)
            setup["validation"] = validation
            setup["score"] = score_fvg_continuation_setup(setup, context, config)
            setup["trade_allowed"] = bool(setup["score"]["trade_allowed"])
            if validation["valid"] and setup["trade_allowed"]:
                return setup
            best_rejected = _rejected(
                symbol,
                validation["rejection_reasons"] or setup["score"]["hard_filter_failures"],
                {"direction_candidate": bias_direction, "score": setup["score"], "fvg": fvg, "rr": round(rr_value, 4)},
            )
            rejection_reasons.extend(validation["rejection_reasons"])
    return best_rejected or _rejected(symbol, rejection_reasons or ["no_valid_fvg_continuation_setup"])
