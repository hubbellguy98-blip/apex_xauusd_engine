"""News protection and post-news SMC setup detection for XAUUSD.

The module is intentionally defensive: high-impact news blocks new entries,
first-spike candles are not valid entry displacement, and post-news setups need
stabilization, spread safety, sweep/reclaim, MSS, FVG/OB, and valid risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


class NewsRestrictionReason(str, Enum):
    INSIDE_BLACKOUT = "inside_high_impact_news_blackout"
    OUTSIDE_RESTRICTION = "outside_news_restriction"
    NO_RELEVANT_NEWS = "no_relevant_news"


class PostNewsSetupStatus(str, Enum):
    VALID = "valid_post_news_smc_setup"
    INSIDE_BLACKOUT = "still_inside_news_blackout"
    FIRST_SPIKE_UNSAFE = "first_spike_not_clean_displacement"
    SPREAD_TOO_HIGH = "spread_too_high_after_news"
    STRUCTURE_UNSTABLE = "structure_not_stable_yet"
    NO_SETUP = "no_post_news_smc_setup"
    RISK_INVALID = "risk_plan_invalid"


class PostNewsDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class _Candle:
    index: int
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True
    timeframe: str = ""
    symbol: str = "XAUUSD"

    @property
    def range(self) -> float:
        return max(0.0, self.high - self.low)

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open


def is_news_restricted_time(
    timestamp: Any,
    news_calendar: Sequence[Mapping[str, Any]],
    before_minutes: int,
    after_minutes: int,
    *,
    symbol: str = "XAUUSD",
    strategy_timezone: str = "UTC",
    include_medium_impact: bool = False,
) -> dict[str, Any]:
    """Return whether ``timestamp`` is inside a relevant news blackout window."""

    current = _to_datetime(timestamp, strategy_timezone)
    matches: list[dict[str, Any]] = []
    for event in news_calendar:
        if not _is_relevant_news(event, symbol, include_medium_impact):
            continue
        event_time = _event_datetime(event, strategy_timezone)
        event_before = int(_get(event, "blackout_before_minutes", default=before_minutes))
        event_after = int(_get(event, "blackout_after_minutes", default=after_minutes))
        start = event_time - timedelta(minutes=event_before)
        end = event_time + timedelta(minutes=event_after)
        if start <= current <= end:
            matches.append(
                {
                    "event": event,
                    "event_time": event_time,
                    "restriction_start": start,
                    "restriction_end": end,
                    "before_minutes": event_before,
                    "after_minutes": event_after,
                    "distance_minutes": abs((event_time - current).total_seconds()) / 60,
                }
            )

    if not matches:
        relevant = [
            event
            for event in news_calendar
            if _is_relevant_news(event, symbol, include_medium_impact)
        ]
        return {
            "concept_name": "News Filter for XAUUSD",
            "function": "is_news_restricted_time",
            "timestamp": current,
            "symbol": symbol,
            "restricted": False,
            "matched_news_event": None,
            "restriction_window": None,
            "minutes_to_news": None,
            "minutes_after_news": None,
            "trade_permissions": {
                "new_entries_allowed": True,
                "pending_orders_allowed": True,
                "manage_existing_positions_only": False,
                "cancel_pending_orders": False,
            },
            "reason": (
                NewsRestrictionReason.OUTSIDE_RESTRICTION.value
                if relevant
                else NewsRestrictionReason.NO_RELEVANT_NEWS.value
            ),
        }

    match = sorted(
        matches,
        key=lambda item: (_event_priority(item["event"]), item["distance_minutes"]),
    )[0]
    event_time = match["event_time"]
    return {
        "concept_name": "News Filter for XAUUSD",
        "function": "is_news_restricted_time",
        "timestamp": current,
        "symbol": symbol,
        "restricted": True,
        "matched_news_event": _news_payload(match["event"], event_time),
        "restriction_window": {
            "before_minutes": match["before_minutes"],
            "after_minutes": match["after_minutes"],
            "restriction_start": match["restriction_start"],
            "restriction_end": match["restriction_end"],
        },
        "minutes_to_news": (
            round((event_time - current).total_seconds() / 60, 2)
            if current <= event_time
            else None
        ),
        "minutes_after_news": (
            round((current - event_time).total_seconds() / 60, 2)
            if current >= event_time
            else None
        ),
        "trade_permissions": {
            "new_entries_allowed": False,
            "pending_orders_allowed": False,
            "manage_existing_positions_only": True,
            "cancel_pending_orders": True,
        },
        "reason": NewsRestrictionReason.INSIDE_BLACKOUT.value,
    }


def detect_post_news_smc_setup(
    df: Sequence[Mapping[str, Any]],
    news_event: Mapping[str, Any],
    *,
    symbol: str = "XAUUSD",
    before_minutes: int = 30,
    after_minutes: int = 30,
    stabilization_minutes: int = 15,
    news_range_minutes: int = 15,
    spread_data: Mapping[str, Any] | None = None,
    htf_bias: str = "neutral",
    min_rr: float = 1.2,
    atr_period: int = 14,
    sweep_buffer: float = 0.0,
    break_buffer: float = 0.0,
) -> dict[str, Any]:
    """Detect a tradable post-news SMC setup after volatility stabilizes."""

    candles = _to_candles(df, symbol)
    if not candles:
        return _blocked_post_news(
            symbol,
            news_event,
            PostNewsSetupStatus.NO_SETUP,
            ["no_confirmed_closed_candles"],
        )

    event_time = _event_datetime(news_event, "UTC")
    current_time = candles[-1].timestamp
    restriction = is_news_restricted_time(
        current_time,
        [news_event],
        before_minutes,
        after_minutes,
        symbol=symbol,
    )
    if restriction["restricted"]:
        return _blocked_post_news(
            symbol,
            news_event,
            PostNewsSetupStatus.INSIDE_BLACKOUT,
            ["News blackout window has not ended."],
            restriction=restriction,
        )

    restriction_end = event_time + timedelta(
        minutes=int(_get(news_event, "blackout_after_minutes", default=after_minutes))
    )
    minutes_after_news = (current_time - event_time).total_seconds() / 60
    if minutes_after_news < after_minutes + stabilization_minutes:
        return _blocked_post_news(
            symbol,
            news_event,
            PostNewsSetupStatus.STRUCTURE_UNSTABLE,
            ["Post-news stabilization time has not elapsed."],
            restriction=restriction,
        )

    atr = _atr(candles, atr_period)
    news_range = _news_range(candles, event_time, news_range_minutes)
    if news_range is None:
        return _blocked_post_news(
            symbol,
            news_event,
            PostNewsSetupStatus.NO_SETUP,
            ["news_range_not_available"],
            restriction=restriction,
        )

    spread = _spread_status(spread_data or {})
    if not spread["spread_safe"]:
        return _blocked_post_news(
            symbol,
            news_event,
            PostNewsSetupStatus.SPREAD_TOO_HIGH,
            ["Spread or slippage remains unsafe after news."],
            restriction=restriction,
            news_range=news_range,
            spread=spread,
        )

    post_stabilization = [
        candle
        for candle in candles
        if candle.timestamp > restriction_end + timedelta(minutes=stabilization_minutes)
    ]
    if len(post_stabilization) < 3:
        return _blocked_post_news(
            symbol,
            news_event,
            PostNewsSetupStatus.STRUCTURE_UNSTABLE,
            ["Not enough post-news closed candles after stabilization."],
            restriction=restriction,
            news_range=news_range,
            spread=spread,
        )

    volatility = _volatility_status(candles, post_stabilization, atr)
    if not volatility["stable"]:
        return _blocked_post_news(
            symbol,
            news_event,
            PostNewsSetupStatus.FIRST_SPIKE_UNSAFE,
            ["First spike or latest candles remain too abnormal for entry."],
            restriction=restriction,
            news_range=news_range,
            spread=spread,
            volatility=volatility,
        )

    bullish = _find_bullish_setup(post_stabilization, news_range, atr, sweep_buffer, break_buffer)
    bearish = _find_bearish_setup(post_stabilization, news_range, atr, sweep_buffer, break_buffer)
    candidates = [candidate for candidate in [bullish, bearish] if candidate is not None]
    if not candidates:
        return _blocked_post_news(
            symbol,
            news_event,
            PostNewsSetupStatus.NO_SETUP,
            ["No sweep + reclaim/rejection + MSS + FVG/OB after stabilization."],
            restriction=restriction,
            news_range=news_range,
            spread=spread,
            volatility=volatility,
        )

    best = sorted(candidates, key=lambda item: item["confidence_base"], reverse=True)[0]
    risk_plan = _risk_plan(best, news_range, min_rr)
    confidence = _confidence_score(
        best,
        spread,
        volatility,
        risk_plan,
        htf_bias,
        minutes_after_news,
        after_minutes,
    )
    if not risk_plan["entry_allowed"]:
        return _blocked_post_news(
            symbol,
            news_event,
            PostNewsSetupStatus.RISK_INVALID,
            ["Risk-to-reward is poor after news volatility."],
            restriction=restriction,
            news_range=news_range,
            spread=spread,
            volatility=volatility,
            confidence_score=min(confidence, 5.0),
        )

    direction = best["direction"]
    return {
        "concept_name": "Post-News SMC Setup",
        "symbol": symbol,
        "timeframe": candles[-1].timeframe,
        "setup_id": f"POST_NEWS_{direction.upper()}_{best['sweep_candle'].index}",
        "post_news_setup_detected": confidence >= 7.0,
        "direction": direction,
        "news_event": _news_payload(news_event, event_time),
        "news_filter_status": {
            "restricted_time_active": False,
            "minutes_after_news": round(minutes_after_news, 2),
            "spread_status": spread["spread_status"],
            "volatility_status": volatility["volatility_status"],
        },
        "news_range": news_range,
        "sweep": {
            "swept_level": best["swept_level"],
            "swept_side": best["swept_side"],
            "sweep_extreme": best["sweep_extreme"],
            "reclaim_status": best["reclaim_status"],
        },
        "confirmation": {
            "mss_confirmed": best["mss_confirmed"],
            "mss_direction": direction,
            "displacement_after_stabilization": best["displacement_after_stabilization"],
            "fvg_created": best["fvg_created"],
            "entry_zone_type": best["entry_zone"]["entry_zone_type"],
        },
        "entry_zone": best["entry_zone"],
        "risk_plan": risk_plan,
        "confidence_score": confidence,
        "reasons": [
            "News blackout window ended.",
            "Spread normalized.",
            "Post-news structure stabilized.",
            f"{best['swept_side']} news range liquidity was swept and reclaimed/rejected.",
            f"{direction} MSS confirmed with candle close.",
            f"{direction} FVG/OB entry zone formed after stabilization.",
            "Risk-to-reward is acceptable.",
        ],
        "warnings": [
            "Post-news trades remain higher risk than normal session setups.",
            "Do not use the first news spike as an entry signal.",
        ],
        "trade_permissions": {
            "new_entries_allowed": confidence >= 7.0,
            "pending_orders_allowed": confidence >= 7.0,
            "manage_existing_positions_only": False,
        },
    }


def _find_bullish_setup(
    candles: Sequence[_Candle],
    news_range: Mapping[str, Any],
    atr: float,
    sweep_buffer: float,
    break_buffer: float,
) -> dict[str, Any] | None:
    range_low = float(news_range["range_low"])
    for position, candle in enumerate(candles[:-2]):
        if candle.low >= range_low - sweep_buffer or candle.close <= range_low:
            continue
        after = list(candles[position + 1 : position + 8])
        mss_level = max([item.high for item in candles[: position + 1]], default=candle.high)
        mss_candle = next((item for item in after if item.close > mss_level + break_buffer), None)
        if mss_candle is None:
            continue
        fvg = _first_fvg(candles[position:], bullish=True)
        if fvg is None:
            continue
        displacement = (mss_candle.close - candle.low) >= max(atr * 0.7, 0.01)
        return {
            "direction": PostNewsDirection.BULLISH.value,
            "sweep_candle": candle,
            "swept_level": "news_range_low",
            "swept_side": "sell_side",
            "sweep_extreme": candle.low,
            "reclaim_status": "reclaimed_back_above_news_range_low",
            "mss_confirmed": True,
            "displacement_after_stabilization": displacement,
            "fvg_created": True,
            "entry_zone": {
                "entry_zone_type": "bullish_fvg",
                "zone_low": fvg["zone_low"],
                "zone_high": fvg["zone_high"],
                "zone_mid": (fvg["zone_low"] + fvg["zone_high"]) / 2,
                "invalidation_level": candle.low,
            },
            "confidence_base": 7.0 + (1.0 if displacement else 0.0),
        }
    return None


def _find_bearish_setup(
    candles: Sequence[_Candle],
    news_range: Mapping[str, Any],
    atr: float,
    sweep_buffer: float,
    break_buffer: float,
) -> dict[str, Any] | None:
    range_high = float(news_range["range_high"])
    for position, candle in enumerate(candles[:-2]):
        if candle.high <= range_high + sweep_buffer or candle.close >= range_high:
            continue
        after = list(candles[position + 1 : position + 8])
        mss_level = min([item.low for item in candles[: position + 1]], default=candle.low)
        mss_candle = next((item for item in after if item.close < mss_level - break_buffer), None)
        if mss_candle is None:
            continue
        fvg = _first_fvg(candles[position:], bullish=False)
        if fvg is None:
            continue
        displacement = (candle.high - mss_candle.close) >= max(atr * 0.7, 0.01)
        return {
            "direction": PostNewsDirection.BEARISH.value,
            "sweep_candle": candle,
            "swept_level": "news_range_high",
            "swept_side": "buy_side",
            "sweep_extreme": candle.high,
            "reclaim_status": "rejected_back_below_news_range_high",
            "mss_confirmed": True,
            "displacement_after_stabilization": displacement,
            "fvg_created": True,
            "entry_zone": {
                "entry_zone_type": "bearish_fvg",
                "zone_low": fvg["zone_low"],
                "zone_high": fvg["zone_high"],
                "zone_mid": (fvg["zone_low"] + fvg["zone_high"]) / 2,
                "invalidation_level": candle.high,
            },
            "confidence_base": 7.0 + (1.0 if displacement else 0.0),
        }
    return None


def _risk_plan(
    candidate: Mapping[str, Any],
    news_range: Mapping[str, Any],
    min_rr: float,
) -> dict[str, Any]:
    entry = float(candidate["entry_zone"]["zone_mid"])
    stop = float(candidate["entry_zone"]["invalidation_level"])
    if candidate["direction"] == PostNewsDirection.BULLISH.value:
        target = float(news_range["range_high"])
        risk = max(0.0, entry - stop)
        reward = max(0.0, target - entry)
        target_ref = "news_range_high"
    else:
        target = float(news_range["range_low"])
        risk = max(0.0, stop - entry)
        reward = max(0.0, entry - target)
        target_ref = "news_range_low"
    rr = reward / risk if risk > 0 else 0.0
    return {
        "entry": round(entry, 5),
        "stop": round(stop, 5),
        "target": round(target, 5),
        "target_reference": target_ref,
        "risk_reward": round(rr, 2),
        "entry_allowed": rr >= min_rr,
    }


def _confidence_score(
    candidate: Mapping[str, Any],
    spread: Mapping[str, Any],
    volatility: Mapping[str, Any],
    risk_plan: Mapping[str, Any],
    htf_bias: str,
    minutes_after_news: float,
    after_minutes: int,
) -> float:
    score = 1.0
    score += 1.5 if spread["spread_status"] == "normalized" else 0.75
    score += 1.5 if volatility["volatility_status"] == "stabilized" else 0.5
    score += 1.5
    score += 1.0
    score += 1.5 if candidate["mss_confirmed"] else 0.0
    score += 1.0 if candidate["displacement_after_stabilization"] else 0.5
    score += 1.0 if candidate["fvg_created"] else 0.0
    score += 1.0 if risk_plan["entry_allowed"] else 0.0
    if htf_bias in {candidate["direction"], "neutral", "none", ""}:
        score += 0.4
    else:
        score -= 0.8
    if minutes_after_news < after_minutes + 10:
        score -= 0.5
    if not risk_plan["entry_allowed"]:
        score = min(score, 5.0)
    return round(max(0.0, min(10.0, score)), 2)


def _blocked_post_news(
    symbol: str,
    news_event: Mapping[str, Any],
    status: PostNewsSetupStatus,
    failed: Sequence[str],
    *,
    restriction: Mapping[str, Any] | None = None,
    news_range: Mapping[str, Any] | None = None,
    spread: Mapping[str, Any] | None = None,
    volatility: Mapping[str, Any] | None = None,
    confidence_score: float | None = None,
) -> dict[str, Any]:
    event_time = _event_datetime(news_event, "UTC")
    score = confidence_score
    if score is None:
        score = (
            3.0
            if status in {PostNewsSetupStatus.NO_SETUP, PostNewsSetupStatus.RISK_INVALID}
            else 2.0
        )
    if status == PostNewsSetupStatus.INSIDE_BLACKOUT:
        score = min(score, 3.0)
    if status == PostNewsSetupStatus.SPREAD_TOO_HIGH:
        score = min(score, 4.0)
    if status in {PostNewsSetupStatus.STRUCTURE_UNSTABLE, PostNewsSetupStatus.FIRST_SPIKE_UNSAFE}:
        score = min(score, 4.0)
    return {
        "concept_name": "Post-News SMC Setup",
        "symbol": symbol,
        "setup_id": None,
        "post_news_setup_detected": False,
        "direction": None,
        "news_event": _news_payload(news_event, event_time),
        "news_filter_status": {
            "restricted_time_active": bool(restriction and restriction.get("restricted")),
            "spread_status": (spread or {}).get("spread_status", "unknown"),
            "volatility_status": (volatility or {}).get("volatility_status", "unknown"),
        },
        "news_range": news_range,
        "failed_requirements": list(failed),
        "reason": status.value,
        "confidence_score": round(score, 2),
        "trade_permissions": {
            "new_entries_allowed": False,
            "pending_orders_allowed": False,
            "manage_existing_positions_only": True,
        },
        "warnings": [
            "Wait for volatility to calm and structure to form before looking for SMC setup.",
            "Do not use the first news spike as a normal displacement entry.",
        ],
    }


def _news_range(
    candles: Sequence[_Candle],
    event_time: datetime,
    minutes: int,
) -> dict[str, Any] | None:
    start = event_time
    end = event_time + timedelta(minutes=minutes)
    selected = [candle for candle in candles if start <= candle.timestamp <= end]
    if not selected:
        return None
    high = max(candle.high for candle in selected)
    low = min(candle.low for candle in selected)
    return {
        "range_high": high,
        "range_low": low,
        "range_midpoint": (high + low) / 2,
        "range_start": selected[0].timestamp,
        "range_end": selected[-1].timestamp,
        "news_spike_candle_index": selected[0].index,
        "range_size": high - low,
        "range_source": f"first_{minutes}_minutes_after_news",
    }


def _first_fvg(candles: Sequence[_Candle], *, bullish: bool) -> dict[str, float] | None:
    for first, _, third in zip(candles, candles[1:], candles[2:]):
        if bullish and first.high < third.low:
            return {"zone_low": first.high, "zone_high": third.low}
        if not bullish and first.low > third.high:
            return {"zone_low": third.high, "zone_high": first.low}
    return None


def _spread_status(spread_data: Mapping[str, Any]) -> dict[str, Any]:
    current = float(_get(spread_data, "current_spread", default=0.0))
    average = float(_get(spread_data, "average_spread", default=max(current, 1.0)))
    max_allowed = float(_get(spread_data, "max_allowed_spread", default=average * 2.5))
    multiplier = float(_get(spread_data, "spread_multiplier", default=2.5))
    slippage = float(_get(spread_data, "estimated_slippage", default=0.0))
    max_slippage = float(
        _get(spread_data, "max_allowed_slippage", default=max(slippage, 0.0) + 1.0)
    )
    safe = current <= max_allowed and current <= average * multiplier and slippage <= max_slippage
    return {
        "spread_safe": safe,
        "spread_status": "normalized" if safe else "wide",
        "current_spread": current,
        "average_spread": average,
        "max_allowed_spread": max_allowed,
        "estimated_slippage": slippage,
        "max_allowed_slippage": max_slippage,
    }


def _volatility_status(
    all_candles: Sequence[_Candle],
    post_candles: Sequence[_Candle],
    atr: float,
) -> dict[str, Any]:
    latest = list(post_candles[-3:])
    if not latest:
        return {"stable": False, "volatility_status": "unstable", "latest_range_to_atr": None}
    max_latest = max(candle.range for candle in latest)
    ratio = max_latest / atr if atr > 0 else 99.0
    spike = max((candle.range for candle in all_candles), default=0.0)
    stable = ratio <= 2.2 and spike > 0
    return {
        "stable": stable,
        "volatility_status": "stabilized" if stable else "unstable",
        "latest_range_to_atr": round(ratio, 4),
    }


def _atr(candles: Sequence[_Candle], period: int) -> float:
    if len(candles) < 2:
        return 0.0
    ranges: list[float] = []
    for previous, current in zip(candles, candles[1:]):
        true_range = max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        ranges.append(true_range)
    selected = ranges[-period:] if len(ranges) >= period else ranges
    return mean(selected) if selected else 0.0


def _is_relevant_news(
    event: Mapping[str, Any],
    symbol: str,
    include_medium_impact: bool,
) -> bool:
    impact = str(_get(event, "impact", default="")).lower()
    if impact == "medium" and not include_medium_impact:
        return False
    if impact not in {"high", "medium"}:
        return False
    currency = str(_get(event, "currency", default="")).upper()
    affected = [str(item).upper() for item in _get(event, "affected_symbols", default=[]) or []]
    symbol_aliases = _symbol_aliases(symbol)
    if affected and not any(alias in affected for alias in symbol_aliases):
        return False
    if symbol_aliases.intersection({"XAUUSD", "GOLD", "GOLD.I#"}) and currency != "USD":
        return False
    return True


def _symbol_aliases(symbol: str) -> set[str]:
    symbol_upper = symbol.upper()
    aliases = {symbol_upper}
    if "XAU" in symbol_upper or "GOLD" in symbol_upper:
        aliases.update({"XAUUSD", "GOLD", "GOLD.I#"})
    return aliases


def _event_priority(event: Mapping[str, Any]) -> int:
    name = str(_get(event, "event_name", "name", default="")).lower()
    if "fomc" in name:
        return 0
    if any(item in name for item in ["cpi", "nfp", "pce"]):
        return 1
    return 2


def _news_payload(event: Mapping[str, Any], event_time: datetime) -> dict[str, Any]:
    return {
        "event_id": _get(event, "event_id", default=None),
        "event_name": _get(event, "event_name", "name", default="unknown"),
        "currency": _get(event, "currency", default="unknown"),
        "impact": _get(event, "impact", default="unknown"),
        "event_time": event_time,
    }


def _to_candles(rows: Sequence[Mapping[str, Any]], fallback_symbol: str) -> list[_Candle]:
    candles: list[_Candle] = []
    for position, row in enumerate(_records(rows)):
        is_closed = bool(_get(row, "is_closed", "closed", default=True))
        if not is_closed:
            continue
        candles.append(
            _Candle(
                index=int(_get(row, "index", default=position)),
                timestamp=_to_datetime(_get(row, "timestamp", "time", default=position), "UTC"),
                open=float(_get(row, "open", default=0.0)),
                high=float(_get(row, "high", default=0.0)),
                low=float(_get(row, "low", default=0.0)),
                close=float(_get(row, "close", default=0.0)),
                volume=float(_get(row, "volume", default=0.0)),
                is_closed=is_closed,
                timeframe=str(_get(row, "timeframe", default="")),
                symbol=str(_get(row, "symbol", default=fallback_symbol)),
            )
        )
    return sorted(candles, key=lambda candle: (candle.timestamp, candle.index))


def _event_datetime(event: Mapping[str, Any], strategy_timezone: str) -> datetime:
    event_timezone = str(_get(event, "timezone", default=strategy_timezone))
    timestamp = _get(event, "timestamp", "event_time", default=None)
    return _to_datetime(timestamp, event_timezone)


def _to_datetime(value: Any, timezone_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_timezone(timezone_name))
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _timezone(timezone_name: str):
    if timezone_name.upper() == "UTC":
        return timezone.utc
    return ZoneInfo(timezone_name)


def _records(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if hasattr(rows, "to_dict"):
        return list(rows.to_dict("records"))  # type: ignore[call-arg, union-attr]
    return list(rows)


def _get(row: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return default
