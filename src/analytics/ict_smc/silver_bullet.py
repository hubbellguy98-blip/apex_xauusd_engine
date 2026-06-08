"""ICT Silver Bullet setup detection.

Silver Bullet is modeled as a strict sequence:
time window -> liquidity sweep -> displacement -> FVG -> FVG retracement
-> opposing liquidity target.

This module is deterministic analytics only. It does not place orders and should
not be wired directly into execution without the normal risk/firewall checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone as dt_timezone, tzinfo
from enum import Enum
from statistics import mean
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.core.domain.market_data import CandleNode


class SilverBulletDirection(str, Enum):
    NONE = "none"
    BULLISH = "bullish"
    BEARISH = "bearish"


class SilverBulletSweepSide(str, Enum):
    NONE = "none"
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"


class SilverBulletReclaimStatus(str, Enum):
    NONE = "none"
    RECLAIMED_AFTER_SELL_SIDE_SWEEP = "reclaimed_after_sell_side_sweep"
    STRONG_RECLAIM = "strong_reclaim"
    REJECTED_AFTER_BUY_SIDE_SWEEP = "rejected_after_buy_side_sweep"
    STRONG_REJECTION = "strong_rejection"
    ACCEPTED_BELOW_SELL_SIDE_LIQUIDITY = "accepted_below_sell_side_liquidity"
    ACCEPTED_ABOVE_BUY_SIDE_LIQUIDITY = "accepted_above_buy_side_liquidity"
    UNCLEAR = "unclear"


class SilverBulletFVGType(str, Enum):
    NONE = "none"
    BULLISH = "bullish_fvg"
    BEARISH = "bearish_fvg"


class SilverBulletRetestStatus(str, Enum):
    NONE = "none"
    TOUCHED = "touched"
    HALF_FILLED = "half_filled"
    CONFIRMED_REACTION = "confirmed_reaction"
    CONFIRMED_REJECTION = "confirmed_rejection"
    INVALIDATED = "invalidated"


class SilverBulletClassification(str, Enum):
    NO_SETUP = "no_silver_bullet_setup"
    BULLISH = "bullish_silver_bullet"
    BEARISH = "bearish_silver_bullet"
    OUTSIDE_TIME_WINDOW = "outside_time_window"
    FVG_ONLY_NO_SWEEP = "fvg_only_no_sweep"
    SWEEP_WITHOUT_FVG = "sweep_without_fvg"
    NO_TARGET = "no_opposing_liquidity_target"
    RR_TOO_LOW = "risk_reward_below_minimum"
    INCOMPLETE = "incomplete_silver_bullet_sequence"


class SilverBulletQualityGrade(str, Enum):
    INVALID = "invalid"
    WATCHLIST = "watchlist"
    VALID = "valid_setup"
    STRONG = "strong_setup"


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
class _TimeWindow:
    window_name: str
    start_time: time
    end_time: time
    timezone: str
    allowed_days: set[str]
    strict: bool


@dataclass(frozen=True, slots=True)
class _LiquidityPool:
    liquidity_id: str
    liquidity_type: str
    direction: SilverBulletSweepSide
    zone_low: float
    zone_mid: float
    zone_high: float
    swept_status: str
    quality_score: float
    target_priority_score: float


@dataclass(frozen=True, slots=True)
class _SweepCandidate:
    direction: SilverBulletDirection
    swept_side: SilverBulletSweepSide
    pool: _LiquidityPool
    candle: _Candle
    sweep_level: float
    sweep_extreme: float
    reclaim_status: SilverBulletReclaimStatus


@dataclass(frozen=True, slots=True)
class _FVGZone:
    fvg_type: SilverBulletFVGType
    zone_low: float
    zone_high: float
    zone_mid: float
    creation_index: int
    creation_timestamp: datetime


@dataclass(frozen=True, slots=True)
class _Retest:
    status: SilverBulletRetestStatus
    entry: float | None
    candle_index: int | None
    timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class _Target:
    pool: _LiquidityPool
    price: float


def detect_silver_bullet_setup(
    df: Sequence[CandleNode | Mapping[str, Any]] | Any,
    time_window: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    liquidity_pools: Sequence[Mapping[str, Any] | Any],
    htf_bias: str,
    *,
    atr_period: int = 14,
    sweep_buffer: float | None = None,
    buffer_atr_multiplier: float = 0.05,
    close_buffer: float | None = None,
    min_displacement_atr: float = 1.0,
    min_body_to_range: float = 0.55,
    min_close_position: float = 0.70,
    max_displacement_candles: int = 8,
    max_retest_candles: int = 12,
    min_rr: float = 1.0,
    minimum_score: float = 7.0,
    entry_mode: str = "conservative",
    symbol: str = "unknown",
    timeframe: str | None = None,
) -> dict[str, Any]:
    """Detect the highest-quality ICT Silver Bullet setup from closed candles."""
    candles = [candle for candle in _normalize_candles(df, timeframe, symbol) if candle.is_closed]
    windows = _parse_time_windows(time_window)
    pools = _normalize_liquidity_pools(liquidity_pools)
    if not candles or not windows:
        return _empty_result("missing_closed_candles_or_time_window", symbol, timeframe, htf_bias)

    atr = _calculate_atr(candles, atr_period)[-1]
    sweep = sweep_buffer if sweep_buffer is not None else max(atr * buffer_atr_multiplier, 0.00001)
    close = close_buffer if close_buffer is not None else sweep
    window_context, warnings = _window_context(candles, windows)
    window_candles = window_context["window_candles"]
    if not window_candles:
        result = _empty_result(
            "no_closed_candles_inside_configured_time_window",
            candles[0].symbol,
            timeframe,
            htf_bias,
        )
        result["classification"] = SilverBulletClassification.OUTSIDE_TIME_WINDOW.value
        result["time_window"] = window_context["time_window"]
        result["warnings"].extend(warnings)
        if _has_fvg_anywhere(candles):
            result["warnings"].append("valid_price_action_but_not_silver_bullet_outside_window")
        return result

    sweeps = [
        sweep_candidate
        for candle in window_candles
        for sweep_candidate in _sweeps_from_candle(candle, pools, sweep, close)
    ]
    if not sweeps:
        result = _empty_result("no_liquidity_sweep_inside_time_window", candles[0].symbol, timeframe, htf_bias)
        result["classification"] = (
            SilverBulletClassification.FVG_ONLY_NO_SWEEP.value
            if _has_fvg_anywhere(window_candles)
            else SilverBulletClassification.NO_SETUP.value
        )
        result["time_window"] = window_context["time_window"]
        result["score"] = 3.0 if result["classification"] == SilverBulletClassification.FVG_ONLY_NO_SWEEP.value else 0.0
        result["warnings"].extend(warnings + ["Silver Bullet requires a liquidity sweep before FVG analysis"])
        return result

    candidates = [
        _build_candidate_result(
            candles,
            sweep_candidate,
            pools,
            htf_bias,
            atr,
            sweep,
            window_context,
            warnings,
            min_displacement_atr,
            min_body_to_range,
            min_close_position,
            max_displacement_candles,
            max_retest_candles,
            min_rr,
            minimum_score,
            entry_mode,
            timeframe,
        )
        for sweep_candidate in sweeps
    ]
    candidates.sort(key=lambda item: item["score"], reverse=True)
    best = candidates[0]
    best["candidate_count"] = len(candidates)
    best["alternative_candidates"] = [
        {
            "direction": item["direction"],
            "classification": item["classification"],
            "score": item["score"],
            "rr": item["rr"],
            "failed_requirements": item["failed_requirements"],
        }
        for item in candidates[1:4]
    ]
    return best


def _build_candidate_result(
    candles: Sequence[_Candle],
    sweep: _SweepCandidate,
    pools: Sequence[_LiquidityPool],
    htf_bias: str,
    atr: float,
    sweep_buffer: float,
    window_context: Mapping[str, Any],
    inherited_warnings: Sequence[str],
    min_displacement_atr: float,
    min_body_to_range: float,
    min_close_position: float,
    max_displacement_candles: int,
    max_retest_candles: int,
    min_rr: float,
    minimum_score: float,
    entry_mode: str,
    timeframe: str | None,
) -> dict[str, Any]:
    direction = sweep.direction
    displacement = _find_displacement_and_fvg(
        candles,
        sweep.candle.index,
        direction,
        atr,
        min_displacement_atr,
        min_body_to_range,
        min_close_position,
        max_displacement_candles,
    )
    fvg = displacement["fvg_zone"]
    failed: list[str] = []
    warnings = list(inherited_warnings)
    if fvg is None:
        failed.append("no_valid_fvg_created_by_displacement")
        warnings.append("Sweep was detected, but no displacement-created FVG completed the sequence")

    retest = _find_retest(candles, fvg, direction, entry_mode, max_retest_candles) if fvg is not None else None
    if retest is None or retest.status in {SilverBulletRetestStatus.NONE, SilverBulletRetestStatus.INVALIDATED}:
        failed.append("no_confirmed_fvg_retracement_entry")
        warnings.append("Silver Bullet entry requires FVG retracement confirmation")

    entry = retest.entry if retest is not None else None
    stop = _calculate_stop(sweep, fvg, direction, sweep_buffer) if fvg is not None else None
    target = _select_target(pools, direction, entry) if entry is not None else None
    if target is None:
        failed.append("no_valid_opposing_liquidity_target")
        warnings.append("Target must be opposing unswept liquidity, not an arbitrary RR projection")

    rr = _risk_reward(direction, entry, stop, target.price if target is not None else None)
    if rr < min_rr:
        failed.append("risk_reward_below_minimum")

    score, reasons, htf_alignment = _score_candidate(
        sweep,
        displacement,
        fvg,
        retest,
        target,
        rr,
        htf_bias,
        min_rr,
    )
    valid = bool(not failed and score >= minimum_score and rr >= min_rr)
    classification = _classification(valid, direction, fvg, target, rr, min_rr)
    quality_grade = _quality_grade(score, valid)

    if not valid and not failed:
        failed.append("confirmation_score_below_minimum_threshold")
    if htf_alignment is False:
        warnings.append(
            "HTF bias opposes Silver Bullet direction; require stronger local confirmation before execution"
        )

    return {
        "concept_name": "ICT Silver Bullet",
        "setup_id": _setup_id(sweep, fvg),
        "valid_setup": valid,
        "classification": classification.value,
        "quality_grade": quality_grade.value,
        "direction": direction.value if valid else (direction.value if score > 0 else None),
        "time_window": window_context["time_window"],
        "strategy_timezone": window_context["strategy_timezone"],
        "input_timezone": window_context["input_timezone"],
        "window_start_timestamp": window_context["window_start_timestamp"],
        "window_end_timestamp": window_context["window_end_timestamp"],
        "sweep_level": sweep.sweep_level,
        "sweep": {
            "swept_liquidity_id": sweep.pool.liquidity_id,
            "swept_side": sweep.swept_side.value,
            "sweep_level": sweep.sweep_level,
            "sweep_candle_index": sweep.candle.index,
            "sweep_timestamp": sweep.candle.timestamp.isoformat(),
            "sweep_extreme": sweep.sweep_extreme,
            "reclaim_status": sweep.reclaim_status.value,
        },
        "displacement": {
            "displacement_confirmed": displacement["confirmed"],
            "direction": direction.value if displacement["confirmed"] else None,
            "start_index": displacement["start_index"],
            "end_index": displacement["end_index"],
            "strength": displacement["strength"],
            "range_to_atr_ratio": round(displacement["range_to_atr_ratio"], 4),
            "body_to_range_ratio": round(displacement["body_to_range_ratio"], 4),
        },
        "fvg_zone": _fvg_payload(fvg, retest),
        "entry": entry,
        "stop": stop,
        "target": target.price if target is not None else None,
        "trade_plan": {
            "entry": entry,
            "entry_type": "fvg_retest_reaction" if entry_mode != "aggressive" else "fvg_midpoint",
            "stop": stop,
            "stop_reference": _stop_reference(direction),
            "target": target.price if target is not None else None,
            "target_reference": "nearest_opposing_liquidity",
            "target_liquidity_id": target.pool.liquidity_id if target is not None else None,
            "risk_reward": rr,
        },
        "score": round(score, 2),
        "rr": rr,
        "htf_bias": htf_bias,
        "htf_alignment": htf_alignment,
        "atr": atr,
        "xauusd_volatility_buffer": sweep_buffer,
        "failed_requirements": failed,
        "reasons": reasons,
        "warnings": warnings,
        "entry_allowed_from_silver_bullet_alone": False,
        "timeframe": timeframe or sweep.candle.timeframe,
        "symbol": sweep.candle.symbol,
    }


def _sweeps_from_candle(
    candle: _Candle,
    pools: Sequence[_LiquidityPool],
    sweep_buffer: float,
    close_buffer: float,
) -> list[_SweepCandidate]:
    candidates: list[_SweepCandidate] = []
    for pool in pools:
        if pool.swept_status not in {"unswept", "active", "fresh", ""}:
            continue
        if pool.direction == SilverBulletSweepSide.SELL_SIDE and candle.low < pool.zone_low - sweep_buffer:
            if candle.close > pool.zone_high:
                reclaim = SilverBulletReclaimStatus.STRONG_RECLAIM
            elif candle.close > pool.zone_low:
                reclaim = SilverBulletReclaimStatus.RECLAIMED_AFTER_SELL_SIDE_SWEEP
            elif candle.close < pool.zone_low - close_buffer:
                reclaim = SilverBulletReclaimStatus.ACCEPTED_BELOW_SELL_SIDE_LIQUIDITY
            else:
                reclaim = SilverBulletReclaimStatus.UNCLEAR
            if reclaim in {
                SilverBulletReclaimStatus.RECLAIMED_AFTER_SELL_SIDE_SWEEP,
                SilverBulletReclaimStatus.STRONG_RECLAIM,
            }:
                candidates.append(
                    _SweepCandidate(
                        SilverBulletDirection.BULLISH,
                        SilverBulletSweepSide.SELL_SIDE,
                        pool,
                        candle,
                        pool.zone_mid,
                        candle.low,
                        reclaim,
                    )
                )
        if pool.direction == SilverBulletSweepSide.BUY_SIDE and candle.high > pool.zone_high + sweep_buffer:
            if candle.close < pool.zone_low:
                reclaim = SilverBulletReclaimStatus.STRONG_REJECTION
            elif candle.close < pool.zone_high:
                reclaim = SilverBulletReclaimStatus.REJECTED_AFTER_BUY_SIDE_SWEEP
            elif candle.close > pool.zone_high + close_buffer:
                reclaim = SilverBulletReclaimStatus.ACCEPTED_ABOVE_BUY_SIDE_LIQUIDITY
            else:
                reclaim = SilverBulletReclaimStatus.UNCLEAR
            if reclaim in {
                SilverBulletReclaimStatus.REJECTED_AFTER_BUY_SIDE_SWEEP,
                SilverBulletReclaimStatus.STRONG_REJECTION,
            }:
                candidates.append(
                    _SweepCandidate(
                        SilverBulletDirection.BEARISH,
                        SilverBulletSweepSide.BUY_SIDE,
                        pool,
                        candle,
                        pool.zone_mid,
                        candle.high,
                        reclaim,
                    )
                )
    return candidates


def _find_displacement_and_fvg(
    candles: Sequence[_Candle],
    sweep_index: int,
    direction: SilverBulletDirection,
    atr: float,
    min_displacement_atr: float,
    min_body_to_range: float,
    min_close_position: float,
    max_displacement_candles: int,
) -> dict[str, Any]:
    start_pos = _position_for_index(candles, sweep_index) + 1
    end_pos = min(len(candles) - 2, start_pos + max_displacement_candles)
    for pos in range(start_pos, end_pos):
        c1, c2, c3 = candles[pos - 1], candles[pos], candles[pos + 1]
        if not _is_displacement(c2, direction, atr, min_displacement_atr, min_body_to_range, min_close_position):
            continue
        fvg = _fvg_from_triple(c1, c2, c3, direction)
        if fvg is None:
            continue
        body_ratio = c2.body / c2.range if c2.range > 0 else 0.0
        range_ratio = c2.range / atr if atr > 0 else 0.0
        return {
            "confirmed": True,
            "start_index": c2.index,
            "end_index": c3.index,
            "strength": _displacement_strength(range_ratio, body_ratio),
            "range_to_atr_ratio": range_ratio,
            "body_to_range_ratio": body_ratio,
            "fvg_zone": fvg,
        }
    return {
        "confirmed": False,
        "start_index": None,
        "end_index": None,
        "strength": "none",
        "range_to_atr_ratio": 0.0,
        "body_to_range_ratio": 0.0,
        "fvg_zone": None,
    }


def _is_displacement(
    candle: _Candle,
    direction: SilverBulletDirection,
    atr: float,
    min_displacement_atr: float,
    min_body_to_range: float,
    min_close_position: float,
) -> bool:
    if candle.range <= 0 or atr <= 0:
        return False
    body_ratio = candle.body / candle.range
    range_ratio = candle.range / atr
    if body_ratio < min_body_to_range or range_ratio < min_displacement_atr:
        return False
    if direction == SilverBulletDirection.BULLISH:
        return candle.bullish and candle.close_position >= min_close_position
    return candle.bearish and candle.close_position <= 1.0 - min_close_position


def _fvg_from_triple(
    c1: _Candle,
    _c2: _Candle,
    c3: _Candle,
    direction: SilverBulletDirection,
) -> _FVGZone | None:
    if direction == SilverBulletDirection.BULLISH and c1.high < c3.low:
        return _FVGZone(
            SilverBulletFVGType.BULLISH,
            c1.high,
            c3.low,
            (c1.high + c3.low) / 2.0,
            c3.index,
            c3.timestamp,
        )
    if direction == SilverBulletDirection.BEARISH and c1.low > c3.high:
        return _FVGZone(
            SilverBulletFVGType.BEARISH,
            c3.high,
            c1.low,
            (c3.high + c1.low) / 2.0,
            c3.index,
            c3.timestamp,
        )
    return None


def _find_retest(
    candles: Sequence[_Candle],
    fvg: _FVGZone,
    direction: SilverBulletDirection,
    entry_mode: str,
    max_retest_candles: int,
) -> _Retest:
    start_pos = _position_for_index(candles, fvg.creation_index) + 1
    end_pos = min(len(candles), start_pos + max_retest_candles)
    if entry_mode == "aggressive":
        return _Retest(SilverBulletRetestStatus.HALF_FILLED, fvg.zone_mid, None, None)
    for candle in candles[start_pos:end_pos]:
        touches_zone = candle.low <= fvg.zone_high and candle.high >= fvg.zone_low
        if not touches_zone:
            continue
        if direction == SilverBulletDirection.BULLISH:
            if candle.close < fvg.zone_low:
                return _Retest(SilverBulletRetestStatus.INVALIDATED, None, candle.index, candle.timestamp)
            if candle.close > fvg.zone_mid and candle.bullish:
                return _Retest(
                    SilverBulletRetestStatus.CONFIRMED_REACTION,
                    candle.close,
                    candle.index,
                    candle.timestamp,
                )
            return _Retest(SilverBulletRetestStatus.TOUCHED, fvg.zone_mid, candle.index, candle.timestamp)
        if candle.close > fvg.zone_high:
            return _Retest(SilverBulletRetestStatus.INVALIDATED, None, candle.index, candle.timestamp)
        if candle.close < fvg.zone_mid and candle.bearish:
            return _Retest(SilverBulletRetestStatus.CONFIRMED_REJECTION, candle.close, candle.index, candle.timestamp)
        return _Retest(SilverBulletRetestStatus.TOUCHED, fvg.zone_mid, candle.index, candle.timestamp)
    return _Retest(SilverBulletRetestStatus.NONE, None, None, None)


def _select_target(
    pools: Sequence[_LiquidityPool],
    direction: SilverBulletDirection,
    entry: float | None,
) -> _Target | None:
    if entry is None:
        return None
    side = (
        SilverBulletSweepSide.BUY_SIDE
        if direction == SilverBulletDirection.BULLISH
        else SilverBulletSweepSide.SELL_SIDE
    )
    valid_targets = [
        pool
        for pool in pools
        if pool.direction == side
        and pool.swept_status in {"unswept", "active", "fresh", ""}
        and (
            (direction == SilverBulletDirection.BULLISH and pool.zone_mid > entry)
            or (direction == SilverBulletDirection.BEARISH and pool.zone_mid < entry)
        )
    ]
    if not valid_targets:
        return None
    valid_targets.sort(
        key=lambda pool: (
            -pool.target_priority_score,
            abs(pool.zone_mid - entry),
        )
    )
    return _Target(valid_targets[0], valid_targets[0].zone_mid)


def _calculate_stop(
    sweep: _SweepCandidate,
    fvg: _FVGZone | None,
    direction: SilverBulletDirection,
    buffer: float,
) -> float | None:
    if fvg is None:
        return None
    if direction == SilverBulletDirection.BULLISH:
        return min(sweep.sweep_extreme, fvg.zone_low) - buffer
    return max(sweep.sweep_extreme, fvg.zone_high) + buffer


def _risk_reward(
    direction: SilverBulletDirection,
    entry: float | None,
    stop: float | None,
    target: float | None,
) -> float:
    if entry is None or stop is None or target is None:
        return 0.0
    if direction == SilverBulletDirection.BULLISH:
        risk = entry - stop
        reward = target - entry
    else:
        risk = stop - entry
        reward = entry - target
    if risk <= 0 or reward <= 0:
        return 0.0
    return round(reward / risk, 4)


def _score_candidate(
    sweep: _SweepCandidate,
    displacement: Mapping[str, Any],
    fvg: _FVGZone | None,
    retest: _Retest | None,
    target: _Target | None,
    rr: float,
    htf_bias: str,
    min_rr: float,
) -> tuple[float, list[str], bool | None]:
    score = 1.5
    reasons = ["Setup occurred inside configured Silver Bullet window"]
    if sweep.reclaim_status in {
        SilverBulletReclaimStatus.STRONG_RECLAIM,
        SilverBulletReclaimStatus.STRONG_REJECTION,
    }:
        score += 2.2
        reasons.append("Liquidity was swept with strong reclaim/rejection")
    else:
        score += 1.8
        reasons.append("Liquidity was swept and reclaimed/rejected")
    if displacement["confirmed"]:
        strength_bonus = min(2.0, 0.8 + float(displacement["range_to_atr_ratio"]) * 0.55)
        score += strength_bonus
        reasons.append("Displacement formed after the sweep")
    if fvg is not None:
        score += 1.4
        reasons.append("FVG was created by displacement")
    if retest is not None and retest.status in {
        SilverBulletRetestStatus.HALF_FILLED,
        SilverBulletRetestStatus.CONFIRMED_REACTION,
        SilverBulletRetestStatus.CONFIRMED_REJECTION,
    }:
        score += 1.2
        reasons.append("Price retraced into the FVG entry model")
    if target is not None:
        score += 1.0
        reasons.append("Opposing liquidity target exists")
    if rr >= min_rr:
        score += min(0.8, rr * 0.25)
        reasons.append("Risk-to-reward passed minimum requirement")

    alignment = _htf_alignment(sweep.direction, htf_bias)
    if alignment is True:
        score += 0.7
        reasons.append("HTF bias aligns with setup direction")
    elif alignment is False:
        score -= 1.2
        reasons.append("HTF bias conflicts with setup direction")
    else:
        reasons.append("HTF bias is neutral or unknown")
    return max(0.0, min(10.0, score)), reasons, alignment


def _classification(
    valid: bool,
    direction: SilverBulletDirection,
    fvg: _FVGZone | None,
    target: _Target | None,
    rr: float,
    min_rr: float,
) -> SilverBulletClassification:
    if valid and direction == SilverBulletDirection.BULLISH:
        return SilverBulletClassification.BULLISH
    if valid and direction == SilverBulletDirection.BEARISH:
        return SilverBulletClassification.BEARISH
    if fvg is None:
        return SilverBulletClassification.SWEEP_WITHOUT_FVG
    if target is None:
        return SilverBulletClassification.NO_TARGET
    if rr < min_rr:
        return SilverBulletClassification.RR_TOO_LOW
    return SilverBulletClassification.INCOMPLETE


def _quality_grade(score: float, valid: bool) -> SilverBulletQualityGrade:
    if not valid:
        return SilverBulletQualityGrade.INVALID if score < 5.0 else SilverBulletQualityGrade.WATCHLIST
    if score >= 8.5:
        return SilverBulletQualityGrade.STRONG
    return SilverBulletQualityGrade.VALID


def _window_context(candles: Sequence[_Candle], windows: Sequence[_TimeWindow]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    for window in windows:
        tz = _timezone_for_name(window.timezone, warnings)
        window_candles = []
        converted = []
        for candle in candles:
            timestamp = candle.timestamp
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=dt_timezone.utc)
                warnings.append("input_timestamp_timezone_unknown_assumed_UTC")
            local_timestamp = timestamp.astimezone(tz)
            if _day_allowed(local_timestamp, window.allowed_days) and _inside_time(local_timestamp.time(), window):
                window_candles.append(candle)
                converted.append(local_timestamp)
        if window_candles:
            return (
                {
                    "window_candles": window_candles,
                    "strategy_timezone": window.timezone,
                    "input_timezone": _input_timezone_name(candles),
                    "window_start_timestamp": converted[0].isoformat(),
                    "window_end_timestamp": converted[-1].isoformat(),
                    "time_window": {
                        "window_name": window.window_name,
                        "start_time": window.start_time.strftime("%H:%M"),
                        "end_time": window.end_time.strftime("%H:%M"),
                        "timezone": window.timezone,
                        "allowed_days": sorted(window.allowed_days),
                        "strict": window.strict,
                        "window_valid": True,
                    },
                },
                warnings,
            )
    first = windows[0]
    return (
        {
            "window_candles": [],
            "strategy_timezone": first.timezone,
            "input_timezone": _input_timezone_name(candles),
            "window_start_timestamp": None,
            "window_end_timestamp": None,
            "time_window": {
                "window_name": first.window_name,
                "start_time": first.start_time.strftime("%H:%M"),
                "end_time": first.end_time.strftime("%H:%M"),
                "timezone": first.timezone,
                "allowed_days": sorted(first.allowed_days),
                "strict": first.strict,
                "window_valid": False,
            },
        },
        warnings,
    )


def _parse_time_windows(
    time_window: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> list[_TimeWindow]:
    raw_windows = (
        time_window
        if isinstance(time_window, Sequence) and not isinstance(time_window, Mapping)
        else [time_window]
    )
    windows: list[_TimeWindow] = []
    for raw in raw_windows:
        if not isinstance(raw, Mapping):
            continue
        start = _parse_clock(raw.get("start_time", raw.get("start")))
        end = _parse_clock(raw.get("end_time", raw.get("end")))
        if start is None or end is None:
            continue
        allowed = raw.get("allowed_days", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
        windows.append(
            _TimeWindow(
                str(raw.get("window_name", raw.get("name", "silver_bullet_window"))),
                start,
                end,
                str(raw.get("timezone", "UTC")),
                {str(day) for day in allowed},
                bool(raw.get("strict", True)),
            )
        )
    return windows


def _normalize_liquidity_pools(liquidity_pools: Sequence[Mapping[str, Any] | Any]) -> list[_LiquidityPool]:
    pools: list[_LiquidityPool] = []
    for index, pool in enumerate(liquidity_pools or []):
        direction = str(_get(pool, "direction", _get(pool, "side", ""))).lower()
        side = SilverBulletSweepSide.NONE
        if direction in {"buy_side", "buyside", "buy", "high", "equal_highs", "bsl"}:
            side = SilverBulletSweepSide.BUY_SIDE
        elif direction in {"sell_side", "sellside", "sell", "low", "equal_lows", "ssl"}:
            side = SilverBulletSweepSide.SELL_SIDE
        low = _float(_get(pool, "zone_low", _get(pool, "low", _get(pool, "price", 0.0))))
        high = _float(_get(pool, "zone_high", _get(pool, "high", _get(pool, "price", low))))
        if high < low:
            low, high = high, low
        mid = _float(_get(pool, "zone_mid", _get(pool, "mid", (low + high) / 2.0)))
        if side == SilverBulletSweepSide.NONE:
            continue
        pools.append(
            _LiquidityPool(
                str(_get(pool, "liquidity_id", _get(pool, "id", f"LIQ_{index}"))),
                str(_get(pool, "liquidity_type", _get(pool, "type", "unknown"))),
                side,
                low,
                mid,
                high,
                str(_get(pool, "swept_status", _get(pool, "status", "unswept"))).lower(),
                _float(_get(pool, "quality_score", 5.0)),
                _float(_get(pool, "target_priority_score", _get(pool, "priority_score", 5.0))),
            )
        )
    return pools


def _normalize_candles(
    df: Sequence[CandleNode | Mapping[str, Any]] | Any,
    timeframe: str | None,
    symbol: str,
) -> list[_Candle]:
    rows = df.to_dict("records") if hasattr(df, "to_dict") else list(df or [])
    candles: list[_Candle] = []
    for fallback_index, row in enumerate(rows):
        if isinstance(row, CandleNode):
            candles.append(
                _Candle(
                    fallback_index,
                    row.end_time,
                    float(row.open_p),
                    float(row.high_p),
                    float(row.low_p),
                    float(row.close_p),
                    float(row.volume),
                    timeframe or row.timeframe,
                    row.symbol,
                    bool(row.is_closed),
                )
            )
            continue
        timestamp = _get(row, "timestamp", _get(row, "end_time", _get(row, "start_time", None)))
        if timestamp is None:
            continue
        candles.append(
            _Candle(
                int(_get(row, "index", fallback_index)),
                _parse_datetime(timestamp),
                _float(_get(row, "open", _get(row, "open_p", 0.0))),
                _float(_get(row, "high", _get(row, "high_p", 0.0))),
                _float(_get(row, "low", _get(row, "low_p", 0.0))),
                _float(_get(row, "close", _get(row, "close_p", 0.0))),
                _float(_get(row, "volume", 0.0)),
                str(_get(row, "timeframe", timeframe or "unknown")),
                str(_get(row, "symbol", symbol)),
                bool(_get(row, "is_closed", True)),
            )
        )
    return sorted(candles, key=lambda candle: (candle.timestamp, candle.index))


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
        true_ranges.append(max(true_range, 0.00001))
        previous_close = candle.close
    values: list[float] = []
    for index in range(len(true_ranges)):
        start = max(0, index - period + 1)
        values.append(mean(true_ranges[start : index + 1]))
    return values


def _has_fvg_anywhere(candles: Sequence[_Candle]) -> bool:
    for index in range(len(candles) - 2):
        c1, c3 = candles[index], candles[index + 2]
        if c1.high < c3.low or c1.low > c3.high:
            return True
    return False


def _empty_result(reason: str, symbol: str, timeframe: str | None, htf_bias: str) -> dict[str, Any]:
    return {
        "concept_name": "ICT Silver Bullet",
        "setup_id": None,
        "valid_setup": False,
        "classification": SilverBulletClassification.NO_SETUP.value,
        "quality_grade": SilverBulletQualityGrade.INVALID.value,
        "direction": None,
        "sweep_level": None,
        "fvg_zone": None,
        "entry": None,
        "stop": None,
        "target": None,
        "trade_plan": {
            "entry": None,
            "entry_type": None,
            "stop": None,
            "stop_reference": None,
            "target": None,
            "target_reference": None,
            "target_liquidity_id": None,
            "risk_reward": 0.0,
        },
        "score": 0.0,
        "rr": 0.0,
        "htf_bias": htf_bias,
        "htf_alignment": None,
        "symbol": symbol,
        "timeframe": timeframe,
        "failed_requirements": [reason],
        "reasons": [],
        "warnings": ["Do not force Silver Bullet trades just because the time window is active"],
        "entry_allowed_from_silver_bullet_alone": False,
    }


def _fvg_payload(fvg: _FVGZone | None, retest: _Retest | None) -> dict[str, Any] | None:
    if fvg is None:
        return None
    return {
        "fvg_type": fvg.fvg_type.value,
        "zone_low": fvg.zone_low,
        "zone_high": fvg.zone_high,
        "zone_mid": fvg.zone_mid,
        "creation_index": fvg.creation_index,
        "creation_timestamp": fvg.creation_timestamp.isoformat(),
        "retest_status": retest.status.value if retest is not None else SilverBulletRetestStatus.NONE.value,
        "retest_index": retest.candle_index if retest is not None else None,
        "retest_timestamp": retest.timestamp.isoformat() if retest is not None and retest.timestamp else None,
    }


def _setup_id(sweep: _SweepCandidate, fvg: _FVGZone | None) -> str | None:
    if fvg is None:
        return None
    return f"SB_{sweep.direction.value.upper()}_{sweep.candle.index}_{fvg.creation_index}_{sweep.pool.liquidity_id}"


def _stop_reference(direction: SilverBulletDirection) -> str:
    if direction == SilverBulletDirection.BULLISH:
        return "below_sweep_low_or_fvg_low_with_ATR_buffer"
    return "above_sweep_high_or_fvg_high_with_ATR_buffer"


def _htf_alignment(direction: SilverBulletDirection, htf_bias: str) -> bool | None:
    bias = htf_bias.lower()
    if bias in {"neutral", "ranging", "range", "unknown", "none", ""}:
        return None
    if direction == SilverBulletDirection.BULLISH:
        return "bull" in bias
    return "bear" in bias


def _displacement_strength(range_ratio: float, body_ratio: float) -> str:
    if range_ratio >= 1.6 and body_ratio >= 0.70:
        return "strong"
    if range_ratio >= 1.2 and body_ratio >= 0.60:
        return "moderate"
    return "minimum"


def _position_for_index(candles: Sequence[_Candle], index: int) -> int:
    for position, candle in enumerate(candles):
        if candle.index == index:
            return position
    return max(0, min(index, len(candles) - 1))


def _parse_clock(value: Any) -> time | None:
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        parts = value.strip().split(":")
        if len(parts) >= 2:
            return time(int(parts[0]), int(parts[1]))
    return None


def _inside_time(value: time, window: _TimeWindow) -> bool:
    if window.start_time <= window.end_time:
        return window.start_time <= value <= window.end_time
    return value >= window.start_time or value <= window.end_time


def _day_allowed(timestamp: datetime, allowed_days: set[str]) -> bool:
    return not allowed_days or timestamp.strftime("%A") in allowed_days


def _timezone_for_name(name: str, warnings: list[str]) -> tzinfo:
    if name.upper() == "UTC":
        return dt_timezone.utc
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        warnings.append(f"strategy_timezone_unknown_fell_back_to_UTC:{name}")
        return dt_timezone.utc


def _input_timezone_name(candles: Sequence[_Candle]) -> str:
    for candle in candles:
        if candle.timestamp.tzinfo is not None:
            return str(candle.timestamp.tzinfo)
    return "unknown_assumed_UTC"


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"Unsupported timestamp value: {value!r}")


def _get(source: Mapping[str, Any] | Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
