"""Previous day high/low liquidity logic for ICT/SMC analysis.

PDH and PDL are external liquidity references. They can be targets, raid
zones, or continuation levels, but they are not entry signals by themselves.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence

from src.core.domain.market_data import CandleNode


class PreviousDayLiquidityType(str, Enum):
    PREVIOUS_DAY_HIGH = "previous_day_high"
    PREVIOUS_DAY_LOW = "previous_day_low"


class PreviousDayLiquidityDirection(str, Enum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


class PreviousDayRaidType(str, Enum):
    NONE = "none"
    PDH_RAID = "pdh_raid"
    PDH_BREAKOUT = "pdh_breakout"
    PDL_RAID = "pdl_raid"
    PDL_BREAKDOWN = "pdl_breakdown"


class PreviousDayRaidDirection(str, Enum):
    NONE = "none"
    BUY_SIDE_TAKEN = "buy_side_liquidity_taken"
    SELL_SIDE_TAKEN = "sell_side_liquidity_taken"


class PreviousDayReactionBias(str, Enum):
    NONE = "none"
    BEARISH_POSSIBLE = "bearish_possible"
    BULLISH_POSSIBLE = "bullish_possible"
    BULLISH_CONTINUATION = "bullish_continuation_possible"
    BEARISH_CONTINUATION = "bearish_continuation_possible"


class PreviousDayQualityGrade(str, Enum):
    INVALID = "invalid"
    WEAK = "weak"
    USABLE_CONTEXT = "usable_context"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True, slots=True)
class PreviousDayLevelZone:
    liquidity_id: str
    liquidity_type: PreviousDayLiquidityType
    direction: PreviousDayLiquidityDirection
    price: float
    zone_low: float
    zone_high: float
    liquidity_role: str
    target_use: str
    swept_status: str = "unswept"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["liquidity_type"] = self.liquidity_type.value
        payload["direction"] = self.direction.value
        payload["price"] = round(self.price, 5)
        payload["zone_low"] = round(self.zone_low, 5)
        payload["zone_high"] = round(self.zone_high, 5)
        return payload


@dataclass(frozen=True, slots=True)
class _Candle:
    index: int
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str
    symbol: str
    is_closed: bool = True

    @property
    def range(self) -> float:
        return max(0.0, self.high - self.low)

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
    def close_position(self) -> float:
        if self.range <= 0:
            return 0.5
        return (self.close - self.low) / self.range


@dataclass(frozen=True, slots=True)
class _RaidEvent:
    raid_type: PreviousDayRaidType
    raid_direction: PreviousDayRaidDirection
    level: float
    candle: _Candle
    sweep_confirmed: bool
    breakout_confirmed: bool
    raid_result: str
    reaction_bias: PreviousDayReactionBias
    condition: str


def calculate_previous_day_levels(
    df: Sequence[CandleNode | Mapping[str, Any]] | Any,
    *,
    session_definition: str = "utc_day",
    timezone_name: str | None = None,
    symbol: str = "unknown",
    timeframe: str | None = None,
    tolerance: float | None = None,
    tolerance_percent: float = 0.02,
) -> dict[str, Any]:
    """Calculate PDH/PDL from the completed day before the latest closed candle."""
    candles = [candle for candle in _normalize_candles(df, timeframe, symbol) if candle.is_closed]
    if len(candles) < 2:
        return _empty_levels("insufficient_closed_candles", symbol, session_definition)

    grouped = _group_by_trading_day(candles, timezone_name)
    ordered_days = sorted(grouped)
    if len(ordered_days) < 2:
        return _empty_levels("insufficient_completed_trading_days", symbol, session_definition)

    current_day = ordered_days[-1]
    previous_day = ordered_days[-2]
    previous = sorted(grouped[previous_day], key=lambda item: item.timestamp)
    pdh = max(item.high for item in previous)
    pdl = min(item.low for item in previous)
    previous_range = max(0.0, pdh - pdl)
    zone_tolerance = tolerance if tolerance is not None else previous_range * tolerance_percent
    warnings = [
        "PDH/PDL depends on the selected daily session definition",
        "Use the same session definition in backtest and live trading",
        "PDH/PDL is not an entry signal without MSS/FVG/retest confirmation",
    ]
    if session_definition in {"unknown", "broker_day"}:
        warnings.append("session_definition_uncertain")
    if len(previous) < 2:
        warnings.append("previous_day_has_thin_candle_sample")

    levels = {
        "pdh": round(pdh, 5),
        "pdl": round(pdl, 5),
        "previous_day_open": round(previous[0].open, 5),
        "previous_day_close": round(previous[-1].close, 5),
        "previous_day_midpoint": round((pdh + pdl) / 2.0, 5),
        "previous_day_range": round(previous_range, 5),
        "previous_day_volume": round(sum(item.volume for item in previous), 5),
    }
    liquidity_objects = [
        PreviousDayLevelZone(
            liquidity_id=f"PDH_{previous_day.isoformat().replace('-', '_')}",
            liquidity_type=PreviousDayLiquidityType.PREVIOUS_DAY_HIGH,
            direction=PreviousDayLiquidityDirection.BUY_SIDE,
            price=pdh,
            zone_low=pdh - zone_tolerance,
            zone_high=pdh + zone_tolerance,
            liquidity_role="external_buy_side_liquidity",
            target_use="long_target_or_bearish_raid_area",
        ).as_dict(),
        PreviousDayLevelZone(
            liquidity_id=f"PDL_{previous_day.isoformat().replace('-', '_')}",
            liquidity_type=PreviousDayLiquidityType.PREVIOUS_DAY_LOW,
            direction=PreviousDayLiquidityDirection.SELL_SIDE,
            price=pdl,
            zone_low=pdl - zone_tolerance,
            zone_high=pdl + zone_tolerance,
            liquidity_role="external_sell_side_liquidity",
            target_use="short_target_or_bullish_raid_area",
        ).as_dict(),
    ]
    return {
        "concept_name": "Previous Day High / Previous Day Low Liquidity",
        "symbol": previous[0].symbol or symbol,
        "session_definition": session_definition,
        "daily_session_definition": session_definition,
        "current_trading_day": current_day.isoformat(),
        "previous_day_date": previous_day.isoformat(),
        **levels,
        "previous_day_levels": levels,
        "liquidity_objects": liquidity_objects,
        "data_quality_warning": any("thin" in warning or "missing" in warning for warning in warnings),
        "session_definition_uncertain": session_definition in {"unknown", "broker_day"},
        "warnings": warnings,
        "entry_allowed_from_pdh_pdl_alone": False,
    }


def detect_pdh_pdl_raid(
    intraday_df: Sequence[CandleNode | Mapping[str, Any]] | Any,
    pdh: float | Mapping[str, Any],
    pdl: float | Mapping[str, Any],
    *,
    raid_buffer: float | None = None,
    sweep_buffer: float | None = None,
    close_buffer: float | None = None,
    atr_period: int = 14,
    buffer_atr_multiplier: float = 0.05,
    mss_events: Sequence[Mapping[str, Any] | Any] | None = None,
    fvg_events: Sequence[Mapping[str, Any] | Any] | None = None,
    symbol: str = "unknown",
    timeframe: str | None = None,
    session_label: str | None = None,
) -> dict[str, Any]:
    """Detect closed-candle PDH/PDL sweeps, acceptance, and continuation context."""
    candles = [candle for candle in _normalize_candles(intraday_df, timeframe, symbol) if candle.is_closed]
    pdh_price = _extract_level_price(pdh, "pdh")
    pdl_price = _extract_level_price(pdl, "pdl")
    if not candles or pdh_price is None or pdl_price is None:
        return _empty_raid("missing_closed_candles_or_levels", symbol, timeframe)

    atr = _calculate_atr(candles, atr_period)[-1]
    sweep = sweep_buffer if sweep_buffer is not None else max(atr * buffer_atr_multiplier, 0.00001)
    close = close_buffer if close_buffer is not None else sweep
    raid = raid_buffer if raid_buffer is not None else sweep
    events = _detect_events(candles, pdh_price, pdl_price, raid, sweep, close)
    if not events:
        return _empty_raid("no_pdh_pdl_interaction", candles[0].symbol or symbol, timeframe or candles[-1].timeframe)

    selected = events[0]
    expected_direction = _expected_mss_direction(selected.raid_type)
    mss = _find_mss_confirmation(candles, selected, expected_direction, mss_events)
    fvg = _find_fvg_confirmation(candles, selected, expected_direction, fvg_events)
    displacement_strength = _displacement_strength(candles, selected, expected_direction, atr)
    reasons, warnings = _raid_reasons_and_warnings(selected, mss, fvg, displacement_strength, session_label)
    score = _quality_score(selected, mss, fvg, displacement_strength, session_label, reasons, warnings)

    return {
        "concept_name": "PDH/PDL Raid",
        "symbol": selected.candle.symbol or symbol,
        "timeframe": timeframe or selected.candle.timeframe,
        "raid_detected": True,
        "raid_type": selected.raid_type.value,
        "raid_direction": selected.raid_direction.value,
        "raid_level": round(selected.level, 5),
        "raid_candle_index": selected.candle.index,
        "raid_candle_timestamp": selected.candle.timestamp.isoformat(),
        "sweep_confirmed": selected.sweep_confirmed,
        "breakout_confirmed": selected.breakout_confirmed,
        "raid_result": selected.raid_result,
        "reaction_bias": selected.reaction_bias.value,
        "expected_reaction_bias": selected.reaction_bias.value,
        "mss_after_raid": mss["confirmed"],
        "mss_direction": mss["direction"],
        "mss_confirmation_index": mss["index"],
        "displacement_after_raid": displacement_strength in {"moderate", "strong"},
        "displacement_strength": displacement_strength,
        "fvg_after_raid": fvg["confirmed"],
        "fvg_type": fvg["type"],
        "fvg_zone_low": _round_optional(fvg["zone_low"]),
        "fvg_zone_high": _round_optional(fvg["zone_high"]),
        "fvg_creation_index": fvg["index"],
        "double_raid_day": len({event.raid_type for event in events}) > 1,
        "quality_score": score,
        "quality_grade": _quality_grade(score).value,
        "entry_model": {
            "entry_allowed_from_raid_alone": False,
            "recommended_entry": _recommended_entry(selected.raid_type),
            "stop_loss_reference": _stop_reference(selected.raid_type),
            "target_liquidity": _target_liquidity(selected.raid_type),
        },
        "reasons": reasons,
        "warnings": warnings,
    }


def _detect_events(
    candles: Sequence[_Candle],
    pdh: float,
    pdl: float,
    raid_buffer: float,
    sweep_buffer: float,
    close_buffer: float,
) -> list[_RaidEvent]:
    events: list[_RaidEvent] = []
    for candle in candles:
        if candle.high > pdh + raid_buffer:
            if candle.high > pdh + sweep_buffer and candle.close < pdh:
                events.append(
                    _RaidEvent(
                        PreviousDayRaidType.PDH_RAID,
                        PreviousDayRaidDirection.BUY_SIDE_TAKEN,
                        pdh,
                        candle,
                        True,
                        False,
                        "swept_and_rejected",
                        PreviousDayReactionBias.BEARISH_POSSIBLE,
                        "candle_high_above_PDH_and_close_back_below_PDH",
                    )
                )
            elif candle.close > pdh + close_buffer and candle.bullish and candle.close_position >= 0.65:
                events.append(
                    _RaidEvent(
                        PreviousDayRaidType.PDH_BREAKOUT,
                        PreviousDayRaidDirection.BUY_SIDE_TAKEN,
                        pdh,
                        candle,
                        False,
                        True,
                        "accepted_breakout_above_pdh",
                        PreviousDayReactionBias.BULLISH_CONTINUATION,
                        "candle_close_above_PDH_with_bullish_acceptance",
                    )
                )
        if candle.low < pdl - raid_buffer:
            if candle.low < pdl - sweep_buffer and candle.close > pdl:
                events.append(
                    _RaidEvent(
                        PreviousDayRaidType.PDL_RAID,
                        PreviousDayRaidDirection.SELL_SIDE_TAKEN,
                        pdl,
                        candle,
                        True,
                        False,
                        "swept_and_reclaimed",
                        PreviousDayReactionBias.BULLISH_POSSIBLE,
                        "candle_low_below_PDL_and_close_back_above_PDL",
                    )
                )
            elif candle.close < pdl - close_buffer and candle.bearish and candle.close_position <= 0.35:
                events.append(
                    _RaidEvent(
                        PreviousDayRaidType.PDL_BREAKDOWN,
                        PreviousDayRaidDirection.SELL_SIDE_TAKEN,
                        pdl,
                        candle,
                        False,
                        True,
                        "accepted_breakdown_below_pdl",
                        PreviousDayReactionBias.BEARISH_CONTINUATION,
                        "candle_close_below_PDL_with_bearish_acceptance",
                    )
                )
    return sorted(events, key=lambda event: (event.candle.timestamp, event.candle.index))


def _normalize_candles(
    source: Sequence[CandleNode | Mapping[str, Any]] | Any,
    timeframe: str | None,
    symbol: str,
) -> list[_Candle]:
    if hasattr(source, "to_dict"):
        source = source.to_dict("records")
    candles: list[_Candle] = []
    for position, item in enumerate(source or []):
        if isinstance(item, CandleNode):
            raw_timestamp = item.timestamp
            open_price = item.open
            high_price = item.high
            low_price = item.low
            close_price = item.close
            volume = item.volume
            candle_timeframe = item.timeframe
            candle_symbol = item.symbol
            is_closed = getattr(item, "is_closed", True)
            index = getattr(item, "index", position)
        else:
            raw_timestamp = _get(item, "timestamp", datetime.now(timezone.utc))
            open_price = _get(item, "open", _get(item, "open_p", 0.0))
            high_price = _get(item, "high", _get(item, "high_p", 0.0))
            low_price = _get(item, "low", _get(item, "low_p", 0.0))
            close_price = _get(item, "close", _get(item, "close_p", 0.0))
            volume = _get(item, "volume", 0.0)
            candle_timeframe = _get(item, "timeframe", timeframe or "unknown")
            candle_symbol = _get(item, "symbol", symbol)
            is_closed = bool(_get(item, "is_closed", True))
            index = int(_get(item, "index", position))
        candles.append(
            _Candle(
                index=index,
                timestamp=_timestamp(raw_timestamp),
                open=float(open_price),
                high=float(high_price),
                low=float(low_price),
                close=float(close_price),
                volume=float(volume or 0.0),
                timeframe=str(candle_timeframe or timeframe or "unknown"),
                symbol=str(candle_symbol or symbol),
                is_closed=is_closed,
            )
        )
    return sorted(candles, key=lambda candle: (candle.timestamp, candle.index))


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if hasattr(value, "to_pydatetime"):
        result = value.to_pydatetime()
        return result if result.tzinfo is not None else result.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else result.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _group_by_trading_day(candles: Sequence[_Candle], timezone_name: str | None) -> dict[date, list[_Candle]]:
    grouped: dict[date, list[_Candle]] = {}
    for candle in candles:
        trading_day = (
            candle.timestamp.date()
            if timezone_name is None
            else candle.timestamp.astimezone(timezone.utc).date()
        )
        grouped.setdefault(trading_day, []).append(candle)
    return grouped


def _calculate_atr(candles: Sequence[_Candle], period: int) -> list[float]:
    if not candles:
        return [0.0]
    true_ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        if previous_close is None:
            true_range = candle.range
        else:
            true_range = max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        true_ranges.append(max(0.0, true_range))
        previous_close = candle.close
    atr_values: list[float] = []
    for idx in range(len(true_ranges)):
        start = max(0, idx - period + 1)
        atr_values.append(mean(true_ranges[start : idx + 1]))
    return atr_values


def _extract_level_price(value: float | Mapping[str, Any], key: str) -> float | None:
    if isinstance(value, Mapping):
        if key in value:
            return float(value[key])
        if "price" in value:
            return float(value["price"])
        if "previous_day_levels" in value and key in value["previous_day_levels"]:
            return float(value["previous_day_levels"][key])
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _expected_mss_direction(raid_type: PreviousDayRaidType) -> str | None:
    if raid_type in {PreviousDayRaidType.PDH_RAID, PreviousDayRaidType.PDL_BREAKDOWN}:
        return "bearish"
    if raid_type in {PreviousDayRaidType.PDL_RAID, PreviousDayRaidType.PDH_BREAKOUT}:
        return "bullish"
    return None


def _find_mss_confirmation(
    candles: Sequence[_Candle],
    event: _RaidEvent,
    direction: str | None,
    mss_events: Sequence[Mapping[str, Any] | Any] | None,
) -> dict[str, Any]:
    if direction is None:
        return {"confirmed": False, "direction": None, "index": None}
    if mss_events is not None:
        for item in mss_events:
            item_direction = str(_get(item, "direction", _get(item, "mss_direction", ""))).lower()
            item_index = int(_get(item, "index", _get(item, "confirmation_index", -1)))
            if item_direction == direction and item_index > event.candle.index:
                return {"confirmed": True, "direction": direction, "index": item_index}
        return {"confirmed": False, "direction": direction, "index": None}
    later = [candle for candle in candles if candle.index > event.candle.index]
    if len(later) < 3:
        return {"confirmed": False, "direction": direction, "index": None}
    if direction == "bearish":
        reference = min(candle.low for candle in later[:2])
        for candle in later[2:]:
            if candle.close < reference:
                return {"confirmed": True, "direction": direction, "index": candle.index}
    else:
        reference = max(candle.high for candle in later[:2])
        for candle in later[2:]:
            if candle.close > reference:
                return {"confirmed": True, "direction": direction, "index": candle.index}
    return {"confirmed": False, "direction": direction, "index": None}


def _find_fvg_confirmation(
    candles: Sequence[_Candle],
    event: _RaidEvent,
    direction: str | None,
    fvg_events: Sequence[Mapping[str, Any] | Any] | None,
) -> dict[str, Any]:
    if direction is None:
        return _empty_fvg()
    fvg_type = "bearish_fvg" if direction == "bearish" else "bullish_fvg"
    if fvg_events is not None:
        for item in fvg_events:
            item_type = str(_get(item, "type", _get(item, "fvg_type", ""))).lower()
            item_direction = str(_get(item, "direction", "")).lower()
            item_index = int(_get(item, "index", _get(item, "creation_index", -1)))
            if item_index > event.candle.index and (item_type == fvg_type or item_direction == direction):
                return {
                    "confirmed": True,
                    "type": fvg_type,
                    "index": item_index,
                    "zone_low": _optional_float(_get(item, "zone_low")),
                    "zone_high": _optional_float(_get(item, "zone_high")),
                }
        return _empty_fvg(fvg_type)
    later = [candle for candle in candles if candle.index > event.candle.index]
    for idx in range(len(later) - 2):
        first, _, third = later[idx], later[idx + 1], later[idx + 2]
        if direction == "bearish" and first.low > third.high:
            return {
                "confirmed": True,
                "type": fvg_type,
                "index": third.index,
                "zone_low": third.high,
                "zone_high": first.low,
            }
        if direction == "bullish" and first.high < third.low:
            return {
                "confirmed": True,
                "type": fvg_type,
                "index": third.index,
                "zone_low": first.high,
                "zone_high": third.low,
            }
    return _empty_fvg(fvg_type)


def _empty_fvg(fvg_type: str | None = None) -> dict[str, Any]:
    return {"confirmed": False, "type": fvg_type, "index": None, "zone_low": None, "zone_high": None}


def _optional_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _round_optional(value: Any) -> float | None:
    parsed = _optional_float(value)
    return None if parsed is None else round(parsed, 5)


def _displacement_strength(candles: Sequence[_Candle], event: _RaidEvent, direction: str | None, atr: float) -> str:
    if direction is None or atr <= 0:
        return "none"
    strongest = 0.0
    for candle in [item for item in candles if item.index > event.candle.index][:5]:
        directional = (direction == "bullish" and candle.bullish) or (direction == "bearish" and candle.bearish)
        if directional:
            strongest = max(strongest, candle.body / atr)
    if strongest >= 1.2:
        return "strong"
    if strongest >= 0.65:
        return "moderate"
    return "none"


def _quality_score(
    event: _RaidEvent,
    mss: Mapping[str, Any],
    fvg: Mapping[str, Any],
    displacement_strength: str,
    session_label: str | None,
    reasons: list[str],
    warnings: list[str],
) -> float:
    score = 1.0
    score += 1.5 if event.sweep_confirmed else 1.2
    score += 1.5
    if mss["confirmed"]:
        score += 2.0
        reasons.append("market_structure_shift_confirmed_after_level_interaction")
    else:
        warnings.append("no_mss_after_raid_detected")
    if displacement_strength == "strong":
        score += 1.0
    elif displacement_strength == "moderate":
        score += 0.5
    else:
        warnings.append("no_clear_displacement_after_raid")
    if fvg["confirmed"]:
        score += 1.0
        reasons.append("fvg_detected_after_raid_or_acceptance")
    else:
        warnings.append("no_fvg_after_raid_detected")
    if session_label and session_label.lower() in {"london", "london_killzone", "newyork", "newyork_killzone", "ny"}:
        score += 1.0
        reasons.append("interaction_happened_during_preferred_active_session")
    elif session_label:
        score += 0.5
    score += 1.0 if event.sweep_confirmed else 0.5
    if not mss["confirmed"]:
        score = min(score, 5.0)
    if not fvg["confirmed"] or displacement_strength == "none":
        score = min(score, 6.0)
    if event.breakout_confirmed:
        score = min(score, 8.0)
    return round(max(0.0, min(10.0, score)), 2)


def _raid_reasons_and_warnings(
    event: _RaidEvent,
    mss: Mapping[str, Any],
    fvg: Mapping[str, Any],
    displacement_strength: str,
    session_label: str | None,
) -> tuple[list[str], list[str]]:
    reasons = [event.condition]
    warnings = ["Do not use PDH/PDL raid alone as an entry trigger"]
    if event.raid_type == PreviousDayRaidType.PDH_RAID:
        reasons += ["previous_day_high_acted_as_buy_side_liquidity", "price_raided_above_PDH_and_closed_back_below_it"]
    elif event.raid_type == PreviousDayRaidType.PDL_RAID:
        reasons += ["previous_day_low_acted_as_sell_side_liquidity", "price_raided_below_PDL_and_closed_back_above_it"]
    elif event.raid_type == PreviousDayRaidType.PDH_BREAKOUT:
        reasons.append("price_accepted_above_PDH_instead_of_rejecting")
        warnings.append("not_a_bearish_pdh_sweep")
    elif event.raid_type == PreviousDayRaidType.PDL_BREAKDOWN:
        reasons.append("price_accepted_below_PDL_instead_of_reclaiming")
        warnings.append("not_a_bullish_pdl_sweep")
    if session_label is None:
        warnings.append("session_timing_not_supplied_for_quality_scoring")
    if not mss["confirmed"]:
        warnings.append("execution_model_should_wait_for_confirmed_mss")
    if not fvg["confirmed"]:
        warnings.append("execution_model_should_wait_for_fvg_or_ob_retest")
    if displacement_strength == "none":
        warnings.append("displacement_not_confirmed")
    return reasons, warnings


def _quality_grade(score: float) -> PreviousDayQualityGrade:
    if score < 2.5:
        return PreviousDayQualityGrade.INVALID
    if score < 5.0:
        return PreviousDayQualityGrade.WEAK
    if score < 7.0:
        return PreviousDayQualityGrade.USABLE_CONTEXT
    if score < 9.0:
        return PreviousDayQualityGrade.STRONG
    return PreviousDayQualityGrade.HIGH_QUALITY


def _recommended_entry(raid_type: PreviousDayRaidType) -> str:
    if raid_type == PreviousDayRaidType.PDH_RAID:
        return "wait_for_bearish_MSS_then_retest_into_bearish_FVG_or_OB_and_confirm_rejection"
    if raid_type == PreviousDayRaidType.PDL_RAID:
        return "wait_for_bullish_MSS_then_retest_into_bullish_FVG_or_OB_and_confirm_reaction"
    if raid_type == PreviousDayRaidType.PDH_BREAKOUT:
        return "wait_for_retest_of_PDH_as_support_or_bullish_FVG_continuation_context"
    if raid_type == PreviousDayRaidType.PDL_BREAKDOWN:
        return "wait_for_retest_of_PDL_as_resistance_or_bearish_FVG_continuation_context"
    return "no_entry_context"


def _stop_reference(raid_type: PreviousDayRaidType) -> str | None:
    if raid_type == PreviousDayRaidType.PDH_RAID:
        return "above_raid_high_or_above_PDH_sweep_high"
    if raid_type == PreviousDayRaidType.PDL_RAID:
        return "below_raid_low_or_below_PDL_sweep_low"
    if raid_type == PreviousDayRaidType.PDH_BREAKOUT:
        return "below_reclaimed_PDH_or_continuation_FVG"
    if raid_type == PreviousDayRaidType.PDL_BREAKDOWN:
        return "above_rejected_PDL_or_continuation_FVG"
    return None


def _target_liquidity(raid_type: PreviousDayRaidType) -> str | None:
    if raid_type == PreviousDayRaidType.PDH_RAID:
        return "internal_sell_side_liquidity_or_PDL"
    if raid_type == PreviousDayRaidType.PDL_RAID:
        return "internal_buy_side_liquidity_or_PDH"
    if raid_type == PreviousDayRaidType.PDH_BREAKOUT:
        return "higher_external_buy_side_liquidity"
    if raid_type == PreviousDayRaidType.PDL_BREAKDOWN:
        return "lower_external_sell_side_liquidity"
    return None


def _empty_levels(reason: str, symbol: str, session_definition: str) -> dict[str, Any]:
    return {
        "concept_name": "Previous Day High / Previous Day Low Liquidity",
        "symbol": symbol,
        "session_definition": session_definition,
        "daily_session_definition": session_definition,
        "pdh": None,
        "pdl": None,
        "previous_day_levels": {},
        "liquidity_objects": [],
        "data_quality_warning": True,
        "session_definition_uncertain": session_definition in {"unknown", "broker_day"},
        "warnings": [reason],
        "entry_allowed_from_pdh_pdl_alone": False,
    }


def _empty_raid(reason: str, symbol: str, timeframe: str | None) -> dict[str, Any]:
    return {
        "concept_name": "PDH/PDL Raid",
        "symbol": symbol,
        "timeframe": timeframe or "unknown",
        "raid_detected": False,
        "raid_type": PreviousDayRaidType.NONE.value,
        "raid_direction": PreviousDayRaidDirection.NONE.value,
        "raid_level": None,
        "raid_candle_index": None,
        "raid_candle_timestamp": None,
        "sweep_confirmed": False,
        "breakout_confirmed": False,
        "raid_result": reason,
        "reaction_bias": PreviousDayReactionBias.NONE.value,
        "expected_reaction_bias": PreviousDayReactionBias.NONE.value,
        "mss_after_raid": False,
        "mss_direction": None,
        "mss_confirmation_index": None,
        "displacement_after_raid": False,
        "displacement_strength": "none",
        "fvg_after_raid": False,
        "fvg_type": None,
        "fvg_zone_low": None,
        "fvg_zone_high": None,
        "fvg_creation_index": None,
        "double_raid_day": False,
        "quality_score": 0.0,
        "quality_grade": PreviousDayQualityGrade.INVALID.value,
        "entry_model": {
            "entry_allowed_from_raid_alone": False,
            "recommended_entry": "no_entry_context",
            "stop_loss_reference": None,
            "target_liquidity": None,
        },
        "reasons": [],
        "warnings": [reason],
    }
