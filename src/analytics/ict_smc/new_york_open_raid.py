"""ICT/SMC New York Open liquidity raid model.

The New York Open Raid model scans important pre-NY liquidity levels, classifies
New York-window interaction as sweep/reversal or continuation, and applies news
risk before marking a setup valid. It is deterministic analytics only; a NY
sweep is never an entry signal by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as dt_timezone, tzinfo
from enum import Enum
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class NewYorkRaidType(str, Enum):
    NONE = "no_valid_new_york_open_raid"
    NEWS_BLACKOUT = "news_blackout_no_trade"
    NEWS_SPIKE = "news_spike_or_news_blackout"
    BUY_SIDE_SWEEP_REVERSAL = "ny_buy_side_sweep_bearish_reversal"
    SELL_SIDE_SWEEP_REVERSAL = "ny_sell_side_sweep_bullish_reversal"
    BUY_SIDE_SWEEP_CANDIDATE = "ny_buy_side_sweep_candidate"
    SELL_SIDE_SWEEP_CANDIDATE = "ny_sell_side_sweep_candidate"
    BUY_SIDE_BREAKOUT_CONTINUATION = "ny_buy_side_breakout_continuation"
    SELL_SIDE_BREAKDOWN_CONTINUATION = "ny_sell_side_breakdown_continuation"
    UNCLEAR_BUY_SIDE_INTERACTION = "unclear_buy_side_interaction"
    UNCLEAR_SELL_SIDE_INTERACTION = "unclear_sell_side_interaction"
    OUTSIDE_NY_WINDOW = "outside_new_york_window"


class NewYorkRaidDirection(str, Enum):
    NONE = "none"
    BULLISH = "bullish"
    BEARISH = "bearish"
    BULLISH_CANDIDATE = "bullish_candidate"
    BEARISH_CANDIDATE = "bearish_candidate"
    BULLISH_CONTINUATION = "bullish_continuation"
    BEARISH_CONTINUATION = "bearish_continuation"


class NewYorkLiquiditySide(str, Enum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


class NewYorkReclaimStatus(str, Enum):
    NONE = "none"
    BUY_SIDE_SWEEP_REJECTED = "buy_side_sweep_rejected"
    SELL_SIDE_SWEEP_RECLAIMED = "sell_side_sweep_reclaimed"
    BUY_SIDE_BREAKOUT_ACCEPTED = "buy_side_breakout_accepted"
    SELL_SIDE_BREAKDOWN_ACCEPTED = "sell_side_breakdown_accepted"
    UNCLEAR = "unclear"


class NewYorkNewsStatus(str, Enum):
    SAFE = "safe"
    CAUTION = "news_caution"
    BLACKOUT = "news_blackout"


class NewYorkConfidenceGrade(str, Enum):
    INVALID = "invalid"
    WATCHLIST = "watchlist"
    VALID = "valid"
    STRONG = "strong"


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
    def bullish_close_position(self) -> float:
        if self.range <= 0:
            return 0.5
        return (self.close - self.low) / self.range

    @property
    def bearish_close_position(self) -> float:
        if self.range <= 0:
            return 0.5
        return (self.high - self.close) / self.range


@dataclass(frozen=True, slots=True)
class _LiquidityLevel:
    liquidity_id: str
    level_type: str
    direction: NewYorkLiquiditySide
    price: float
    zone_low: float
    zone_high: float
    session_source: str
    timeframe: str
    swept_status: str
    quality_score: float
    target_priority_score: float


@dataclass(frozen=True, slots=True)
class _NewYorkWindow:
    name: str
    start_time: time
    end_time: time
    timezone: tzinfo
    timezone_name: str
    allowed_days: set[str]
    strict_mode: bool
    post_window_buffer_minutes: int


_FIXED_ZONE_FALLBACKS: dict[str, int] = {
    "America/New_York": -4,
    "Europe/London": 1,
}


def detect_new_york_open_raid(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    session_levels: Mapping[str, Any],
    news_filter: Mapping[str, Any],
    htf_bias: str,
    *,
    minimum_valid_score: float = 7.0,
    sweep_buffer: float | None = None,
    close_buffer: float | None = None,
    break_buffer: float | None = None,
    stop_buffer: float | None = None,
    atr_period: int = 14,
    min_displacement_body_ratio: float = 0.55,
    min_displacement_range_ratio: float = 1.0,
    min_rr: float = 0.8,
) -> dict[str, Any]:
    """Detect the best New York Open liquidity raid or continuation candidate."""
    warnings: list[str] = []
    candles = [c for c in _normalize_candles(df) if c.is_closed]
    window = _parse_ny_window(session_levels.get("ny_window", {}), warnings)
    levels = _parse_liquidity_levels(session_levels)
    news = _parse_news_filter(news_filter)

    if news["news_status"] is NewYorkNewsStatus.BLACKOUT:
        return _empty_result(
            NewYorkRaidType.NEWS_BLACKOUT,
            "High-impact news inside blackout window",
            session_levels,
            news,
            htf_bias,
            warnings
            + [
                "Skip NY open raid detection during news blackout",
                "Do not treat news spike as clean displacement",
            ],
            confidence_score=1.5,
        )
    if not candles or window is None or not levels:
        return _empty_result(
            NewYorkRaidType.NONE,
            "missing_closed_candles_ny_window_or_liquidity_levels",
            session_levels,
            news,
            htf_bias,
            warnings,
        )

    avg_range = _average_ranges(candles, atr_period)
    sweep = sweep_buffer if sweep_buffer is not None else _default_buffer(levels, avg_range, 0.05)
    close = close_buffer if close_buffer is not None else _default_buffer(levels, avg_range, 0.04)
    brk = break_buffer if break_buffer is not None else _default_buffer(levels, avg_range, 0.04)
    stop = stop_buffer if stop_buffer is not None else _default_buffer(levels, avg_range, 0.08)
    ny_candles = _filter_ny_window(candles, window)
    if not ny_candles:
        return _empty_result(
            NewYorkRaidType.OUTSIDE_NY_WINDOW,
            "no_closed_candles_inside_new_york_open_window",
            session_levels,
            news,
            htf_bias,
            warnings,
        )

    candidates: list[dict[str, Any]] = []
    for offset, candle in enumerate(ny_candles):
        for level in levels:
            candidate = _classify_level_interaction(
                candle,
                offset,
                ny_candles,
                levels,
                level,
                session_levels,
                news,
                htf_bias,
                sweep,
                close,
                brk,
                stop,
                avg_range,
                min_displacement_body_ratio,
                min_displacement_range_ratio,
                min_rr,
                minimum_valid_score,
            )
            if candidate:
                candidates.append(candidate)

    if not candidates:
        return _empty_result(
            NewYorkRaidType.NONE,
            "no_new_york_open_liquidity_raid_detected",
            session_levels,
            news,
            htf_bias,
            warnings,
        )

    candidates.sort(
        key=lambda c: (
            c["valid_setup"],
            _candidate_priority(c["setup_type"]),
            c["confidence_score"],
            c["swept_level"]["target_priority_score"],
        ),
        reverse=True,
    )
    best = candidates[0]
    best["raid_candidates"] = candidates
    best["warnings"] = _dedupe(best["warnings"] + warnings)
    return best


def _candidate_priority(setup_type: str) -> int:
    if setup_type in {
        NewYorkRaidType.BUY_SIDE_SWEEP_REVERSAL.value,
        NewYorkRaidType.SELL_SIDE_SWEEP_REVERSAL.value,
    }:
        return 5
    if setup_type in {
        NewYorkRaidType.BUY_SIDE_SWEEP_CANDIDATE.value,
        NewYorkRaidType.SELL_SIDE_SWEEP_CANDIDATE.value,
    }:
        return 4
    if setup_type in {
        NewYorkRaidType.BUY_SIDE_BREAKOUT_CONTINUATION.value,
        NewYorkRaidType.SELL_SIDE_BREAKDOWN_CONTINUATION.value,
    }:
        return 3
    if setup_type in {
        NewYorkRaidType.UNCLEAR_BUY_SIDE_INTERACTION.value,
        NewYorkRaidType.UNCLEAR_SELL_SIDE_INTERACTION.value,
    }:
        return 1
    return 0


def _classify_level_interaction(
    candle: _Candle,
    offset: int,
    ny_candles: Sequence[_Candle],
    levels: Sequence[_LiquidityLevel],
    level: _LiquidityLevel,
    session_levels: Mapping[str, Any],
    news: Mapping[str, Any],
    htf_bias: str,
    sweep_buffer: float,
    close_buffer: float,
    break_buffer: float,
    stop_buffer: float,
    avg_range: float,
    min_body_ratio: float,
    min_range_ratio: float,
    min_rr: float,
    minimum_valid_score: float,
) -> dict[str, Any] | None:
    if level.direction is NewYorkLiquiditySide.BUY_SIDE:
        if candle.high <= level.zone_high:
            return None
        if candle.high <= level.zone_high + sweep_buffer:
            return _build_candidate(
                candle,
                offset,
                ny_candles,
                levels,
                level,
                session_levels,
                news,
                htf_bias,
                NewYorkRaidType.UNCLEAR_BUY_SIDE_INTERACTION,
                NewYorkRaidDirection.NONE,
                NewYorkReclaimStatus.UNCLEAR,
                candle.high,
                "tiny_wick_above_buy_side_liquidity_below_sweep_buffer",
                stop_buffer,
                avg_range,
                min_body_ratio,
                min_range_ratio,
                break_buffer,
                min_rr,
                minimum_valid_score,
            )
        if candle.close < level.zone_high:
            return _build_candidate(
                candle,
                offset,
                ny_candles,
                levels,
                level,
                session_levels,
                news,
                htf_bias,
                NewYorkRaidType.BUY_SIDE_SWEEP_REVERSAL,
                NewYorkRaidDirection.BEARISH,
                NewYorkReclaimStatus.BUY_SIDE_SWEEP_REJECTED,
                candle.high,
                "candle_high_above_level_and_close_back_below",
                stop_buffer,
                avg_range,
                min_body_ratio,
                min_range_ratio,
                break_buffer,
                min_rr,
                minimum_valid_score,
            )
        if candle.close > level.zone_high + close_buffer:
            if _blocked_by_external_rejection(
                candle,
                levels,
                level,
                sweep_buffer,
                NewYorkLiquiditySide.BUY_SIDE,
            ):
                return None
            return _build_candidate(
                candle,
                offset,
                ny_candles,
                levels,
                level,
                session_levels,
                news,
                htf_bias,
                NewYorkRaidType.BUY_SIDE_BREAKOUT_CONTINUATION,
                NewYorkRaidDirection.BULLISH_CONTINUATION,
                NewYorkReclaimStatus.BUY_SIDE_BREAKOUT_ACCEPTED,
                candle.high,
                "candle_close_accepted_above_buy_side_liquidity",
                stop_buffer,
                avg_range,
                min_body_ratio,
                min_range_ratio,
                break_buffer,
                min_rr,
                minimum_valid_score,
            )
    if level.direction is NewYorkLiquiditySide.SELL_SIDE:
        if candle.low >= level.zone_low:
            return None
        if candle.low >= level.zone_low - sweep_buffer:
            return _build_candidate(
                candle,
                offset,
                ny_candles,
                levels,
                level,
                session_levels,
                news,
                htf_bias,
                NewYorkRaidType.UNCLEAR_SELL_SIDE_INTERACTION,
                NewYorkRaidDirection.NONE,
                NewYorkReclaimStatus.UNCLEAR,
                candle.low,
                "tiny_wick_below_sell_side_liquidity_below_sweep_buffer",
                stop_buffer,
                avg_range,
                min_body_ratio,
                min_range_ratio,
                break_buffer,
                min_rr,
                minimum_valid_score,
            )
        if candle.close > level.zone_low:
            return _build_candidate(
                candle,
                offset,
                ny_candles,
                levels,
                level,
                session_levels,
                news,
                htf_bias,
                NewYorkRaidType.SELL_SIDE_SWEEP_REVERSAL,
                NewYorkRaidDirection.BULLISH,
                NewYorkReclaimStatus.SELL_SIDE_SWEEP_RECLAIMED,
                candle.low,
                "candle_low_below_level_and_close_back_above",
                stop_buffer,
                avg_range,
                min_body_ratio,
                min_range_ratio,
                break_buffer,
                min_rr,
                minimum_valid_score,
            )
        if candle.close < level.zone_low - close_buffer:
            if _blocked_by_external_rejection(
                candle,
                levels,
                level,
                sweep_buffer,
                NewYorkLiquiditySide.SELL_SIDE,
            ):
                return None
            return _build_candidate(
                candle,
                offset,
                ny_candles,
                levels,
                level,
                session_levels,
                news,
                htf_bias,
                NewYorkRaidType.SELL_SIDE_BREAKDOWN_CONTINUATION,
                NewYorkRaidDirection.BEARISH_CONTINUATION,
                NewYorkReclaimStatus.SELL_SIDE_BREAKDOWN_ACCEPTED,
                candle.low,
                "candle_close_accepted_below_sell_side_liquidity",
                stop_buffer,
                avg_range,
                min_body_ratio,
                min_range_ratio,
                break_buffer,
                min_rr,
                minimum_valid_score,
            )
    return None


def _blocked_by_external_rejection(
    candle: _Candle,
    levels: Sequence[_LiquidityLevel],
    level: _LiquidityLevel,
    sweep_buffer: float,
    side: NewYorkLiquiditySide,
) -> bool:
    if side is NewYorkLiquiditySide.BUY_SIDE:
        return any(
            external.direction is NewYorkLiquiditySide.BUY_SIDE
            and external.price > level.price
            and candle.high > external.zone_high + sweep_buffer
            and candle.close < external.zone_high
            for external in levels
        )
    return any(
        external.direction is NewYorkLiquiditySide.SELL_SIDE
        and external.price < level.price
        and candle.low < external.zone_low - sweep_buffer
        and candle.close > external.zone_low
        for external in levels
    )


def _build_candidate(
    candle: _Candle,
    offset: int,
    ny_candles: Sequence[_Candle],
    levels: Sequence[_LiquidityLevel],
    swept_level: _LiquidityLevel,
    session_levels: Mapping[str, Any],
    news: Mapping[str, Any],
    htf_bias: str,
    setup_type: NewYorkRaidType,
    direction: NewYorkRaidDirection,
    reclaim_status: NewYorkReclaimStatus,
    sweep_extreme: float,
    sweep_condition: str,
    stop_buffer: float,
    avg_range: float,
    min_body_ratio: float,
    min_range_ratio: float,
    break_buffer: float,
    min_rr: float,
    minimum_valid_score: float,
) -> dict[str, Any]:
    effective_type = setup_type
    confirmation = _confirm_after_sweep(
        ny_candles,
        offset,
        direction,
        avg_range,
        min_body_ratio,
        min_range_ratio,
        break_buffer,
    )
    if setup_type is NewYorkRaidType.BUY_SIDE_SWEEP_REVERSAL and not confirmation["mss_confirmed"]:
        effective_type = NewYorkRaidType.BUY_SIDE_SWEEP_CANDIDATE
        direction = NewYorkRaidDirection.BEARISH_CANDIDATE
    if setup_type is NewYorkRaidType.SELL_SIDE_SWEEP_REVERSAL and not confirmation["mss_confirmed"]:
        effective_type = NewYorkRaidType.SELL_SIDE_SWEEP_CANDIDATE
        direction = NewYorkRaidDirection.BULLISH_CANDIDATE

    fvg_entry = _entry_zone(ny_candles, offset, direction, confirmation)
    target = _select_target_liquidity(levels, direction, fvg_entry, swept_level)
    risk_plan = _risk_plan(direction, fvg_entry, sweep_extreme, stop_buffer, target)
    htf_alignment = _htf_alignment(direction, htf_bias)
    london_trend = str(session_levels.get("london_trend") or "unknown").lower()
    continuation_context = _continuation_context(direction, london_trend, htf_bias)
    failed = _failed_requirements(
        effective_type,
        confirmation,
        fvg_entry,
        target,
        risk_plan,
        news,
        continuation_context,
        min_rr,
    )
    structurally_valid = not failed and effective_type in {
        NewYorkRaidType.BUY_SIDE_SWEEP_REVERSAL,
        NewYorkRaidType.SELL_SIDE_SWEEP_REVERSAL,
        NewYorkRaidType.BUY_SIDE_BREAKOUT_CONTINUATION,
        NewYorkRaidType.SELL_SIDE_BREAKDOWN_CONTINUATION,
    }
    confidence_score = _confidence_score(
        swept_level,
        effective_type,
        confirmation,
        fvg_entry,
        target,
        risk_plan,
        htf_alignment,
        continuation_context,
        news,
        structurally_valid,
    )
    valid_setup = structurally_valid and confidence_score >= minimum_valid_score
    if structurally_valid and not valid_setup:
        failed.append("confidence_score_below_minimum_valid_score")
    return {
        "concept_name": "New York Open Liquidity Raid",
        "symbol": candle.symbol,
        "timeframe": candle.timeframe,
        "setup_id": (
            f"NY_RAID_{direction.value.upper()}_{candle.timestamp.strftime('%Y%m%d_%H%M%S')}"
            if valid_setup
            else None
        ),
        "valid_setup": valid_setup,
        "setup_type": effective_type.value,
        "classification": effective_type.value,
        "swept_level": _level_payload(swept_level),
        "swept_level_type": swept_level.level_type,
        "swept_liquidity_id": swept_level.liquidity_id,
        "sweep_index": candle.index,
        "sweep_extreme": round(sweep_extreme, 6),
        "sweep": {
            "sweep_index": candle.index,
            "sweep_timestamp": candle.timestamp.isoformat(),
            "sweep_extreme": round(sweep_extreme, 6),
            "reclaim_status": reclaim_status.value,
            "sweep_condition": sweep_condition,
        },
        "direction": direction.value,
        "reclaim_status": reclaim_status.value,
        "mss_confirmed": confirmation["mss_confirmed"],
        "mss": {
            "mss_direction": confirmation["mss_direction"],
            "mss_confirmation_index": confirmation["mss_confirmation_index"],
            "broken_level": confirmation["broken_level"],
            "confirmation_type": confirmation["confirmation_type"],
        },
        "displacement": {
            "displacement_confirmed": confirmation["displacement_confirmed"],
            "direction": confirmation["displacement_direction"],
            "strength": confirmation["displacement_strength"],
            "range_to_atr_ratio": confirmation["range_to_atr_ratio"],
            "body_to_range_ratio": confirmation["body_to_range_ratio"],
        },
        "displacement_confirmed": confirmation["displacement_confirmed"],
        "fvg_entry": fvg_entry,
        "entry_zone": fvg_entry,
        "risk_plan": risk_plan,
        "target_liquidity": _level_payload(target) if target else None,
        "news_filter": _news_payload(news),
        "news_status": news["news_status"].value,
        "context": {
            "htf_bias": htf_bias,
            "htf_alignment": htf_alignment,
            "london_trend": london_trend,
            "continuation_context": continuation_context,
        },
        "confidence_score": confidence_score,
        "confidence_grade": _confidence_grade(confidence_score, valid_setup).value,
        "failed_requirements": failed,
        "reasons": _reasons(
            effective_type,
            swept_level,
            reclaim_status,
            confirmation,
            fvg_entry,
            target,
            htf_alignment,
            continuation_context,
        ),
        "warnings": _candidate_warnings(news),
        "entry_allowed_from_new_york_raid_alone": False,
    }


def _confirm_after_sweep(
    candles: Sequence[_Candle],
    offset: int,
    direction: NewYorkRaidDirection,
    avg_range: float,
    min_body_ratio: float,
    min_range_ratio: float,
    break_buffer: float,
) -> dict[str, Any]:
    follow = list(candles[offset + 1 : offset + 8])
    sweep = candles[offset]
    if not follow:
        return _empty_confirmation()

    bullish = direction in {
        NewYorkRaidDirection.BULLISH,
        NewYorkRaidDirection.BULLISH_CONTINUATION,
    }
    bearish = direction in {
        NewYorkRaidDirection.BEARISH,
        NewYorkRaidDirection.BEARISH_CONTINUATION,
    }
    continuation = direction in {
        NewYorkRaidDirection.BULLISH_CONTINUATION,
        NewYorkRaidDirection.BEARISH_CONTINUATION,
    }
    if bullish:
        mss_candle = None if continuation else next(
            (c for c in follow if c.close > sweep.high + break_buffer),
            None,
        )
        displacement = next(
            (
                c
                for c in follow
                if c.bullish
                and c.body >= c.range * min_body_ratio
                and c.range >= avg_range * min_range_ratio
                and c.bullish_close_position >= 0.70
            ),
            None,
        )
        fvg = _find_bullish_fvg([sweep, *follow])
        fvg_type = "bullish_fvg" if fvg else None
        direction_value = "bullish"
    elif bearish:
        mss_candle = None if continuation else next(
            (c for c in follow if c.close < sweep.low - break_buffer),
            None,
        )
        displacement = next(
            (
                c
                for c in follow
                if c.bearish
                and c.body >= c.range * min_body_ratio
                and c.range >= avg_range * min_range_ratio
                and c.bearish_close_position >= 0.70
            ),
            None,
        )
        fvg = _find_bearish_fvg([sweep, *follow])
        fvg_type = "bearish_fvg" if fvg else None
        direction_value = "bearish"
    else:
        mss_candle = None
        displacement = None
        fvg = None
        fvg_type = None
        direction_value = None

    return {
        "mss_confirmed": False if continuation else mss_candle is not None,
        "mss_direction": None if continuation or not mss_candle else direction_value,
        "mss_confirmation_index": None if continuation or not mss_candle else mss_candle.index,
        "broken_level": _broken_level(sweep, bullish, bearish, mss_candle, continuation),
        "confirmation_type": (
            "bos_acceptance_for_continuation"
            if continuation and displacement
            else "candle_close_after_post_sweep_structure"
            if mss_candle
            else None
        ),
        "displacement_confirmed": displacement is not None,
        "displacement_direction": direction_value if displacement else None,
        "displacement_strength": "strong" if displacement else "none",
        "range_to_atr_ratio": round(displacement.range / avg_range, 4)
        if displacement and avg_range > 0
        else 0.0,
        "body_to_range_ratio": round(displacement.body / displacement.range, 4)
        if displacement and displacement.range > 0
        else 0.0,
        "fvg_created": fvg is not None,
        "fvg_type": fvg_type,
        "fvg_zone": fvg,
    }


def _broken_level(
    sweep: _Candle,
    bullish: bool,
    bearish: bool,
    mss_candle: _Candle | None,
    continuation: bool,
) -> float | None:
    if continuation or not mss_candle:
        return None
    if bullish:
        return round(sweep.high, 6)
    if bearish:
        return round(sweep.low, 6)
    return None


def _entry_zone(
    candles: Sequence[_Candle],
    offset: int,
    direction: NewYorkRaidDirection,
    confirmation: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not confirmation["displacement_confirmed"]:
        return None
    reversal_needs_mss = direction in {
        NewYorkRaidDirection.BULLISH,
        NewYorkRaidDirection.BEARISH,
    }
    if reversal_needs_mss and not confirmation["mss_confirmed"]:
        return None

    fvg = confirmation.get("fvg_zone")
    if fvg:
        bullish = "bullish" in str(confirmation.get("fvg_type"))
        return {
            "entry_zone_type": confirmation["fvg_type"],
            "zone_low": fvg["zone_low"],
            "zone_high": fvg["zone_high"],
            "zone_mid": round((fvg["zone_low"] + fvg["zone_high"]) / 2.0, 6),
            "creation_index": fvg["creation_index"],
            "retest_status": "pending_or_confirmed",
            "entry_price": round((fvg["zone_low"] + fvg["zone_high"]) / 2.0, 6),
            "invalidation_level": round(
                candles[offset].low if bullish else candles[offset].high,
                6,
            ),
        }
    return _order_block_zone(candles, offset, direction)


def _order_block_zone(
    candles: Sequence[_Candle],
    offset: int,
    direction: NewYorkRaidDirection,
) -> dict[str, Any] | None:
    follow = list(candles[offset + 1 : offset + 6])
    if direction in {
        NewYorkRaidDirection.BULLISH,
        NewYorkRaidDirection.BULLISH_CONTINUATION,
    }:
        ob = next((c for c in reversed(follow) if c.bearish), None)
        if ob:
            return {
                "entry_zone_type": "bullish_order_block",
                "zone_low": round(ob.low, 6),
                "zone_high": round(ob.high, 6),
                "zone_mid": round((ob.low + ob.high) / 2.0, 6),
                "creation_index": ob.index,
                "retest_status": "pending_or_confirmed",
                "entry_price": round((ob.low + ob.high) / 2.0, 6),
                "invalidation_level": round(candles[offset].low, 6),
            }
    if direction in {
        NewYorkRaidDirection.BEARISH,
        NewYorkRaidDirection.BEARISH_CONTINUATION,
    }:
        ob = next((c for c in reversed(follow) if c.bullish), None)
        if ob:
            return {
                "entry_zone_type": "bearish_order_block",
                "zone_low": round(ob.low, 6),
                "zone_high": round(ob.high, 6),
                "zone_mid": round((ob.low + ob.high) / 2.0, 6),
                "creation_index": ob.index,
                "retest_status": "pending_or_confirmed",
                "entry_price": round((ob.low + ob.high) / 2.0, 6),
                "invalidation_level": round(candles[offset].high, 6),
            }
    return None


def _select_target_liquidity(
    levels: Sequence[_LiquidityLevel],
    direction: NewYorkRaidDirection,
    entry_zone: Mapping[str, Any] | None,
    swept_level: _LiquidityLevel,
) -> _LiquidityLevel | None:
    entry = float(entry_zone["zone_mid"]) if entry_zone else swept_level.price
    if direction in {
        NewYorkRaidDirection.BULLISH,
        NewYorkRaidDirection.BULLISH_CONTINUATION,
        NewYorkRaidDirection.BULLISH_CANDIDATE,
    }:
        side = NewYorkLiquiditySide.BUY_SIDE
        candidates = [level for level in levels if level.direction is side and level.price > entry]
    elif direction in {
        NewYorkRaidDirection.BEARISH,
        NewYorkRaidDirection.BEARISH_CONTINUATION,
        NewYorkRaidDirection.BEARISH_CANDIDATE,
    }:
        side = NewYorkLiquiditySide.SELL_SIDE
        candidates = [level for level in levels if level.direction is side and level.price < entry]
    else:
        return None
    candidates = [level for level in candidates if level.liquidity_id != swept_level.liquidity_id]
    if not candidates:
        return None
    candidates.sort(
        key=lambda level: (level.target_priority_score, -abs(level.price - entry)),
        reverse=True,
    )
    return candidates[0]


def _risk_plan(
    direction: NewYorkRaidDirection,
    entry_zone: Mapping[str, Any] | None,
    sweep_extreme: float,
    stop_buffer: float,
    target: _LiquidityLevel | None,
) -> dict[str, Any]:
    bullish = direction in {
        NewYorkRaidDirection.BULLISH,
        NewYorkRaidDirection.BULLISH_CONTINUATION,
    }
    bearish = direction in {
        NewYorkRaidDirection.BEARISH,
        NewYorkRaidDirection.BEARISH_CONTINUATION,
    }
    entry = float(entry_zone["zone_mid"]) if entry_zone else None
    if bullish:
        stop = round(sweep_extreme - stop_buffer, 6)
        target_price = target.price if target else None
        rr = _risk_reward(entry, stop, target_price, bullish=True)
        stop_reference = "below_NY_sweep_low_with_ATR_buffer"
    elif bearish:
        stop = round(sweep_extreme + stop_buffer, 6)
        target_price = target.price if target else None
        rr = _risk_reward(entry, stop, target_price, bullish=False)
        stop_reference = "above_NY_sweep_high_with_ATR_buffer"
    else:
        stop = None
        target_price = None
        rr = None
        stop_reference = None
    risk_points = abs(entry - stop) if entry is not None and stop is not None else None
    reward_points = (
        abs(target_price - entry) if entry is not None and target_price is not None else None
    )
    return {
        "entry": round(entry, 6) if entry is not None else None,
        "entry_model": entry_zone.get("entry_zone_type") if entry_zone else None,
        "stop": stop,
        "stop_reference": stop_reference,
        "target": round(target_price, 6) if target_price is not None else None,
        "target_reference": target.level_type if target else None,
        "risk_points": round(risk_points, 6) if risk_points is not None else None,
        "reward_points": round(reward_points, 6) if reward_points is not None else None,
        "risk_reward": rr,
        "position_allowed": False,
        "position_note": "NY raid model is analytics-only until promoted by evidence",
    }


def _failed_requirements(
    setup_type: NewYorkRaidType,
    confirmation: Mapping[str, Any],
    fvg_entry: Mapping[str, Any] | None,
    target: _LiquidityLevel | None,
    risk_plan: Mapping[str, Any],
    news: Mapping[str, Any],
    continuation_context: bool,
    min_rr: float,
) -> list[str]:
    if setup_type in {
        NewYorkRaidType.UNCLEAR_BUY_SIDE_INTERACTION,
        NewYorkRaidType.UNCLEAR_SELL_SIDE_INTERACTION,
    }:
        return ["unclear_interaction_or_below_sweep_buffer"]
    if setup_type in {
        NewYorkRaidType.BUY_SIDE_SWEEP_CANDIDATE,
        NewYorkRaidType.SELL_SIDE_SWEEP_CANDIDATE,
    }:
        return ["mss_not_confirmed_after_ny_raid"]
    failed = []
    reversal = setup_type in {
        NewYorkRaidType.BUY_SIDE_SWEEP_REVERSAL,
        NewYorkRaidType.SELL_SIDE_SWEEP_REVERSAL,
    }
    continuation = setup_type in {
        NewYorkRaidType.BUY_SIDE_BREAKOUT_CONTINUATION,
        NewYorkRaidType.SELL_SIDE_BREAKDOWN_CONTINUATION,
    }
    if reversal and not confirmation["mss_confirmed"]:
        failed.append("mss_not_confirmed_after_ny_raid")
    if continuation and not continuation_context:
        failed.append("london_trend_or_htf_context_not_supporting_continuation")
    if not confirmation["displacement_confirmed"]:
        failed.append("displacement_not_confirmed_after_ny_raid")
    if fvg_entry is None:
        failed.append("no_fvg_or_order_block_entry_zone")
    if target is None:
        failed.append("target_liquidity_missing")
    rr = risk_plan.get("risk_reward")
    if rr is None or rr < min_rr:
        failed.append("risk_reward_below_minimum")
    if news["news_status"] is NewYorkNewsStatus.BLACKOUT:
        failed.append("news_blackout_no_trade")
    return failed


def _confidence_score(
    swept_level: _LiquidityLevel,
    setup_type: NewYorkRaidType,
    confirmation: Mapping[str, Any],
    fvg_entry: Mapping[str, Any] | None,
    target: _LiquidityLevel | None,
    risk_plan: Mapping[str, Any],
    htf_alignment: bool | None,
    continuation_context: bool,
    news: Mapping[str, Any],
    structurally_valid: bool,
) -> float:
    if setup_type in {
        NewYorkRaidType.UNCLEAR_BUY_SIDE_INTERACTION,
        NewYorkRaidType.UNCLEAR_SELL_SIDE_INTERACTION,
    }:
        return 2.5
    if setup_type in {
        NewYorkRaidType.BUY_SIDE_SWEEP_CANDIDATE,
        NewYorkRaidType.SELL_SIDE_SWEEP_CANDIDATE,
    }:
        return 4.0
    score = min(2.0, swept_level.quality_score / 5.0)
    score += 1.5
    if confirmation["mss_confirmed"]:
        score += 1.5
    if continuation_context:
        score += 0.9
    if confirmation["displacement_confirmed"]:
        score += 1.5
    if confirmation["fvg_created"]:
        score += 1.0
    elif fvg_entry is not None:
        score += 0.5
    if target:
        score += min(1.0, target.target_priority_score / 10.0)
    if risk_plan.get("risk_reward"):
        score += min(0.8, float(risk_plan["risk_reward"]) / 3.0)
    if htf_alignment is True:
        score += 0.7
    elif htf_alignment is False:
        score -= 0.7
    if news["news_status"] is NewYorkNewsStatus.CAUTION:
        score -= 1.5
    if not structurally_valid:
        score = min(score, 5.0)
    return round(max(0.0, min(10.0, score)), 4)


def _confidence_grade(score: float, valid_setup: bool) -> NewYorkConfidenceGrade:
    if not valid_setup:
        return NewYorkConfidenceGrade.INVALID if score < 5.0 else NewYorkConfidenceGrade.WATCHLIST
    if score >= 8.0:
        return NewYorkConfidenceGrade.STRONG
    if score >= 7.0:
        return NewYorkConfidenceGrade.VALID
    return NewYorkConfidenceGrade.WATCHLIST


def _parse_liquidity_levels(session_levels: Mapping[str, Any]) -> list[_LiquidityLevel]:
    levels: list[_LiquidityLevel] = []
    tolerance = float(session_levels.get("liquidity_tolerance", 0.0))
    templates = [
        ("london_high", NewYorkLiquiditySide.BUY_SIDE, "london_session"),
        ("london_low", NewYorkLiquiditySide.SELL_SIDE, "london_session"),
        ("asian_high", NewYorkLiquiditySide.BUY_SIDE, "asian_session"),
        ("asian_low", NewYorkLiquiditySide.SELL_SIDE, "asian_session"),
        ("pdh", NewYorkLiquiditySide.BUY_SIDE, "previous_day"),
        ("pdl", NewYorkLiquiditySide.SELL_SIDE, "previous_day"),
        ("previous_day_high", NewYorkLiquiditySide.BUY_SIDE, "previous_day"),
        ("previous_day_low", NewYorkLiquiditySide.SELL_SIDE, "previous_day"),
    ]
    for key, side, source in templates:
        value = session_levels.get(key)
        if value is None:
            continue
        level_type = _canonical_level_type(key)
        levels.append(
            _LiquidityLevel(
                liquidity_id=str(session_levels.get(f"{key}_id") or level_type.upper()),
                level_type=level_type,
                direction=side,
                price=float(value),
                zone_low=float(session_levels.get(f"{key}_zone_low", float(value) - tolerance)),
                zone_high=float(session_levels.get(f"{key}_zone_high", float(value) + tolerance)),
                session_source=str(session_levels.get(f"{key}_session_source") or source),
                timeframe=str(session_levels.get(f"{key}_timeframe") or "session"),
                swept_status=str(session_levels.get(f"{key}_swept_status") or "active"),
                quality_score=float(session_levels.get(f"{key}_quality_score", 8.0)),
                target_priority_score=float(
                    session_levels.get(f"{key}_target_priority_score", 8.0)
                ),
            )
        )
    for raw in session_levels.get("liquidity_pools", []) or []:
        if not isinstance(raw, Mapping) or raw.get("price") is None:
            continue
        side = NewYorkLiquiditySide(str(raw.get("direction") or raw.get("side")))
        price = float(raw["price"])
        liquidity_id = str(raw.get("liquidity_id") or raw.get("id") or f"LIQ_{len(levels)}")
        level_type = str(raw.get("liquidity_type") or raw.get("level_type") or "liquidity_pool")
        levels.append(
            _LiquidityLevel(
                liquidity_id=liquidity_id,
                level_type=level_type,
                direction=side,
                price=price,
                zone_low=float(raw.get("zone_low", price - tolerance)),
                zone_high=float(raw.get("zone_high", price + tolerance)),
                session_source=str(raw.get("session_source") or "custom"),
                timeframe=str(raw.get("timeframe") or "unknown"),
                swept_status=str(raw.get("swept_status") or "active"),
                quality_score=float(raw.get("quality_score", 7.0)),
                target_priority_score=float(raw.get("target_priority_score", 7.0)),
            )
        )
    return levels


def _canonical_level_type(key: str) -> str:
    return {
        "pdh": "previous_day_high",
        "pdl": "previous_day_low",
    }.get(key, key)


def _parse_news_filter(raw: Mapping[str, Any]) -> dict[str, Any]:
    high_impact = bool(raw.get("high_impact_news_nearby", False))
    allow = bool(raw.get("allow_trading_during_news", False))
    spread_high = bool(raw.get("spread_high", False))
    if high_impact and not allow:
        status = NewYorkNewsStatus.BLACKOUT
    elif high_impact or spread_high:
        status = NewYorkNewsStatus.CAUTION
    else:
        status = NewYorkNewsStatus.SAFE
    return {
        "news_status": status,
        "high_impact_news_nearby": high_impact,
        "allow_trading_during_news": allow,
        "spread_high": spread_high,
        "impact_level": raw.get("impact_level"),
        "currency": raw.get("currency"),
        "news_name": raw.get("news_name"),
        "minutes_to_news": raw.get("minutes_to_news"),
        "blackout_before_minutes": raw.get("blackout_before_minutes"),
        "blackout_after_minutes": raw.get("blackout_after_minutes"),
    }


def _parse_ny_window(raw: Mapping[str, Any], warnings: list[str]) -> _NewYorkWindow | None:
    start = _parse_time(raw.get("start_time") or "08:00")
    end = _parse_time(raw.get("end_time") or "11:00")
    if start is None or end is None or start == end:
        warnings.append("invalid_new_york_window_time")
        return None
    timezone_name = str(raw.get("timezone") or "America/New_York")
    tz = _resolve_timezone(timezone_name, warnings)
    return _NewYorkWindow(
        name=str(raw.get("window_name") or "new_york_open"),
        start_time=start,
        end_time=end,
        timezone=tz,
        timezone_name=timezone_name,
        allowed_days=set(raw.get("allowed_days") or []),
        strict_mode=bool(raw.get("strict_mode", True)),
        post_window_buffer_minutes=int(raw.get("post_window_buffer_minutes", 0)),
    )


def _filter_ny_window(candles: Sequence[_Candle], window: _NewYorkWindow) -> list[_Candle]:
    selected = []
    for candle in candles:
        converted = candle.timestamp.astimezone(window.timezone)
        if window.allowed_days and converted.strftime("%A") not in window.allowed_days:
            continue
        if _time_inside_ny_window(converted, window):
            selected.append(candle)
    return selected


def _time_inside_ny_window(value: datetime, window: _NewYorkWindow) -> bool:
    if _time_inside(value.time(), window.start_time, window.end_time):
        return True
    if window.post_window_buffer_minutes <= 0:
        return False
    end_value = value.replace(
        hour=window.end_time.hour,
        minute=window.end_time.minute,
        second=0,
        microsecond=0,
    )
    if window.start_time > window.end_time and value.time() > window.end_time:
        end_value += timedelta(days=1)
    return end_value < value <= end_value + timedelta(
        minutes=window.post_window_buffer_minutes
    )


def _normalize_candles(df: Sequence[Mapping[str, Any] | Any] | Any) -> list[_Candle]:
    records = df.to_dict("records") if hasattr(df, "to_dict") else list(df or [])
    candles: list[_Candle] = []
    for fallback_index, row in enumerate(records):
        get = row.get if isinstance(row, Mapping) else lambda k, d=None: getattr(row, k, d)
        timestamp = _coerce_datetime(get("timestamp"))
        if timestamp is None:
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=dt_timezone.utc)
        candles.append(
            _Candle(
                index=int(get("index", fallback_index)),
                timestamp=timestamp,
                open=float(get("open", 0.0)),
                high=float(get("high", 0.0)),
                low=float(get("low", 0.0)),
                close=float(get("close", 0.0)),
                volume=float(get("volume", 0.0)),
                timeframe=str(get("timeframe", "unknown")),
                symbol=str(get("symbol", "unknown")),
                is_closed=bool(get("is_closed", True)),
            )
        )
    candles.sort(key=lambda candle: candle.timestamp)
    return candles


def _find_bullish_fvg(candles: Sequence[_Candle]) -> dict[str, Any] | None:
    for i in range(len(candles) - 2):
        if candles[i].high < candles[i + 2].low:
            return {
                "zone_low": round(candles[i].high, 6),
                "zone_high": round(candles[i + 2].low, 6),
                "creation_index": candles[i + 2].index,
            }
    return None


def _find_bearish_fvg(candles: Sequence[_Candle]) -> dict[str, Any] | None:
    for i in range(len(candles) - 2):
        if candles[i].low > candles[i + 2].high:
            return {
                "zone_low": round(candles[i + 2].high, 6),
                "zone_high": round(candles[i].low, 6),
                "creation_index": candles[i + 2].index,
            }
    return None


def _continuation_context(
    direction: NewYorkRaidDirection,
    london_trend: str,
    htf_bias: str,
) -> bool:
    bias = htf_bias.lower()
    if direction is NewYorkRaidDirection.BULLISH_CONTINUATION:
        return london_trend == "bullish" and bias not in {"strongly_bearish", "bearish"}
    if direction is NewYorkRaidDirection.BEARISH_CONTINUATION:
        return london_trend == "bearish" and bias not in {"strongly_bullish", "bullish"}
    return False


def _htf_alignment(direction: NewYorkRaidDirection, htf_bias: str) -> bool | None:
    bias = str(htf_bias).lower()
    if bias in {"neutral", "ranging", "unknown", "none", ""}:
        return None
    if direction in {
        NewYorkRaidDirection.BULLISH,
        NewYorkRaidDirection.BULLISH_CONTINUATION,
    }:
        return bias == "bullish"
    if direction in {
        NewYorkRaidDirection.BEARISH,
        NewYorkRaidDirection.BEARISH_CONTINUATION,
    }:
        return bias == "bearish"
    return None


def _risk_reward(
    entry: float | None,
    stop: float | None,
    target: float | None,
    *,
    bullish: bool,
) -> float | None:
    if entry is None or stop is None or target is None:
        return None
    risk = entry - stop if bullish else stop - entry
    reward = target - entry if bullish else entry - target
    if risk <= 0 or reward <= 0:
        return None
    return round(reward / risk, 4)


def _average_ranges(candles: Sequence[_Candle], period: int) -> float:
    ranges = [c.range for c in candles[-period:] if c.range > 0]
    return sum(ranges) / len(ranges) if ranges else 0.00001


def _default_buffer(
    levels: Sequence[_LiquidityLevel],
    avg_range: float,
    multiplier: float,
) -> float:
    prices = [level.price for level in levels]
    price_span = max(prices) - min(prices) if len(prices) > 1 else avg_range
    return max(avg_range * multiplier, price_span * multiplier * 0.05, 0.00001)


def _parse_time(value: Any) -> time | None:
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        return time(int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_timezone.utc)
        except ValueError:
            return None
    return None


def _resolve_timezone(name: str, warnings: list[str]) -> tzinfo:
    if not name or name in {"broker_timezone", "broker"}:
        warnings.append("ny_window_timezone_unknown_assumed_UTC")
        return dt_timezone.utc
    if name.upper() == "UTC":
        return dt_timezone.utc
    offset = _offset_timezone(name)
    if offset:
        return offset
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        fallback = _FIXED_ZONE_FALLBACKS.get(name)
        if fallback is not None:
            warnings.append(f"ny_window_fixed_offset_fallback_used:{name}")
            return dt_timezone(timedelta(hours=fallback), name)
        warnings.append(f"ny_window_timezone_unknown_assumed_UTC:{name}")
        return dt_timezone.utc


def _offset_timezone(value: str) -> tzinfo | None:
    value = value.strip()
    if len(value) != 6 or value[0] not in "+-" or value[3] != ":":
        return None
    try:
        hours = int(value[1:3])
        minutes = int(value[4:6])
    except ValueError:
        return None
    sign = 1 if value[0] == "+" else -1
    return dt_timezone(timedelta(hours=hours, minutes=minutes) * sign, value)


def _time_inside(value: time, start: time, end: time) -> bool:
    if start < end:
        return start <= value <= end
    return value >= start or value <= end


def _level_payload(level: _LiquidityLevel) -> dict[str, Any]:
    return {
        "liquidity_id": level.liquidity_id,
        "level_type": level.level_type,
        "price": round(level.price, 6),
        "direction": level.direction.value,
        "zone_low": round(level.zone_low, 6),
        "zone_high": round(level.zone_high, 6),
        "session_source": level.session_source,
        "timeframe": level.timeframe,
        "swept_status": level.swept_status,
        "quality_score": round(level.quality_score, 4),
        "target_priority_score": round(level.target_priority_score, 4),
    }


def _news_payload(news: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(news)
    payload["news_status"] = news["news_status"].value
    return payload


def _empty_confirmation() -> dict[str, Any]:
    return {
        "mss_confirmed": False,
        "mss_direction": None,
        "mss_confirmation_index": None,
        "broken_level": None,
        "confirmation_type": None,
        "displacement_confirmed": False,
        "displacement_direction": None,
        "displacement_strength": "none",
        "range_to_atr_ratio": 0.0,
        "body_to_range_ratio": 0.0,
        "fvg_created": False,
        "fvg_type": None,
        "fvg_zone": None,
    }


def _candidate_warnings(news: Mapping[str, Any]) -> list[str]:
    warnings = [
        "NY open raid alone is not an entry signal",
        "Entry requires FVG/OB retest reaction and valid risk-to-reward",
    ]
    if news["news_status"] is NewYorkNewsStatus.CAUTION:
        warnings.append("News/spread caution reduced confidence")
    return warnings


def _reasons(
    setup_type: NewYorkRaidType,
    swept_level: _LiquidityLevel,
    reclaim_status: NewYorkReclaimStatus,
    confirmation: Mapping[str, Any],
    fvg_entry: Mapping[str, Any] | None,
    target: _LiquidityLevel | None,
    htf_alignment: bool | None,
    continuation_context: bool,
) -> list[str]:
    reasons = [
        f"New York classified event as {setup_type.value}",
        f"Swept or accepted liquidity level: {swept_level.level_type}",
        f"Reclaim status: {reclaim_status.value}",
    ]
    if confirmation["mss_confirmed"]:
        reasons.append("MSS confirmed with candle close after NY raid")
    if continuation_context:
        reasons.append("London trend/HTF context supports continuation model")
    if confirmation["displacement_confirmed"]:
        reasons.append("Displacement confirmed after NY interaction")
    if fvg_entry:
        reasons.append(f"Entry zone detected: {fvg_entry['entry_zone_type']}")
    if target:
        reasons.append(f"Target liquidity exists: {target.level_type}")
    if htf_alignment is True:
        reasons.append("HTF bias aligns with NY raid direction")
    elif htf_alignment is False:
        reasons.append("HTF bias conflicts with NY raid direction")
    return reasons


def _empty_result(
    setup_type: NewYorkRaidType,
    reason: str,
    session_levels: Mapping[str, Any],
    news: Mapping[str, Any],
    htf_bias: str,
    warnings: list[str],
    *,
    confidence_score: float = 0.0,
) -> dict[str, Any]:
    return {
        "concept_name": "New York Open Liquidity Raid",
        "symbol": "unknown",
        "timeframe": "unknown",
        "setup_id": None,
        "valid_setup": False,
        "setup_type": setup_type.value,
        "classification": setup_type.value,
        "swept_level": None,
        "swept_level_type": None,
        "swept_liquidity_id": None,
        "sweep_index": None,
        "sweep_extreme": None,
        "sweep": None,
        "direction": NewYorkRaidDirection.NONE.value,
        "reclaim_status": NewYorkReclaimStatus.NONE.value,
        "mss_confirmed": False,
        "mss": _empty_confirmation(),
        "displacement_confirmed": False,
        "fvg_entry": None,
        "entry_zone": None,
        "risk_plan": None,
        "target_liquidity": None,
        "news_filter": _news_payload(news),
        "news_status": news["news_status"].value,
        "context": {"htf_bias": htf_bias, "htf_alignment": None},
        "confidence_score": confidence_score,
        "confidence_grade": NewYorkConfidenceGrade.INVALID.value,
        "failed_requirements": [reason],
        "reasons": [reason],
        "warnings": _dedupe(warnings),
        "entry_allowed_from_new_york_raid_alone": False,
        "session_levels_keys": sorted(str(key) for key in session_levels.keys()),
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
