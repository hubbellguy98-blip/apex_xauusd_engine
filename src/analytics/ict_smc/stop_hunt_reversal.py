"""ICT/SMC Turtle Soup / stop-hunt reversal detector.

The detector focuses on meaningful prior highs/lows, not random candle noise.
It classifies accepted breakouts separately from stop-hunt reversals and never
allows a stop hunt to become an entry signal by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence


class StopHuntType(str, Enum):
    NONE = "no_stop_hunt_reversal"
    BULLISH_REVERSAL = "bullish_stop_hunt_reversal"
    BEARISH_REVERSAL = "bearish_stop_hunt_reversal"
    BULLISH_CANDIDATE = "bullish_stop_hunt_candidate"
    BEARISH_CANDIDATE = "bearish_stop_hunt_candidate"
    BULLISH_BREAKOUT_CONTINUATION = "bullish_breakout_continuation"
    BEARISH_BREAKDOWN_CONTINUATION = "bearish_breakdown_continuation"
    UNCLEAR_PRIOR_HIGH_SWEEP = "unclear_prior_high_sweep"
    UNCLEAR_PRIOR_LOW_SWEEP = "unclear_prior_low_sweep"
    WEAK_PRIOR_LEVEL_NOISE = "weak_prior_level_noise"


class StopHuntSide(str, Enum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"
    NONE = "none"


class StopHuntReclaimStatus(str, Enum):
    NONE = "none"
    RECLAIMED_PRIOR_LOW = "reclaimed_back_above_prior_low"
    REJECTED_PRIOR_HIGH = "rejected_back_below_prior_high"
    ACCEPTED_ABOVE_PRIOR_HIGH = "accepted_above_prior_high"
    ACCEPTED_BELOW_PRIOR_LOW = "accepted_below_prior_low"
    UNCLEAR = "unclear"


class StopHuntConfidenceGrade(str, Enum):
    INVALID = "invalid"
    CANDIDATE = "candidate"
    VALID_CONTEXT = "valid_context"
    STRONG_CONTEXT = "strong_context"


@dataclass(frozen=True, slots=True)
class _Candle:
    index: int
    timestamp: Any
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
class _PriorLevel:
    level_id: str
    level_type: str
    direction: StopHuntSide
    price: float
    zone_low: float
    zone_mid: float
    zone_high: float
    index: int
    timestamp: Any
    timeframe: str
    strength_score: float
    swept_status: str
    quality_score: float


def detect_stop_hunt_reversal(
    df: Sequence[Mapping[str, Any] | Any] | Any,
    prior_highs_lows: Sequence[Mapping[str, Any] | Any],
    *,
    minimum_level_quality: float = 5.0,
    sweep_buffer: float | None = None,
    close_buffer: float | None = None,
    break_buffer: float | None = None,
    stop_buffer: float | None = None,
    atr_period: int = 14,
    min_displacement_body_ratio: float = 0.55,
    min_displacement_range_ratio: float = 1.0,
    minimum_rr: float = 1.5,
) -> dict[str, Any]:
    """Detect the best Turtle Soup / stop-hunt reversal event."""
    warnings = [
        "Do not enter from stop hunt alone",
        "Risk-to-reward must be validated before execution",
    ]
    candles = [c for c in _normalize_candles(df) if c.is_closed]
    if not candles:
        return _empty_result("no_closed_candles", warnings)

    levels, weak_levels = _normalize_prior_levels(prior_highs_lows, minimum_level_quality)
    if weak_levels and not levels:
        return _empty_result(
            StopHuntType.WEAK_PRIOR_LEVEL_NOISE.value,
            warnings + ["weak_prior_level_noise"],
            confidence_score=1.5,
        )
    if not levels:
        return _empty_result("no_meaningful_prior_highs_lows", warnings)

    avg_range = _average_range(candles, atr_period)
    sweep = sweep_buffer if sweep_buffer is not None else max(avg_range * 0.05, 0.0001)
    close = close_buffer if close_buffer is not None else max(avg_range * 0.04, 0.0001)
    brk = break_buffer if break_buffer is not None else max(avg_range * 0.04, 0.0001)
    stop = stop_buffer if stop_buffer is not None else max(avg_range * 0.08, 0.0001)

    events: list[dict[str, Any]] = []
    for level in levels:
        for pos, candle in enumerate(candles):
            if candle.index <= level.index:
                continue
            event = _classify_stop_hunt(
                candles,
                pos,
                candle,
                level,
                sweep,
                close,
                brk,
                stop,
                avg_range,
                min_displacement_body_ratio,
                min_displacement_range_ratio,
                minimum_rr,
            )
            if event:
                events.append(event)

    if not events:
        return _empty_result("no_stop_hunt_reversal_detected", warnings)

    events.sort(
        key=lambda event: (
            event["stop_hunt_detected"],
            event["confidence_score"],
            event["swept_level"]["quality_score"],
            event["sweep"]["sweep_index"],
        ),
        reverse=True,
    )
    best = events[0]
    best["stop_hunt_events"] = events
    best["warnings"] = _dedupe(best["warnings"] + warnings)
    return best


def _classify_stop_hunt(
    candles: Sequence[_Candle],
    pos: int,
    candle: _Candle,
    level: _PriorLevel,
    sweep_buffer: float,
    close_buffer: float,
    break_buffer: float,
    stop_buffer: float,
    avg_range: float,
    min_body_ratio: float,
    min_range_ratio: float,
    minimum_rr: float,
) -> dict[str, Any] | None:
    if level.direction is StopHuntSide.BUY_SIDE:
        if candle.high <= level.zone_high + sweep_buffer:
            return None
        if candle.close > level.zone_high + close_buffer:
            return _accepted_breakout(candle, level, StopHuntType.BULLISH_BREAKOUT_CONTINUATION)
        if candle.close < level.zone_high:
            return _build_reversal_event(
                candles,
                pos,
                candle,
                level,
                StopHuntType.BEARISH_REVERSAL,
                StopHuntSide.BUY_SIDE,
                StopHuntReclaimStatus.REJECTED_PRIOR_HIGH,
                "bearish",
                candle.high,
                stop_buffer,
                break_buffer,
                avg_range,
                min_body_ratio,
                min_range_ratio,
                minimum_rr,
            )
        return _unclear_sweep(candle, level, StopHuntType.UNCLEAR_PRIOR_HIGH_SWEEP)

    if level.direction is StopHuntSide.SELL_SIDE:
        if candle.low >= level.zone_low - sweep_buffer:
            return None
        if candle.close < level.zone_low - close_buffer:
            return _accepted_breakout(candle, level, StopHuntType.BEARISH_BREAKDOWN_CONTINUATION)
        if candle.close > level.zone_low:
            return _build_reversal_event(
                candles,
                pos,
                candle,
                level,
                StopHuntType.BULLISH_REVERSAL,
                StopHuntSide.SELL_SIDE,
                StopHuntReclaimStatus.RECLAIMED_PRIOR_LOW,
                "bullish",
                candle.low,
                stop_buffer,
                break_buffer,
                avg_range,
                min_body_ratio,
                min_range_ratio,
                minimum_rr,
            )
        return _unclear_sweep(candle, level, StopHuntType.UNCLEAR_PRIOR_LOW_SWEEP)
    return None


def _build_reversal_event(
    candles: Sequence[_Candle],
    sweep_pos: int,
    sweep_candle: _Candle,
    level: _PriorLevel,
    stop_hunt_type: StopHuntType,
    swept_side: StopHuntSide,
    reclaim_status: StopHuntReclaimStatus,
    direction: str,
    sweep_extreme: float,
    stop_buffer: float,
    break_buffer: float,
    avg_range: float,
    min_body_ratio: float,
    min_range_ratio: float,
    minimum_rr: float,
) -> dict[str, Any]:
    mss = _confirm_mss(candles, sweep_pos, direction, break_buffer)
    displacement = _confirm_displacement(
        candles,
        mss["mss_position"],
        direction,
        avg_range,
        min_body_ratio,
        min_range_ratio,
    )
    entry_zone = _entry_zone(candles, mss["mss_position"], direction, sweep_extreme)
    target = _target_liquidity(candles, sweep_pos, direction, level, sweep_extreme)
    invalidation = (
        round(sweep_extreme - stop_buffer, 5)
        if direction == "bullish"
        else round(sweep_extreme + stop_buffer, 5)
    )
    risk_plan = _risk_plan(entry_zone, target, invalidation, direction, minimum_rr)
    candidate_only = not mss["mss_confirmed"]
    effective_type = _candidate_type(stop_hunt_type) if candidate_only else stop_hunt_type
    confidence, grade, reasons, failed = _score_event(
        level,
        reclaim_status,
        mss,
        displacement,
        entry_zone,
        target,
        risk_plan,
        candidate_only,
    )
    return {
        "concept_name": "Turtle Soup / Stop-Hunt Reversal",
        "symbol": sweep_candle.symbol,
        "timeframe": sweep_candle.timeframe,
        "setup_id": f"STOP_HUNT_{direction.upper()}_{level.level_id}_{sweep_candle.index}",
        "stop_hunt_detected": not candidate_only,
        "valid_setup": not candidate_only,
        "stop_hunt_type": effective_type.value,
        "classification": effective_type.value,
        "swept_level": _level_to_dict(level),
        "swept_side": swept_side.value,
        "sweep": {
            "swept_side": swept_side.value,
            "sweep_index": sweep_candle.index,
            "sweep_timestamp": _serialize_timestamp(sweep_candle.timestamp),
            "sweep_extreme": sweep_extreme,
            "sweep_condition": _sweep_condition(direction),
        },
        "reclaim_status": reclaim_status.value,
        "mss_confirmed": mss["mss_confirmed"],
        "confirmation": {
            **mss,
            "displacement_confirmed": displacement["displacement_confirmed"],
            "displacement_direction": displacement["displacement_direction"],
            "displacement_strength": displacement["displacement_strength"],
        },
        "entry_zone": entry_zone,
        "fvg_entry": entry_zone if entry_zone and "fvg" in entry_zone["entry_zone_type"] else None,
        "ob_entry": (
            entry_zone
            if entry_zone and entry_zone["entry_zone_type"].endswith("_ob")
            else None
        ),
        "target_liquidity": target,
        "invalidation_level": invalidation,
        "risk_plan": risk_plan,
        "false_positive_flags": failed,
        "confidence_score": confidence,
        "confidence_grade": grade.value,
        "entry_allowed_from_stop_hunt_alone": False,
        "invalidation_rules": _invalidation_rules(direction),
        "warnings": _warnings(candidate_only, risk_plan, failed),
        "reasons": reasons,
    }


def _confirm_mss(
    candles: Sequence[_Candle],
    sweep_pos: int,
    direction: str,
    break_buffer: float,
) -> dict[str, Any]:
    if sweep_pos + 2 >= len(candles):
        return _mss_result(False, direction)
    after = list(candles[sweep_pos + 1 :])
    if direction == "bullish":
        for rel_pos, candle in enumerate(after[1:], start=1):
            broken = max(c.high for c in after[:rel_pos])
            if candle.close > broken + break_buffer:
                return _mss_result(True, direction, candle, sweep_pos + 1 + rel_pos, broken)
    else:
        for rel_pos, candle in enumerate(after[1:], start=1):
            broken = min(c.low for c in after[:rel_pos])
            if candle.close < broken - break_buffer:
                return _mss_result(True, direction, candle, sweep_pos + 1 + rel_pos, broken)
    return _mss_result(False, direction)


def _mss_result(
    confirmed: bool,
    direction: str,
    candle: _Candle | None = None,
    position: int | None = None,
    broken_level: float | None = None,
) -> dict[str, Any]:
    return {
        "mss_confirmed": confirmed,
        "mss_direction": direction if confirmed else None,
        "mss_confirmation_index": candle.index if candle else None,
        "mss_position": position,
        "broken_level": broken_level,
        "confirmation_type": (
            f"candle_close_{'above' if direction == 'bullish' else 'below'}_post_sweep_swing"
            if confirmed
            else None
        ),
    }


def _confirm_displacement(
    candles: Sequence[_Candle],
    mss_pos: int | None,
    direction: str,
    avg_range: float,
    min_body_ratio: float,
    min_range_ratio: float,
) -> dict[str, Any]:
    if mss_pos is None or mss_pos >= len(candles):
        return {
            "displacement_confirmed": False,
            "displacement_direction": None,
            "displacement_strength": "none",
        }
    candle = candles[mss_pos]
    body_ratio = candle.body / candle.range if candle.range > 0 else 0.0
    range_ratio = candle.range / avg_range if avg_range > 0 else 0.0
    if direction == "bullish":
        direction_ok = candle.bullish and candle.bullish_close_position >= 0.65
    else:
        direction_ok = candle.bearish and candle.bearish_close_position >= 0.65
    confirmed = direction_ok and body_ratio >= min_body_ratio and range_ratio >= min_range_ratio
    return {
        "displacement_confirmed": confirmed,
        "displacement_direction": direction if confirmed else None,
        "displacement_strength": "strong" if confirmed and range_ratio >= 1.25 else "moderate",
        "displacement_index": candle.index if confirmed else None,
        "body_to_range_ratio": round(body_ratio, 4),
        "range_to_atr_ratio": round(range_ratio, 4),
    }


def _entry_zone(
    candles: Sequence[_Candle],
    mss_pos: int | None,
    direction: str,
    sweep_extreme: float,
) -> dict[str, Any] | None:
    if mss_pos is None or mss_pos < 2 or mss_pos >= len(candles):
        return None
    first = candles[mss_pos - 2]
    third = candles[mss_pos]
    if direction == "bullish" and first.high < third.low:
        return _zone_dict("bullish_fvg", first.high, third.low, third.index, sweep_extreme)
    if direction == "bearish" and first.low > third.high:
        return _zone_dict("bearish_fvg", third.high, first.low, third.index, sweep_extreme)
    ob = _order_block_zone(candles, mss_pos, direction, sweep_extreme)
    return ob


def _order_block_zone(
    candles: Sequence[_Candle],
    mss_pos: int,
    direction: str,
    sweep_extreme: float,
) -> dict[str, Any] | None:
    prior = reversed(candles[max(0, mss_pos - 5) : mss_pos])
    for candle in prior:
        if direction == "bullish" and candle.bearish:
            return _zone_dict("bullish_ob", candle.low, candle.high, candle.index, sweep_extreme)
        if direction == "bearish" and candle.bullish:
            return _zone_dict("bearish_ob", candle.low, candle.high, candle.index, sweep_extreme)
    return None


def _zone_dict(
    zone_type: str,
    low: float,
    high: float,
    creation_index: int,
    invalidation_level: float,
) -> dict[str, Any]:
    zone_low = min(low, high)
    zone_high = max(low, high)
    return {
        "entry_zone_type": zone_type,
        "zone_low": round(zone_low, 5),
        "zone_high": round(zone_high, 5),
        "zone_mid": round((zone_low + zone_high) / 2, 5),
        "creation_index": creation_index,
        "retest_status": "pending_or_confirmed",
        "invalidation_level": invalidation_level,
    }


def _target_liquidity(
    candles: Sequence[_Candle],
    sweep_pos: int,
    direction: str,
    level: _PriorLevel,
    sweep_extreme: float,
) -> dict[str, Any] | None:
    before = candles[:sweep_pos]
    if not before:
        return None
    if direction == "bullish":
        highs = [c.high for c in before if c.high > level.zone_mid]
        target_price = max(highs) if highs else max(c.high for c in before)
        return {
            "target_side": "buy_side",
            "target_type": "prior_swing_high_or_equal_highs",
            "target_price": round(target_price, 5),
            "target_priority_score": round(min(10.0, level.quality_score + 0.5), 2),
        }
    lows = [c.low for c in before if c.low < level.zone_mid]
    target_price = min(lows) if lows else min(c.low for c in before)
    return {
        "target_side": "sell_side",
        "target_type": "prior_swing_low_or_equal_lows",
        "target_price": round(target_price, 5),
        "target_priority_score": round(min(10.0, level.quality_score + 0.5), 2),
    }


def _risk_plan(
    entry_zone: Mapping[str, Any] | None,
    target: Mapping[str, Any] | None,
    invalidation: float,
    direction: str,
    minimum_rr: float,
) -> dict[str, Any]:
    entry = entry_zone.get("zone_mid") if entry_zone else None
    target_price = target.get("target_price") if target else None
    if entry is None or target_price is None:
        return {
            "entry": entry,
            "stop": invalidation,
            "target": target_price,
            "risk_reward": None,
            "rr_valid": False,
            "note": "Entry zone or target liquidity is missing",
        }
    if direction == "bullish":
        risk = entry - invalidation
        reward = target_price - entry
        stop_reference = "below_sweep_low_with_ATR_buffer"
    else:
        risk = invalidation - entry
        reward = entry - target_price
        stop_reference = "above_sweep_high_with_ATR_buffer"
    rr = round(reward / risk, 4) if risk > 0 and reward > 0 else None
    return {
        "entry": entry,
        "stop": invalidation,
        "stop_reference": stop_reference,
        "target": target_price,
        "risk_points": round(risk, 5),
        "reward_points": round(reward, 5),
        "risk_reward": rr,
        "rr_valid": rr is not None and rr >= minimum_rr,
        "minimum_rr_required": minimum_rr,
    }


def _score_event(
    level: _PriorLevel,
    reclaim_status: StopHuntReclaimStatus,
    mss: Mapping[str, Any],
    displacement: Mapping[str, Any],
    entry_zone: Mapping[str, Any] | None,
    target: Mapping[str, Any] | None,
    risk_plan: Mapping[str, Any],
    candidate_only: bool,
) -> tuple[float, StopHuntConfidenceGrade, list[str], list[str]]:
    score = 2.0
    reasons = [f"Meaningful prior level swept: {level.level_type}"]
    failed: list[str] = []
    score += min(level.quality_score, 10.0) * 0.2
    if reclaim_status in {
        StopHuntReclaimStatus.RECLAIMED_PRIOR_LOW,
        StopHuntReclaimStatus.REJECTED_PRIOR_HIGH,
    }:
        score += 1.3
        reasons.append("Price reclaimed/rejected the swept prior level")
    if mss["mss_confirmed"]:
        score += 1.6
        reasons.append("MSS confirmed with candle close")
    else:
        failed.append("mss_not_confirmed_after_sweep")
    if displacement["displacement_confirmed"]:
        score += 1.0
        reasons.append("Displacement confirmed after MSS")
    else:
        failed.append("displacement_not_confirmed")
    if entry_zone:
        score += 1.0
        reasons.append(f"{entry_zone['entry_zone_type']} created entry zone")
    else:
        failed.append("no_fvg_or_ob_entry_zone")
    if target:
        score += 0.8
        reasons.append("Opposite-side target liquidity exists")
    else:
        failed.append("no_opposite_liquidity_target")
    if risk_plan.get("rr_valid") is True:
        score += 0.7
        reasons.append("Risk-to-reward meets minimum requirement")
    elif risk_plan.get("risk_reward") is not None:
        failed.append("risk_reward_below_minimum")
    if candidate_only:
        score = min(score, 5.0)
        grade = StopHuntConfidenceGrade.CANDIDATE
    elif score >= 8.0:
        grade = StopHuntConfidenceGrade.STRONG_CONTEXT
    elif score >= 7.0:
        grade = StopHuntConfidenceGrade.VALID_CONTEXT
    else:
        grade = StopHuntConfidenceGrade.CANDIDATE
    return round(max(0.0, min(10.0, score)), 2), grade, reasons, failed


def _accepted_breakout(
    candle: _Candle,
    level: _PriorLevel,
    stop_hunt_type: StopHuntType,
) -> dict[str, Any]:
    bullish = stop_hunt_type is StopHuntType.BULLISH_BREAKOUT_CONTINUATION
    reclaim = (
        StopHuntReclaimStatus.ACCEPTED_ABOVE_PRIOR_HIGH
        if bullish
        else StopHuntReclaimStatus.ACCEPTED_BELOW_PRIOR_LOW
    )
    return {
        "concept_name": "Turtle Soup / Stop-Hunt Reversal",
        "symbol": candle.symbol,
        "timeframe": candle.timeframe,
        "setup_id": f"STOP_HUNT_FALSE_{level.level_id}_{candle.index}",
        "stop_hunt_detected": False,
        "valid_setup": False,
        "stop_hunt_type": stop_hunt_type.value,
        "classification": stop_hunt_type.value,
        "swept_level": _level_to_dict(level),
        "swept_side": level.direction.value,
        "sweep": {
            "swept_side": level.direction.value,
            "sweep_index": candle.index,
            "sweep_timestamp": _serialize_timestamp(candle.timestamp),
            "sweep_extreme": candle.high if bullish else candle.low,
        },
        "reclaim_status": reclaim.value,
        "mss_confirmed": False,
        "entry_zone": None,
        "target_liquidity": None,
        "invalidation_level": None,
        "risk_plan": None,
        "confidence_score": 2.6,
        "confidence_grade": StopHuntConfidenceGrade.INVALID.value,
        "failed_requirements": [
            "Price accepted beyond prior level instead of reclaiming/rejecting",
            "Reversal MSS did not confirm",
        ],
        "warnings": [
            "Do not classify accepted breakout as stop-hunt reversal",
            "This may be continuation if BOS/FVG confirms",
        ],
        "reasons": [],
        "entry_allowed_from_stop_hunt_alone": False,
    }


def _unclear_sweep(
    candle: _Candle,
    level: _PriorLevel,
    stop_hunt_type: StopHuntType,
) -> dict[str, Any]:
    return {
        "concept_name": "Turtle Soup / Stop-Hunt Reversal",
        "symbol": candle.symbol,
        "timeframe": candle.timeframe,
        "setup_id": f"STOP_HUNT_UNCLEAR_{level.level_id}_{candle.index}",
        "stop_hunt_detected": False,
        "valid_setup": False,
        "stop_hunt_type": stop_hunt_type.value,
        "classification": stop_hunt_type.value,
        "swept_level": _level_to_dict(level),
        "swept_side": level.direction.value,
        "reclaim_status": StopHuntReclaimStatus.UNCLEAR.value,
        "mss_confirmed": False,
        "entry_zone": None,
        "target_liquidity": None,
        "invalidation_level": None,
        "risk_plan": None,
        "confidence_score": 2.0,
        "confidence_grade": StopHuntConfidenceGrade.INVALID.value,
        "warnings": ["Sweep did not clearly reclaim/reject or accept beyond prior level"],
        "reasons": [],
        "entry_allowed_from_stop_hunt_alone": False,
    }


def _empty_result(
    status: str,
    warnings: list[str],
    *,
    confidence_score: float = 0.0,
) -> dict[str, Any]:
    return {
        "concept_name": "Turtle Soup / Stop-Hunt Reversal",
        "symbol": "unknown",
        "timeframe": "unknown",
        "setup_id": None,
        "stop_hunt_detected": False,
        "valid_setup": False,
        "stop_hunt_type": StopHuntType.NONE.value,
        "classification": status,
        "swept_level": None,
        "swept_side": StopHuntSide.NONE.value,
        "reclaim_status": StopHuntReclaimStatus.NONE.value,
        "mss_confirmed": False,
        "entry_zone": None,
        "target_liquidity": None,
        "invalidation_level": None,
        "risk_plan": None,
        "confidence_score": confidence_score,
        "confidence_grade": StopHuntConfidenceGrade.INVALID.value,
        "warnings": _dedupe(warnings),
        "reasons": [],
        "stop_hunt_events": [],
        "entry_allowed_from_stop_hunt_alone": False,
    }


def _candidate_type(stop_hunt_type: StopHuntType) -> StopHuntType:
    if stop_hunt_type is StopHuntType.BULLISH_REVERSAL:
        return StopHuntType.BULLISH_CANDIDATE
    return StopHuntType.BEARISH_CANDIDATE


def _sweep_condition(direction: str) -> str:
    if direction == "bullish":
        return "candle_low_below_prior_low_and_close_back_above_prior_low"
    return "candle_high_above_prior_high_and_close_back_below_prior_high"


def _warnings(
    candidate_only: bool,
    risk_plan: Mapping[str, Any],
    failed: Sequence[str],
) -> list[str]:
    warnings = ["Do not enter from stop hunt alone"]
    if candidate_only:
        warnings.append("Sweep and reclaim/rejection are not enough without MSS")
    if risk_plan and risk_plan.get("rr_valid") is False:
        warnings.append("Risk-to-reward must be validated before execution")
    warnings.extend(failed)
    return _dedupe(warnings)


def _invalidation_rules(direction: str) -> list[str]:
    if direction == "bullish":
        return [
            "Close below sweep low after entry",
            "Bullish FVG fully violated",
            "No reaction from entry zone",
            "Target liquidity already swept",
        ]
    return [
        "Close above sweep high after entry",
        "Bearish FVG fully violated",
        "No rejection from entry zone",
        "Target liquidity already swept",
    ]


def _normalize_candles(df: Sequence[Mapping[str, Any] | Any] | Any) -> list[_Candle]:
    if hasattr(df, "to_dict"):
        records = df.to_dict("records")
    else:
        records = list(df or [])
    candles: list[_Candle] = []
    for pos, item in enumerate(records):
        raw = _mapping(item)
        candles.append(
            _Candle(
                index=_int(raw.get("index"), pos),
                timestamp=raw.get("timestamp"),
                open=_float(raw.get("open"), 0.0),
                high=_float(raw.get("high"), 0.0),
                low=_float(raw.get("low"), 0.0),
                close=_float(raw.get("close"), 0.0),
                volume=_float(raw.get("volume"), 0.0),
                timeframe=str(raw.get("timeframe") or "unknown"),
                symbol=str(raw.get("symbol") or "unknown"),
                is_closed=_truthy(raw.get("is_closed"), True),
            )
        )
    return candles


def _normalize_prior_levels(
    items: Sequence[Mapping[str, Any] | Any],
    minimum_quality: float,
) -> tuple[list[_PriorLevel], list[_PriorLevel]]:
    valid: list[_PriorLevel] = []
    weak: list[_PriorLevel] = []
    for pos, item in enumerate(items or []):
        raw = _mapping(item)
        direction = _level_direction(raw)
        if direction is StopHuntSide.NONE:
            continue
        price = _float(raw.get("price") or raw.get("zone_mid"), 0.0)
        zone_low = _float(raw.get("zone_low"), price)
        zone_high = _float(raw.get("zone_high"), price)
        level = _PriorLevel(
            level_id=str(raw.get("level_id") or raw.get("liquidity_id") or f"LEVEL_{pos}"),
            level_type=str(raw.get("level_type") or raw.get("liquidity_type") or "prior_level"),
            direction=direction,
            price=price,
            zone_low=min(zone_low, zone_high),
            zone_mid=_float(raw.get("zone_mid"), (zone_low + zone_high) / 2),
            zone_high=max(zone_low, zone_high),
            index=_int(raw.get("index"), -1),
            timestamp=raw.get("timestamp"),
            timeframe=str(raw.get("timeframe") or "unknown"),
            strength_score=_float(raw.get("strength_score"), _float(raw.get("quality_score"), 0.0)),
            swept_status=str(raw.get("swept_status") or "unswept"),
            quality_score=_float(raw.get("quality_score"), 0.0),
        )
        if level.quality_score >= minimum_quality:
            valid.append(level)
        else:
            weak.append(level)
    return valid, weak


def _level_direction(raw: Mapping[str, Any]) -> StopHuntSide:
    direction = str(raw.get("direction") or raw.get("side") or "").lower()
    level_type = str(raw.get("level_type") or raw.get("liquidity_type") or "").lower()
    if direction in {"buy_side", "buyside", "buy", "high"}:
        return StopHuntSide.BUY_SIDE
    if direction in {"sell_side", "sellside", "sell", "low"}:
        return StopHuntSide.SELL_SIDE
    if "high" in level_type or "pdh" in level_type:
        return StopHuntSide.BUY_SIDE
    if "low" in level_type or "pdl" in level_type:
        return StopHuntSide.SELL_SIDE
    return StopHuntSide.NONE


def _level_to_dict(level: _PriorLevel) -> dict[str, Any]:
    return {
        "level_id": level.level_id,
        "level_type": level.level_type,
        "direction": level.direction.value,
        "price": level.price,
        "zone_low": level.zone_low,
        "zone_mid": level.zone_mid,
        "zone_high": level.zone_high,
        "index": level.index,
        "timestamp": _serialize_timestamp(level.timestamp),
        "timeframe": level.timeframe,
        "strength_score": level.strength_score,
        "swept_status": level.swept_status,
        "quality_score": level.quality_score,
    }


def _average_range(candles: Sequence[_Candle], period: int) -> float:
    ranges = [c.range for c in candles[-period:] if c.range > 0]
    if not ranges:
        return 1.0
    return sum(ranges) / len(ranges)


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if value is None:
        return {}
    data: dict[str, Any] = {}
    for key in dir(value):
        if key.startswith("_"):
            continue
        try:
            attr = getattr(value, key)
        except Exception:
            continue
        if not callable(attr):
            data[key] = attr
    return data


def _serialize_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _float(value: Any, default: float) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(value: Any, default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _truthy(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "closed"}


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
