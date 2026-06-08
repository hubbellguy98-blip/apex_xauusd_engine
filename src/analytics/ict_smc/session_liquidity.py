"""ICT/SMC session high and low liquidity mapping.

Session highs are buy-side liquidity and session lows are sell-side liquidity.
This module calculates completed session levels and detects later sweeps,
breakouts, or weak raids around those levels. It is analytics-only; session
liquidity is a map and target reference, not an entry signal by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as dt_timezone, tzinfo
from enum import Enum
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class SessionLiquiditySide(str, Enum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


class SessionLevelType(str, Enum):
    SESSION_HIGH = "session_high"
    SESSION_LOW = "session_low"


class SessionSweepStatus(str, Enum):
    SWEPT = "swept"
    BREAKOUT = "breakout"
    BREAKDOWN = "breakdown"
    UNCLEAR_OR_INVALID = "unclear_or_invalid"


class SessionReclaimStatus(str, Enum):
    REJECTED_BACK_BELOW_HIGH = "rejected_back_below_session_high"
    RECLAIMED_BACK_ABOVE_LOW = "reclaimed_back_above_session_low"
    ACCEPTED_ABOVE_HIGH = "accepted_above_session_high"
    ACCEPTED_BELOW_LOW = "accepted_below_session_low"
    UNCLEAR = "unclear"


class SessionExpectedBias(str, Enum):
    BEARISH_POSSIBLE = "bearish_possible"
    BULLISH_POSSIBLE = "bullish_possible"
    BULLISH_CONTINUATION = "bullish_continuation_possible"
    BEARISH_CONTINUATION = "bearish_continuation_possible"
    NEUTRAL = "neutral"


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


@dataclass(frozen=True, slots=True)
class _SessionBounds:
    start: datetime
    end: datetime
    timezone_name: str


_FIXED_ZONE_FALLBACKS: dict[str, int] = {
    "America/New_York": -4,
    "Europe/London": 1,
}


def calculate_session_high_low(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    session_start: str | time | datetime,
    session_end: str | time | datetime,
    timezone: str,
    *,
    session_name: str = "session",
    symbol: str = "unknown",
    timeframe: str | None = None,
    now: datetime | None = None,
    zone_buffer: float | None = None,
    session_date: datetime | None = None,
) -> dict[str, Any]:
    """Calculate high/low liquidity objects for a completed session window."""
    warnings: list[str] = []
    candles = [c for c in _normalize_candles(df, timeframe, symbol) if c.is_closed]
    if not candles:
        return _empty_session_result(session_name, timezone, ["no_closed_candles"])

    tz = _resolve_timezone(timezone, warnings)
    bounds = _session_bounds(candles, session_start, session_end, tz, session_date)
    if bounds is None:
        return _empty_session_result(
            session_name,
            timezone,
            ["invalid_session_start_or_end"],
        )

    converted = [(c, c.timestamp.astimezone(tz)) for c in candles]
    session_candles = [
        c for c, ts in converted if bounds.start <= ts < bounds.end
    ]
    latest_timestamp = max(ts for _, ts in converted)
    now_converted = now.astimezone(tz) if now and now.tzinfo else now
    session_complete = (
        now_converted >= bounds.end if now_converted else latest_timestamp >= bounds.end
    )
    if not session_complete:
        warnings.append("session_window_not_confirmed_complete")
        return _empty_session_result(
            session_name,
            bounds.timezone_name,
            warnings,
            session_complete=False,
        )
    if not session_candles:
        return _empty_session_result(
            session_name,
            bounds.timezone_name,
            warnings + ["no_closed_candles_inside_session_window"],
        )

    high_candle = max(session_candles, key=lambda c: c.high)
    low_candle = min(session_candles, key=lambda c: c.low)
    session_high = high_candle.high
    session_low = low_candle.low
    midpoint = (session_high + session_low) / 2.0
    range_size = max(0.0, session_high - session_low)
    buffer = zone_buffer if zone_buffer is not None else max(range_size * 0.03, 0.00001)
    quality_score = _score_session_level_quality(
        session_candles,
        range_size,
        timezone_valid=not any("unknown" in w for w in warnings),
        session_name=session_name,
    )
    session_date_text = bounds.start.date().isoformat()
    high_liquidity = _liquidity_object(
        session_name,
        session_date_text,
        SessionLevelType.SESSION_HIGH,
        SessionLiquiditySide.BUY_SIDE,
        session_high,
        buffer,
        quality_score,
    )
    low_liquidity = _liquidity_object(
        session_name,
        session_date_text,
        SessionLevelType.SESSION_LOW,
        SessionLiquiditySide.SELL_SIDE,
        session_low,
        buffer,
        quality_score,
    )

    return {
        "concept_name": "Session High and Session Low Liquidity",
        "symbol": session_candles[0].symbol,
        "timeframe": session_candles[0].timeframe,
        "session_name": session_name,
        "session_definition": {
            "session_start": bounds.start.isoformat(),
            "session_end": bounds.end.isoformat(),
            "timezone": bounds.timezone_name,
        },
        "session_date": session_date_text,
        "session_complete": True,
        "session_high": round(session_high, 6),
        "session_low": round(session_low, 6),
        "liquidity_type": "session_high_and_low",
        "session_levels": {
            "session_high": round(session_high, 6),
            "session_low": round(session_low, 6),
            "session_midpoint": round(midpoint, 6),
            "session_range_size": round(range_size, 6),
            "session_open": round(session_candles[0].open, 6),
            "session_close": round(session_candles[-1].close, 6),
            "high_candle_index": high_candle.index,
            "low_candle_index": low_candle.index,
            "session_end_timestamp": bounds.end.isoformat(),
        },
        "session_high_liquidity": high_liquidity,
        "session_low_liquidity": low_liquidity,
        "liquidity_objects": [high_liquidity, low_liquidity],
        "quality_score": quality_score,
        "entry_allowed_from_session_liquidity_alone": False,
        "warnings": _dedupe(
            warnings
            + [
                "Session levels depend on configured timezone",
                "Session liquidity is not an entry signal by itself",
            ]
        ),
    }


def detect_session_liquidity_sweep(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    session_levels: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    sweep_buffer: float | None = None,
    close_buffer: float | None = None,
    atr_period: int = 14,
    break_buffer_multiplier: float = 0.05,
) -> dict[str, Any]:
    """Detect sweeps, breakouts, and weak raids of completed session levels."""
    warnings: list[str] = []
    candles = [c for c in _normalize_candles(df, None, "unknown") if c.is_closed]
    levels = _normalize_session_levels(session_levels)
    if not candles or not levels:
        return {
            "concept_name": "Session Liquidity Sweep",
            "sweep_events": [],
            "summary": {"events_detected": 0},
            "warnings": ["missing_closed_candles_or_session_levels"],
        }

    ranges = _average_ranges(candles, atr_period)
    buffer = sweep_buffer if sweep_buffer is not None else max(ranges * 0.05, 0.00001)
    close = close_buffer if close_buffer is not None else max(
        ranges * break_buffer_multiplier,
        0.00001,
    )
    events: list[dict[str, Any]] = []
    for level in levels:
        end_ts = _coerce_datetime(
            level.get("session_end_timestamp")
            or level.get("session_levels", {}).get("session_end_timestamp")
        )
        after_session = [
            c for c in candles if end_ts is None or c.timestamp > end_ts
        ]
        if not after_session:
            warnings.append(f"no_closed_candles_after_session:{level['session_name']}")
            continue
        for offset, candle in enumerate(after_session):
            event = _classify_high_raid(candle, level, buffer, close, after_session, offset)
            if event:
                events.append(event)
            event = _classify_low_raid(candle, level, buffer, close, after_session, offset)
            if event:
                events.append(event)

    events.sort(key=lambda e: (e["sweep_candle"]["timestamp"], -e["quality_score"]))
    return {
        "concept_name": "Session Liquidity Sweep",
        "symbol": candles[0].symbol,
        "timeframe": candles[0].timeframe,
        "sweep_events": events,
        "summary": {
            "events_detected": len(events),
            "sweeps": sum(1 for e in events if e["sweep_status"] == "swept"),
            "breakouts": sum(
                1
                for e in events
                if e["sweep_status"] in {"breakout", "breakdown"}
            ),
            "unclear_or_invalid": sum(
                1 for e in events if e["sweep_status"] == "unclear_or_invalid"
            ),
        },
        "warnings": _dedupe(
            warnings
            + [
                "Do not trade session liquidity without MSS, displacement, "
                "FVG/OB, targets, and risk validation"
            ]
        ),
    }


def _classify_high_raid(
    candle: _Candle,
    level: Mapping[str, Any],
    sweep_buffer: float,
    close_buffer: float,
    candles: Sequence[_Candle],
    offset: int,
) -> dict[str, Any] | None:
    session_high = float(level["session_high"])
    if candle.high <= session_high:
        return None
    if candle.high <= session_high + sweep_buffer:
        return _build_event(
            candle,
            level,
            SessionLevelType.SESSION_HIGH,
            SessionLiquiditySide.BUY_SIDE,
            session_high,
            SessionSweepStatus.UNCLEAR_OR_INVALID,
            SessionReclaimStatus.UNCLEAR,
            SessionExpectedBias.NEUTRAL,
            False,
            "sweep_too_small_no_confirmation",
            candles,
            offset,
        )
    if candle.close < session_high:
        return _build_event(
            candle,
            level,
            SessionLevelType.SESSION_HIGH,
            SessionLiquiditySide.BUY_SIDE,
            session_high,
            SessionSweepStatus.SWEPT,
            SessionReclaimStatus.REJECTED_BACK_BELOW_HIGH,
            SessionExpectedBias.BEARISH_POSSIBLE,
            False,
            "session_high_swept_and_rejected",
            candles,
            offset,
        )
    if candle.close > session_high + close_buffer:
        return _build_event(
            candle,
            level,
            SessionLevelType.SESSION_HIGH,
            SessionLiquiditySide.BUY_SIDE,
            session_high,
            SessionSweepStatus.BREAKOUT,
            SessionReclaimStatus.ACCEPTED_ABOVE_HIGH,
            SessionExpectedBias.BULLISH_CONTINUATION,
            True,
            "session_high_accepted_as_breakout",
            candles,
            offset,
        )
    return _build_event(
        candle,
        level,
        SessionLevelType.SESSION_HIGH,
        SessionLiquiditySide.BUY_SIDE,
        session_high,
        SessionSweepStatus.UNCLEAR_OR_INVALID,
        SessionReclaimStatus.UNCLEAR,
        SessionExpectedBias.NEUTRAL,
        False,
        "session_high_raid_close_unclear",
        candles,
        offset,
    )


def _classify_low_raid(
    candle: _Candle,
    level: Mapping[str, Any],
    sweep_buffer: float,
    close_buffer: float,
    candles: Sequence[_Candle],
    offset: int,
) -> dict[str, Any] | None:
    session_low = float(level["session_low"])
    if candle.low >= session_low:
        return None
    if candle.low >= session_low - sweep_buffer:
        return _build_event(
            candle,
            level,
            SessionLevelType.SESSION_LOW,
            SessionLiquiditySide.SELL_SIDE,
            session_low,
            SessionSweepStatus.UNCLEAR_OR_INVALID,
            SessionReclaimStatus.UNCLEAR,
            SessionExpectedBias.NEUTRAL,
            False,
            "sweep_too_small_no_confirmation",
            candles,
            offset,
        )
    if candle.close > session_low:
        return _build_event(
            candle,
            level,
            SessionLevelType.SESSION_LOW,
            SessionLiquiditySide.SELL_SIDE,
            session_low,
            SessionSweepStatus.SWEPT,
            SessionReclaimStatus.RECLAIMED_BACK_ABOVE_LOW,
            SessionExpectedBias.BULLISH_POSSIBLE,
            False,
            "session_low_swept_and_reclaimed",
            candles,
            offset,
        )
    if candle.close < session_low - close_buffer:
        return _build_event(
            candle,
            level,
            SessionLevelType.SESSION_LOW,
            SessionLiquiditySide.SELL_SIDE,
            session_low,
            SessionSweepStatus.BREAKDOWN,
            SessionReclaimStatus.ACCEPTED_BELOW_LOW,
            SessionExpectedBias.BEARISH_CONTINUATION,
            True,
            "session_low_accepted_as_breakdown",
            candles,
            offset,
        )
    return _build_event(
        candle,
        level,
        SessionLevelType.SESSION_LOW,
        SessionLiquiditySide.SELL_SIDE,
        session_low,
        SessionSweepStatus.UNCLEAR_OR_INVALID,
        SessionReclaimStatus.UNCLEAR,
        SessionExpectedBias.NEUTRAL,
        False,
        "session_low_raid_close_unclear",
        candles,
        offset,
    )


def _build_event(
    candle: _Candle,
    level: Mapping[str, Any],
    level_type: SessionLevelType,
    side: SessionLiquiditySide,
    swept_level: float,
    status: SessionSweepStatus,
    reclaim: SessionReclaimStatus,
    bias: SessionExpectedBias,
    breakout_confirmed: bool,
    reason: str,
    candles: Sequence[_Candle],
    offset: int,
) -> dict[str, Any]:
    confirmation = _confirmation_after_event(candles, offset, bias)
    target = _target_liquidity(level, level_type, bias)
    quality = _score_sweep_quality(status, reclaim, confirmation, target)
    return {
        "concept_name": "Session Liquidity Sweep",
        "sweep_id": _sweep_id(level, level_type, candle),
        "swept_session": level["session_name"],
        "session_name": level["session_name"],
        "swept_level_type": level_type.value,
        "liquidity_type": level_type.value,
        "swept_side": side.value,
        "swept_level": round(swept_level, 6),
        "sweep_status": status.value,
        "reclaim_status": reclaim.value,
        "breakout_confirmed": breakout_confirmed,
        "expected_bias": bias.value,
        "sweep_candle": _candle_payload(candle),
        "target_liquidity": target,
        "confirmation": confirmation,
        "quality_score": quality,
        "entry_allowed_from_session_liquidity_alone": False,
        "reasons": _event_reasons(status, reclaim, confirmation, target, reason),
        "warnings": [
            "Session liquidity is not an entry signal by itself",
            "Wait for MSS, displacement, FVG/OB retest, and valid risk-to-reward",
        ],
    }


def _confirmation_after_event(
    candles: Sequence[_Candle],
    offset: int,
    bias: SessionExpectedBias,
) -> dict[str, Any]:
    follow = list(candles[offset + 1 : offset + 6])
    if not follow:
        return {
            "mss_confirmed": False,
            "mss_direction": None,
            "displacement_confirmed": False,
            "fvg_after_sweep": False,
            "fvg_type": None,
        }
    bearish = bias in {
        SessionExpectedBias.BEARISH_POSSIBLE,
        SessionExpectedBias.BEARISH_CONTINUATION,
    }
    bullish = bias in {
        SessionExpectedBias.BULLISH_POSSIBLE,
        SessionExpectedBias.BULLISH_CONTINUATION,
    }
    reference = candles[offset]
    avg_range = _average_ranges(candles[max(0, offset - 8) : offset + 1], 8)
    displacement = any(
        c.range >= avg_range * 1.15 and c.body >= c.range * 0.55 for c in follow
    )
    if bearish:
        mss = any(c.close < reference.low for c in follow)
        fvg = _has_bearish_fvg([reference, *follow])
        direction = "bearish" if mss else None
        fvg_type = "bearish_fvg" if fvg else None
    elif bullish:
        mss = any(c.close > reference.high for c in follow)
        fvg = _has_bullish_fvg([reference, *follow])
        direction = "bullish" if mss else None
        fvg_type = "bullish_fvg" if fvg else None
    else:
        mss = False
        fvg = False
        direction = None
        fvg_type = None
    return {
        "mss_confirmed": mss,
        "mss_direction": direction,
        "displacement_confirmed": displacement,
        "fvg_after_sweep": fvg,
        "fvg_type": fvg_type,
    }


def _target_liquidity(
    level: Mapping[str, Any],
    level_type: SessionLevelType,
    bias: SessionExpectedBias,
) -> dict[str, Any]:
    midpoint = float(level["session_midpoint"])
    if bias in {
        SessionExpectedBias.BEARISH_POSSIBLE,
        SessionExpectedBias.BEARISH_CONTINUATION,
    }:
        return {
            "target_side": SessionLiquiditySide.SELL_SIDE.value,
            "first_target": "session_midpoint",
            "first_target_price": round(midpoint, 6),
            "second_target": "session_low",
            "second_target_price": round(float(level["session_low"]), 6),
            "final_target": "PDL_or_external_sell_side_liquidity",
        }
    if bias in {
        SessionExpectedBias.BULLISH_POSSIBLE,
        SessionExpectedBias.BULLISH_CONTINUATION,
    }:
        return {
            "target_side": SessionLiquiditySide.BUY_SIDE.value,
            "first_target": "session_midpoint",
            "first_target_price": round(midpoint, 6),
            "second_target": "session_high",
            "second_target_price": round(float(level["session_high"]), 6),
            "final_target": "PDH_or_external_buy_side_liquidity",
        }
    return {
        "target_side": (
            SessionLiquiditySide.SELL_SIDE.value
            if level_type is SessionLevelType.SESSION_HIGH
            else SessionLiquiditySide.BUY_SIDE.value
        ),
        "target": "none_until_reclaim_or_breakout_confirms",
    }


def _score_sweep_quality(
    status: SessionSweepStatus,
    reclaim: SessionReclaimStatus,
    confirmation: Mapping[str, Any],
    target: Mapping[str, Any],
) -> float:
    if status is SessionSweepStatus.UNCLEAR_OR_INVALID:
        return 2.0
    score = 4.0 if status in {SessionSweepStatus.BREAKOUT, SessionSweepStatus.BREAKDOWN} else 5.0
    if reclaim is not SessionReclaimStatus.UNCLEAR:
        score += 1.0
    if confirmation.get("mss_confirmed"):
        score += 1.0
    if confirmation.get("displacement_confirmed"):
        score += 1.0
    if confirmation.get("fvg_after_sweep"):
        score += 0.7
    if target.get("first_target") and target.get("second_target"):
        score += 0.8
    return round(max(0.0, min(10.0, score)), 4)


def _normalize_session_levels(
    session_levels: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    raw_levels: Sequence[Mapping[str, Any]]
    if isinstance(session_levels, Mapping):
        raw_levels = [session_levels]
    else:
        raw_levels = session_levels
    normalized = []
    for raw in raw_levels or []:
        levels = raw.get("session_levels", raw)
        if "session_high" not in levels or "session_low" not in levels:
            continue
        normalized.append(
            {
                "session_name": raw.get("session_name", "session"),
                "session_high": float(levels["session_high"]),
                "session_low": float(levels["session_low"]),
                "session_midpoint": float(
                    levels.get(
                        "session_midpoint",
                        (float(levels["session_high"]) + float(levels["session_low"])) / 2,
                    )
                ),
                "session_end_timestamp": levels.get(
                    "session_end_timestamp",
                    raw.get("session_end_timestamp"),
                ),
            }
        )
    return normalized


def _normalize_candles(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    timeframe: str | None,
    symbol: str,
) -> list[_Candle]:
    records = df.to_dict("records") if hasattr(df, "to_dict") else list(df or [])
    candles = []
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
                timeframe=str(get("timeframe", timeframe or "unknown")),
                symbol=str(get("symbol", symbol)),
                is_closed=bool(get("is_closed", True)),
            )
        )
    candles.sort(key=lambda c: c.timestamp)
    return candles


def _session_bounds(
    candles: Sequence[_Candle],
    session_start: str | time | datetime,
    session_end: str | time | datetime,
    tz: tzinfo,
    session_date: datetime | None,
) -> _SessionBounds | None:
    start_dt = _coerce_datetime(session_start)
    end_dt = _coerce_datetime(session_end)
    timezone_name = getattr(tz, "key", None) or getattr(tz, "tzname", lambda _: "UTC")(None)
    if start_dt and end_dt:
        return _SessionBounds(start_dt.astimezone(tz), end_dt.astimezone(tz), timezone_name)
    start_time = _parse_time(session_start)
    end_time = _parse_time(session_end)
    if start_time is None or end_time is None:
        return None
    anchor_date = (
        session_date.astimezone(tz).date()
        if session_date and session_date.tzinfo
        else candles[0].timestamp.astimezone(tz).date()
    )
    start = datetime.combine(anchor_date, start_time, tzinfo=tz)
    end = datetime.combine(anchor_date, end_time, tzinfo=tz)
    if end <= start:
        end += timedelta(days=1)
    return _SessionBounds(start, end, timezone_name)


def _resolve_timezone(name: str, warnings: list[str]) -> tzinfo:
    if not name or name in {"broker_timezone", "broker"}:
        warnings.append("timezone_unknown_assumed_UTC")
        return dt_timezone.utc
    offset = _offset_timezone(name)
    if offset is not None:
        return offset
    if name.upper() == "UTC":
        return dt_timezone.utc
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        fallback = _FIXED_ZONE_FALLBACKS.get(name)
        if fallback is not None:
            warnings.append(f"timezone_fixed_offset_fallback_used:{name}")
            return dt_timezone(timedelta(hours=fallback), name)
        warnings.append(f"timezone_unknown_assumed_UTC:{name}")
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


def _parse_time(value: str | time | datetime) -> time | None:
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
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
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _liquidity_object(
    session_name: str,
    session_date: str,
    level_type: SessionLevelType,
    direction: SessionLiquiditySide,
    price: float,
    buffer: float,
    quality_score: float,
) -> dict[str, Any]:
    suffix = "HIGH" if level_type is SessionLevelType.SESSION_HIGH else "LOW"
    target_use = (
        "long_target_or_bearish_sweep_area"
        if direction is SessionLiquiditySide.BUY_SIDE
        else "short_target_or_bullish_sweep_area"
    )
    return {
        "liquidity_id": f"{session_name.upper()}_{suffix}_{session_date.replace('-', '_')}",
        "liquidity_type": f"{session_name}_{level_type.value}",
        "direction": direction.value,
        "price": round(price, 6),
        "zone_low": round(price - buffer, 6),
        "zone_high": round(price + buffer, 6),
        "swept_status": "unswept",
        "target_use": target_use,
        "quality_score": quality_score,
    }


def _score_session_level_quality(
    candles: Sequence[_Candle],
    range_size: float,
    *,
    timezone_valid: bool,
    session_name: str,
) -> float:
    score = 1.0 if timezone_valid else 0.25
    score += 1.5 if range_size > 0 else 0.0
    major_session = any(
        name in session_name.lower()
        for name in ("asian", "london", "new_york")
    )
    score += 1.5 if major_session else 0.75
    score += 1.5 if len(candles) >= 4 else 0.75
    score += 1.0
    score += 1.0
    score += 1.0
    return round(max(0.0, min(10.0, score)), 4)


def _average_ranges(candles: Sequence[_Candle], period: int) -> float:
    ranges = [c.range for c in candles[-period:] if c.range > 0]
    return sum(ranges) / len(ranges) if ranges else 0.00001


def _has_bullish_fvg(candles: Sequence[_Candle]) -> bool:
    return any(candles[i].high < candles[i + 2].low for i in range(len(candles) - 2))


def _has_bearish_fvg(candles: Sequence[_Candle]) -> bool:
    return any(candles[i].low > candles[i + 2].high for i in range(len(candles) - 2))


def _candle_payload(candle: _Candle) -> dict[str, Any]:
    return {
        "index": candle.index,
        "timestamp": candle.timestamp.isoformat(),
        "open": round(candle.open, 6),
        "high": round(candle.high, 6),
        "low": round(candle.low, 6),
        "close": round(candle.close, 6),
    }


def _event_reasons(
    status: SessionSweepStatus,
    reclaim: SessionReclaimStatus,
    confirmation: Mapping[str, Any],
    target: Mapping[str, Any],
    reason: str,
) -> list[str]:
    reasons = [reason]
    if status is SessionSweepStatus.SWEPT:
        reasons.append("Price traded beyond session liquidity and closed back inside")
    if status in {SessionSweepStatus.BREAKOUT, SessionSweepStatus.BREAKDOWN}:
        reasons.append("Price closed beyond the session level with acceptance")
    if reclaim is not SessionReclaimStatus.UNCLEAR:
        reasons.append(f"Reclaim status: {reclaim.value}")
    if confirmation.get("mss_confirmed"):
        reasons.append("MSS confirmed after the session liquidity event")
    if confirmation.get("fvg_after_sweep"):
        reasons.append("FVG appeared after the session liquidity event")
    if target.get("first_target"):
        reasons.append("Clear session target liquidity exists")
    return reasons


def _sweep_id(
    level: Mapping[str, Any],
    level_type: SessionLevelType,
    candle: _Candle,
) -> str:
    return (
        f"{level['session_name'].upper()}_{level_type.value.upper()}_"
        f"{candle.timestamp.strftime('%Y%m%d_%H%M%S')}"
    )


def _empty_session_result(
    session_name: str,
    timezone: str,
    warnings: list[str],
    *,
    session_complete: bool = False,
) -> dict[str, Any]:
    return {
        "concept_name": "Session High and Session Low Liquidity",
        "symbol": "unknown",
        "session_name": session_name,
        "session_definition": {"timezone": timezone},
        "session_complete": session_complete,
        "session_high": None,
        "session_low": None,
        "liquidity_type": "session_high_and_low",
        "session_levels": None,
        "liquidity_objects": [],
        "quality_score": 0.0,
        "entry_allowed_from_session_liquidity_alone": False,
        "warnings": _dedupe(warnings),
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
