"""ICT/SMC SMT divergence detector.

SMT is modeled here as deterministic confluence between two related assets.
It compares confirmed swings on synchronized candles and never treats SMT as a
standalone entry signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import sqrt
from typing import Any, Mapping, Sequence


class SMTDivergenceType(str, Enum):
    NONE = "no_smt_divergence"
    BULLISH_POSITIVE = "bullish_smt_positive_correlation"
    BEARISH_POSITIVE = "bearish_smt_positive_correlation"
    BULLISH_INVERSE = "bullish_smt_inverse_correlation"
    BEARISH_INVERSE = "bearish_smt_inverse_correlation"
    INVALID_OR_WEAK = "invalid_or_weak_smt"


class SMTCorrelationType(str, Enum):
    POSITIVE = "positive"
    INVERSE = "inverse"
    UNKNOWN = "unknown"


class SMTDirectionBias(str, Enum):
    BULLISH_FOR_ASSET_A = "bullish_for_asset_a"
    BEARISH_FOR_ASSET_A = "bearish_for_asset_a"
    UNCLEAR = "unclear"


class SMTSynchronizationStatus(str, Enum):
    CLEAN = "clean"
    ACCEPTABLE = "acceptable"
    POOR = "poor"


class SMTSwingType(str, Enum):
    HIGH = "swing_high"
    LOW = "swing_low"


@dataclass(frozen=True, slots=True)
class _Candle:
    index: int
    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True
    timeframe: str = ""
    symbol: str = ""


@dataclass(frozen=True, slots=True)
class _Swing:
    swing_id: str
    timestamp: Any
    index: int
    swing_type: SMTSwingType
    price: float
    strength_score: float
    confirmed: bool
    timeframe: str = ""


def detect_smt_divergence(
    asset_a_df: Sequence[Mapping[str, Any]],
    asset_b_df: Sequence[Mapping[str, Any]],
    swing_points_a: Sequence[Mapping[str, Any]],
    swing_points_b: Sequence[Mapping[str, Any]],
    *,
    primary_asset_symbol: str = "asset_a",
    comparison_asset_symbol: str = "asset_b",
    correlation_type: str = "positive",
    time_tolerance_bars: int = 3,
    min_swing_strength: float = 5.0,
    divergence_threshold_percent: float = 0.0001,
    min_correlation_abs: float = 0.25,
    rolling_correlation_period: int = 50,
    news_spike_indices: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Detect SMT divergence between two related assets.

    The function returns the highest-confidence event as the top-level result
    and includes all candidate events in ``smt_events``.
    """

    candles_a = _to_candles(asset_a_df, primary_asset_symbol)
    candles_b = _to_candles(asset_b_df, comparison_asset_symbol)
    sync = _synchronization_snapshot(candles_a, candles_b, time_tolerance_bars)

    if not candles_a or not candles_b:
        return _invalid_result(
            primary_asset_symbol,
            comparison_asset_symbol,
            correlation_type,
            sync,
            ["missing_confirmed_closed_candles"],
        )

    corr = _rolling_correlation(candles_a, candles_b, rolling_correlation_period)
    resolved_correlation, correlation_warnings = _resolve_correlation(
        correlation_type,
        corr,
        min_correlation_abs,
    )

    if sync["synchronization_status"] == SMTSynchronizationStatus.POOR.value:
        return _invalid_result(
            primary_asset_symbol,
            comparison_asset_symbol,
            resolved_correlation.value,
            sync,
            ["synchronization_or_delayed_confirmation_issue"],
            rolling_correlation=corr,
        )

    if abs(corr) < min_correlation_abs:
        return _invalid_result(
            primary_asset_symbol,
            comparison_asset_symbol,
            resolved_correlation.value,
            sync,
            ["weak_or_unstable_correlation"],
            rolling_correlation=corr,
            warnings=correlation_warnings,
        )

    swings_a = _to_swings(swing_points_a, min_swing_strength)
    swings_b = _to_swings(swing_points_b, min_swing_strength)
    if len(swings_a) < 2 or len(swings_b) < 2:
        return _invalid_result(
            primary_asset_symbol,
            comparison_asset_symbol,
            resolved_correlation.value,
            sync,
            ["not_enough_confirmed_meaningful_swings"],
            rolling_correlation=corr,
            warnings=correlation_warnings,
        )

    timestamp_to_bar = {
        _timestamp_key(candle.timestamp): position
        for position, candle in enumerate(candles_a)
        if _timestamp_key(candle.timestamp) in sync["matched_timestamps"]
    }
    news_indices = set(news_spike_indices or [])
    events: list[dict[str, Any]] = []

    if resolved_correlation == SMTCorrelationType.POSITIVE:
        events.extend(
            _detect_positive_correlation(
                candles_a,
                swings_a,
                swings_b,
                timestamp_to_bar,
                time_tolerance_bars,
                divergence_threshold_percent,
                primary_asset_symbol,
                comparison_asset_symbol,
                sync,
                corr,
                news_indices,
            )
        )
    elif resolved_correlation == SMTCorrelationType.INVERSE:
        events.extend(
            _detect_inverse_correlation(
                candles_a,
                swings_a,
                swings_b,
                timestamp_to_bar,
                time_tolerance_bars,
                divergence_threshold_percent,
                primary_asset_symbol,
                comparison_asset_symbol,
                sync,
                corr,
                news_indices,
            )
        )

    events = _deduplicate_events(events)
    events.sort(
        key=lambda item: (
            item["confidence_score"],
            item["reference_swings"]["asset_a"]["current_swing"]["index"],
        ),
        reverse=True,
    )

    if not events:
        return _invalid_result(
            primary_asset_symbol,
            comparison_asset_symbol,
            resolved_correlation.value,
            sync,
            ["no_valid_smt_swing_relationship"],
            rolling_correlation=corr,
            warnings=correlation_warnings,
        )

    best = events[0]
    best["smt_events"] = events
    if correlation_warnings:
        best["warnings"] = list(dict.fromkeys(best["warnings"] + correlation_warnings))
    return best


def _detect_positive_correlation(
    candles_a: Sequence[_Candle],
    swings_a: Sequence[_Swing],
    swings_b: Sequence[_Swing],
    timestamp_to_bar: Mapping[Any, int],
    tolerance: int,
    threshold_percent: float,
    primary_symbol: str,
    comparison_symbol: str,
    sync: Mapping[str, Any],
    rolling_correlation: float,
    news_indices: set[int],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    events.extend(
        _scan_swing_type(
            candles_a,
            swings_a,
            swings_b,
            timestamp_to_bar,
            tolerance,
            threshold_percent,
            SMTSwingType.HIGH,
            SMTSwingType.HIGH,
            primary_symbol,
            comparison_symbol,
            SMTCorrelationType.POSITIVE,
            SMTDivergenceType.BEARISH_POSITIVE,
            SMTDirectionBias.BEARISH_FOR_ASSET_A,
            sync,
            rolling_correlation,
            news_indices,
        )
    )
    events.extend(
        _scan_swing_type(
            candles_a,
            swings_a,
            swings_b,
            timestamp_to_bar,
            tolerance,
            threshold_percent,
            SMTSwingType.LOW,
            SMTSwingType.LOW,
            primary_symbol,
            comparison_symbol,
            SMTCorrelationType.POSITIVE,
            SMTDivergenceType.BULLISH_POSITIVE,
            SMTDirectionBias.BULLISH_FOR_ASSET_A,
            sync,
            rolling_correlation,
            news_indices,
        )
    )
    return events


def _detect_inverse_correlation(
    candles_a: Sequence[_Candle],
    swings_a: Sequence[_Swing],
    swings_b: Sequence[_Swing],
    timestamp_to_bar: Mapping[Any, int],
    tolerance: int,
    threshold_percent: float,
    primary_symbol: str,
    comparison_symbol: str,
    sync: Mapping[str, Any],
    rolling_correlation: float,
    news_indices: set[int],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    events.extend(
        _scan_swing_type(
            candles_a,
            swings_a,
            swings_b,
            timestamp_to_bar,
            tolerance,
            threshold_percent,
            SMTSwingType.HIGH,
            SMTSwingType.LOW,
            primary_symbol,
            comparison_symbol,
            SMTCorrelationType.INVERSE,
            SMTDivergenceType.BEARISH_INVERSE,
            SMTDirectionBias.BEARISH_FOR_ASSET_A,
            sync,
            rolling_correlation,
            news_indices,
        )
    )
    events.extend(
        _scan_swing_type(
            candles_a,
            swings_a,
            swings_b,
            timestamp_to_bar,
            tolerance,
            threshold_percent,
            SMTSwingType.LOW,
            SMTSwingType.HIGH,
            primary_symbol,
            comparison_symbol,
            SMTCorrelationType.INVERSE,
            SMTDivergenceType.BULLISH_INVERSE,
            SMTDirectionBias.BULLISH_FOR_ASSET_A,
            sync,
            rolling_correlation,
            news_indices,
        )
    )
    return events


def _scan_swing_type(
    candles_a: Sequence[_Candle],
    swings_a: Sequence[_Swing],
    swings_b: Sequence[_Swing],
    timestamp_to_bar: Mapping[Any, int],
    tolerance: int,
    threshold_percent: float,
    a_type: SMTSwingType,
    b_type: SMTSwingType,
    primary_symbol: str,
    comparison_symbol: str,
    correlation_type: SMTCorrelationType,
    divergence_type: SMTDivergenceType,
    direction_bias: SMTDirectionBias,
    sync: Mapping[str, Any],
    rolling_correlation: float,
    news_indices: set[int],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    typed_a = [swing for swing in swings_a if swing.swing_type == a_type]
    typed_b = [swing for swing in swings_b if swing.swing_type == b_type]

    for previous_a, current_a in zip(typed_a, typed_a[1:]):
        current_b = _nearest_swing(current_a, typed_b, timestamp_to_bar, tolerance)
        previous_b = _nearest_swing(previous_a, typed_b, timestamp_to_bar, tolerance)
        if current_b is None or previous_b is None:
            continue
        if current_b.index <= previous_b.index:
            continue

        a_made_extreme = _made_expected_extreme(
            previous_a,
            current_a,
            a_type,
            threshold_percent,
        )
        b_failed_confirm = _failed_to_confirm(
            previous_b,
            current_b,
            b_type,
            threshold_percent,
        )
        if not a_made_extreme or not b_failed_confirm:
            continue

        event = _build_event(
            candles_a,
            previous_a,
            current_a,
            previous_b,
            current_b,
            primary_symbol,
            comparison_symbol,
            correlation_type,
            divergence_type,
            direction_bias,
            sync,
            rolling_correlation,
            timestamp_to_bar,
            news_indices,
        )
        events.append(event)
    return events


def _build_event(
    candles_a: Sequence[_Candle],
    previous_a: _Swing,
    current_a: _Swing,
    previous_b: _Swing,
    current_b: _Swing,
    primary_symbol: str,
    comparison_symbol: str,
    correlation_type: SMTCorrelationType,
    divergence_type: SMTDivergenceType,
    direction_bias: SMTDirectionBias,
    sync: Mapping[str, Any],
    rolling_correlation: float,
    timestamp_to_bar: Mapping[Any, int],
    news_indices: set[int],
) -> dict[str, Any]:
    bullish = direction_bias == SMTDirectionBias.BULLISH_FOR_ASSET_A
    sweep_side = "sell_side" if bullish else "buy_side"
    reclaim_status = _reclaim_status(candles_a, previous_a, current_a, bullish)
    mss = _mss_status(candles_a, previous_a, current_a, bullish)
    fvg_ob = _fvg_followthrough(candles_a, current_a, bullish)
    gap = _swing_gap_bars(current_a, current_b, timestamp_to_bar)
    false_flags = _false_positive_flags(
        sync,
        gap,
        rolling_correlation,
        current_a,
        news_indices,
        reclaim_status,
        mss,
    )
    confidence = _confidence_score(
        previous_a,
        current_a,
        previous_b,
        current_b,
        sync,
        gap,
        rolling_correlation,
        reclaim_status,
        mss,
        fvg_ob,
        false_flags,
    )

    reasons = [
        _relationship_reason(primary_symbol, comparison_symbol, divergence_type),
        f"{primary_symbol} swept {sweep_side} liquidity",
    ]
    if reclaim_status != "none":
        reasons.append(f"{primary_symbol} reclaim/rejection confirmed")
    if mss["mss_confirmed"]:
        reasons.append(f"{mss['mss_direction']} MSS confirmed on {primary_symbol}")
    if fvg_ob != "none":
        reasons.append(f"{fvg_ob} after SMT divergence")

    return {
        "concept_name": "SMT Divergence",
        "primary_asset": primary_symbol,
        "comparison_asset": comparison_symbol,
        "timeframe": previous_a.timeframe or current_a.timeframe,
        "divergence_id": (
            f"SMT_{divergence_type.value.upper()}_{current_a.swing_id}_"
            f"{current_b.swing_id}"
        ),
        "divergence_type": divergence_type.value,
        "correlation_type": correlation_type.value,
        "direction_bias": direction_bias.value,
        "reference_swings": _reference_swings(
            previous_a,
            current_a,
            previous_b,
            current_b,
        ),
        "liquidity_context": {
            "asset_a_swept_liquidity": True,
            "swept_side": sweep_side,
            "swept_level": previous_a.price,
            "reclaim_status": reclaim_status,
        },
        "confirmation": {
            **mss,
            "fvg_ob_followthrough": fvg_ob,
        },
        "data_quality": {
            **sync,
            "swing_time_gap_bars": gap,
            "rolling_correlation": round(rolling_correlation, 4),
            "correlation_status": _correlation_status(rolling_correlation),
        },
        "confidence_score": confidence,
        "false_positive_flags": false_flags,
        "invalidation_status": "valid" if confidence >= 7 else "watchlist_only",
        "entry_allowed_from_smt_alone": False,
        "reasons": reasons,
        "warnings": [
            "SMT is confirmation only, not a standalone entry signal",
            "Require MSS, displacement, FVG/OB, target liquidity, and risk plan",
        ],
    }


def _confidence_score(
    previous_a: _Swing,
    current_a: _Swing,
    previous_b: _Swing,
    current_b: _Swing,
    sync: Mapping[str, Any],
    gap: int | None,
    rolling_correlation: float,
    reclaim_status: str,
    mss: Mapping[str, Any],
    fvg_ob: str,
    false_flags: Sequence[str],
) -> float:
    score = 3.0
    score += min(1.5, (previous_a.strength_score + current_a.strength_score) / 12)
    score += min(1.0, (previous_b.strength_score + current_b.strength_score) / 16)
    score += 1.0 if sync["synchronization_status"] == "clean" else 0.4
    score += min(1.2, abs(rolling_correlation))
    score += 1.2 if reclaim_status != "none" else 0.0
    score += 1.4 if mss["mss_confirmed"] else 0.0
    score += 0.8 if fvg_ob != "none" else 0.0
    if gap is not None and gap <= 1:
        score += 0.4
    if false_flags:
        score -= min(3.0, len(false_flags) * 0.8)
    if sync["synchronization_status"] == "poor":
        score = min(score, 4.0)
    if abs(rolling_correlation) < 0.25:
        score = min(score, 4.0)
    if reclaim_status == "none":
        score = min(score, 6.0)
    if not mss["mss_confirmed"]:
        score = min(score, 6.5)
    return round(max(0.0, min(10.0, score)), 2)


def _reclaim_status(
    candles: Sequence[_Candle],
    previous: _Swing,
    current: _Swing,
    bullish: bool,
) -> str:
    after = [candle for candle in candles if candle.index > current.index]
    if bullish:
        if any(candle.close > previous.price for candle in after[:5]):
            return "reclaimed_back_above_swept_low"
    elif any(candle.close < previous.price for candle in after[:5]):
        return "rejected_back_below_swept_high"
    return "none"


def _mss_status(
    candles: Sequence[_Candle],
    previous: _Swing,
    current: _Swing,
    bullish: bool,
) -> dict[str, Any]:
    before = [
        candle
        for candle in candles
        if previous.index <= candle.index <= current.index
    ]
    after = [candle for candle in candles if candle.index > current.index]
    if not before or not after:
        return {"mss_confirmed": False, "mss_direction": "none"}
    if bullish:
        break_level = max(candle.high for candle in before)
        confirmed = any(candle.close > break_level for candle in after[:6])
        return {
            "mss_confirmed": confirmed,
            "mss_direction": "bullish" if confirmed else "none",
        }
    break_level = min(candle.low for candle in before)
    confirmed = any(candle.close < break_level for candle in after[:6])
    return {
        "mss_confirmed": confirmed,
        "mss_direction": "bearish" if confirmed else "none",
    }


def _fvg_followthrough(
    candles: Sequence[_Candle],
    current: _Swing,
    bullish: bool,
) -> str:
    after = [candle for candle in candles if candle.index >= current.index][:8]
    for first, _, third in zip(after, after[1:], after[2:]):
        if bullish and first.high < third.low:
            return "bullish_fvg_created"
        if not bullish and first.low > third.high:
            return "bearish_fvg_created"
    return "none"


def _false_positive_flags(
    sync: Mapping[str, Any],
    swing_gap_bars: int | None,
    rolling_correlation: float,
    current_a: _Swing,
    news_indices: set[int],
    reclaim_status: str,
    mss: Mapping[str, Any],
) -> list[str]:
    flags: list[str] = []
    if sync["synchronization_status"] != "clean":
        flags.append("synchronization_or_delayed_confirmation_issue")
    if swing_gap_bars is None or swing_gap_bars > sync["timestamp_tolerance_used"]:
        flags.append("swing_timing_mismatch")
    if abs(rolling_correlation) < 0.35:
        flags.append("weak_or_unstable_correlation")
    if current_a.index in news_indices:
        flags.append("possible_news_spike_distortion")
    if reclaim_status == "none":
        flags.append("no_reclaim_or_rejection_after_sweep")
    if not mss["mss_confirmed"]:
        flags.append("no_mss_confirmation")
    return flags


def _relationship_reason(
    primary: str,
    comparison: str,
    divergence_type: SMTDivergenceType,
) -> str:
    mapping = {
        SMTDivergenceType.BULLISH_POSITIVE: (
            f"{primary} made lower low while positively correlated "
            f"{comparison} failed to make lower low"
        ),
        SMTDivergenceType.BEARISH_POSITIVE: (
            f"{primary} made higher high while positively correlated "
            f"{comparison} failed to make higher high"
        ),
        SMTDivergenceType.BULLISH_INVERSE: (
            f"{primary} made lower low while inverse asset "
            f"{comparison} failed to make higher high"
        ),
        SMTDivergenceType.BEARISH_INVERSE: (
            f"{primary} made higher high while inverse asset "
            f"{comparison} failed to make lower low"
        ),
    }
    return mapping.get(divergence_type, "No SMT swing relationship confirmed")


def _reference_swings(
    previous_a: _Swing,
    current_a: _Swing,
    previous_b: _Swing,
    current_b: _Swing,
) -> dict[str, Any]:
    return {
        "asset_a": {
            "previous_swing": _swing_payload(previous_a),
            "current_swing": {
                **_swing_payload(current_a),
                "relationship": _relationship(previous_a, current_a),
            },
        },
        "asset_b": {
            "previous_swing": _swing_payload(previous_b),
            "current_swing": {
                **_swing_payload(current_b),
                "relationship": _failed_relationship(previous_b, current_b),
            },
        },
    }


def _swing_payload(swing: _Swing) -> dict[str, Any]:
    return {
        "swing_id": swing.swing_id,
        "type": swing.swing_type.value,
        "timestamp": swing.timestamp,
        "index": swing.index,
        "price": swing.price,
        "strength_score": swing.strength_score,
    }


def _relationship(previous: _Swing, current: _Swing) -> str:
    if current.swing_type == SMTSwingType.HIGH:
        return "higher_high" if current.price > previous.price else "not_higher_high"
    return "lower_low" if current.price < previous.price else "not_lower_low"


def _failed_relationship(previous: _Swing, current: _Swing) -> str:
    if current.swing_type == SMTSwingType.HIGH:
        return (
            "failed_to_make_higher_high"
            if current.price <= previous.price
            else "confirmed_higher_high"
        )
    return (
        "failed_to_make_lower_low"
        if current.price >= previous.price
        else "confirmed_lower_low"
    )


def _made_expected_extreme(
    previous: _Swing,
    current: _Swing,
    swing_type: SMTSwingType,
    threshold_percent: float,
) -> bool:
    threshold = abs(previous.price) * threshold_percent
    if swing_type == SMTSwingType.HIGH:
        return current.price > previous.price + threshold
    return current.price < previous.price - threshold


def _failed_to_confirm(
    previous: _Swing,
    current: _Swing,
    swing_type: SMTSwingType,
    threshold_percent: float,
) -> bool:
    threshold = abs(previous.price) * threshold_percent
    if swing_type == SMTSwingType.HIGH:
        return current.price <= previous.price + threshold
    return current.price >= previous.price - threshold


def _nearest_swing(
    target: _Swing,
    candidates: Sequence[_Swing],
    timestamp_to_bar: Mapping[Any, int],
    tolerance: int,
) -> _Swing | None:
    target_bar = _bar_for(target, timestamp_to_bar)
    if target_bar is None:
        return None
    ranked: list[tuple[int, _Swing]] = []
    for candidate in candidates:
        candidate_bar = _bar_for(candidate, timestamp_to_bar)
        if candidate_bar is None:
            continue
        gap = abs(candidate_bar - target_bar)
        if gap <= tolerance:
            ranked.append((gap, candidate))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1].index))
    return ranked[0][1]


def _swing_gap_bars(
    asset_a_swing: _Swing,
    asset_b_swing: _Swing,
    timestamp_to_bar: Mapping[Any, int],
) -> int | None:
    a_bar = _bar_for(asset_a_swing, timestamp_to_bar)
    b_bar = _bar_for(asset_b_swing, timestamp_to_bar)
    if a_bar is None or b_bar is None:
        return None
    return abs(a_bar - b_bar)


def _bar_for(swing: _Swing, timestamp_to_bar: Mapping[Any, int]) -> int | None:
    key = _timestamp_key(swing.timestamp)
    if key in timestamp_to_bar:
        return timestamp_to_bar[key]
    return swing.index


def _to_candles(rows: Sequence[Mapping[str, Any]], fallback_symbol: str) -> list[_Candle]:
    candles: list[_Candle] = []
    for position, row in enumerate(_records(rows)):
        is_closed = bool(_get(row, "is_closed", "closed", default=True))
        if not is_closed:
            continue
        candles.append(
            _Candle(
                index=int(_get(row, "index", default=position)),
                timestamp=_get(row, "timestamp", "time", "datetime", default=position),
                open=float(_get(row, "open", default=0.0)),
                high=float(_get(row, "high", default=0.0)),
                low=float(_get(row, "low", default=0.0)),
                close=float(_get(row, "close", default=0.0)),
                volume=float(_get(row, "volume", default=0.0)),
                timeframe=str(_get(row, "timeframe", default="")),
                symbol=str(_get(row, "symbol", default=fallback_symbol)),
            )
        )
    return candles


def _to_swings(rows: Sequence[Mapping[str, Any]], min_strength: float) -> list[_Swing]:
    swings: list[_Swing] = []
    for position, row in enumerate(_records(rows)):
        confirmed = bool(
            _get(row, "confirmed_status", "is_confirmed", "confirmed", default=True)
        )
        strength = float(_get(row, "strength_score", "quality_score", default=0.0))
        if not confirmed or strength < min_strength:
            continue
        raw_type = str(_get(row, "type", "swing_type", default="")).lower()
        if raw_type in {"high", "swing_high", "swinghigh"}:
            swing_type = SMTSwingType.HIGH
        elif raw_type in {"low", "swing_low", "swinglow"}:
            swing_type = SMTSwingType.LOW
        else:
            continue
        price = float(
            _get(
                row,
                "price",
                "level",
                "high" if swing_type == SMTSwingType.HIGH else "low",
                default=0.0,
            )
        )
        swings.append(
            _Swing(
                swing_id=str(_get(row, "swing_id", "id", default=f"SWING_{position}")),
                timestamp=_get(row, "timestamp", "time", "datetime", default=position),
                index=int(_get(row, "index", default=position)),
                swing_type=swing_type,
                price=price,
                strength_score=strength,
                confirmed=confirmed,
                timeframe=str(_get(row, "timeframe", default="")),
            )
        )
    return sorted(swings, key=lambda swing: (swing.index, str(swing.timestamp)))


def _synchronization_snapshot(
    candles_a: Sequence[_Candle],
    candles_b: Sequence[_Candle],
    tolerance: int,
) -> dict[str, Any]:
    keys_a = {_timestamp_key(candle.timestamp) for candle in candles_a}
    keys_b = {_timestamp_key(candle.timestamp) for candle in candles_b}
    matched = keys_a & keys_b
    total = max(len(keys_a), len(keys_b), 1)
    ratio = len(matched) / total
    if ratio >= 0.95:
        status = SMTSynchronizationStatus.CLEAN
    elif ratio >= 0.75:
        status = SMTSynchronizationStatus.ACCEPTABLE
    else:
        status = SMTSynchronizationStatus.POOR
    return {
        "synchronization_status": status.value,
        "matched_candles": len(matched),
        "asset_a_candles": len(keys_a),
        "asset_b_candles": len(keys_b),
        "missing_data_warning": ratio < 0.95,
        "timestamp_tolerance_used": tolerance,
        "matched_timestamps": matched,
    }


def _rolling_correlation(
    candles_a: Sequence[_Candle],
    candles_b: Sequence[_Candle],
    period: int,
) -> float:
    by_b = {_timestamp_key(candle.timestamp): candle for candle in candles_b}
    paired = [
        (candle.close, by_b[_timestamp_key(candle.timestamp)].close)
        for candle in candles_a
        if _timestamp_key(candle.timestamp) in by_b
    ]
    if len(paired) < 3:
        return 0.0
    paired = paired[-period:]
    returns_a = [current[0] - previous[0] for previous, current in zip(paired, paired[1:])]
    returns_b = [current[1] - previous[1] for previous, current in zip(paired, paired[1:])]
    if len(returns_a) < 2:
        return 0.0
    mean_a = sum(returns_a) / len(returns_a)
    mean_b = sum(returns_b) / len(returns_b)
    covariance = sum((a - mean_a) * (b - mean_b) for a, b in zip(returns_a, returns_b))
    variance_a = sum((a - mean_a) ** 2 for a in returns_a)
    variance_b = sum((b - mean_b) ** 2 for b in returns_b)
    denominator = sqrt(variance_a * variance_b)
    if denominator == 0:
        return 0.0
    return max(-1.0, min(1.0, covariance / denominator))


def _resolve_correlation(
    correlation_type: str,
    rolling_correlation: float,
    min_abs: float,
) -> tuple[SMTCorrelationType, list[str]]:
    normalized = str(correlation_type).lower().strip()
    warnings: list[str] = []
    if normalized in {"positive", "pos"}:
        if rolling_correlation < min_abs:
            warnings.append("configured_positive_correlation_not_confirmed")
        return SMTCorrelationType.POSITIVE, warnings
    if normalized in {"inverse", "negative", "inv"}:
        if rolling_correlation > -min_abs:
            warnings.append("configured_inverse_correlation_not_confirmed")
        return SMTCorrelationType.INVERSE, warnings
    if rolling_correlation >= min_abs:
        return SMTCorrelationType.POSITIVE, ["correlation_type_inferred_positive"]
    if rolling_correlation <= -min_abs:
        return SMTCorrelationType.INVERSE, ["correlation_type_inferred_inverse"]
    return SMTCorrelationType.UNKNOWN, ["correlation_type_unknown_and_weak"]


def _correlation_status(value: float) -> str:
    absolute = abs(value)
    if absolute >= 0.7:
        return "strong"
    if absolute >= 0.35:
        return "moderate"
    return "weak"


def _deduplicate_events(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for event in events:
        refs = event["reference_swings"]
        key = (
            refs["asset_a"]["previous_swing"]["swing_id"],
            refs["asset_a"]["current_swing"]["swing_id"],
            refs["asset_b"]["previous_swing"]["swing_id"],
            refs["asset_b"]["current_swing"]["swing_id"],
        )
        current = best_by_key.get(key)
        if current is None or event["confidence_score"] > current["confidence_score"]:
            best_by_key[key] = event
    return list(best_by_key.values())


def _invalid_result(
    primary_symbol: str,
    comparison_symbol: str,
    correlation_type: str,
    sync: Mapping[str, Any],
    failed_requirements: Sequence[str],
    *,
    rolling_correlation: float = 0.0,
    warnings: Sequence[str] | None = None,
) -> dict[str, Any]:
    return {
        "concept_name": "SMT Divergence",
        "primary_asset": primary_symbol,
        "comparison_asset": comparison_symbol,
        "divergence_id": None,
        "divergence_type": SMTDivergenceType.INVALID_OR_WEAK.value,
        "correlation_type": correlation_type,
        "direction_bias": SMTDirectionBias.UNCLEAR.value,
        "reference_swings": None,
        "smt_events": [],
        "data_quality": {
            **sync,
            "rolling_correlation": round(rolling_correlation, 4),
            "correlation_status": _correlation_status(rolling_correlation),
        },
        "liquidity_context": {
            "asset_a_swept_liquidity": False,
            "swept_side": "none",
            "reclaim_status": "none",
        },
        "confirmation": {
            "mss_confirmed": False,
            "mss_direction": "none",
            "fvg_ob_followthrough": "none",
        },
        "confidence_score": 2.0,
        "failed_requirements": list(failed_requirements),
        "false_positive_flags": list(failed_requirements),
        "entry_allowed_from_smt_alone": False,
        "warnings": list(warnings or [])
        + [
            "Do not force SMT when synchronization, correlation, or swings are weak",
            "SMT is confirmation only, not a standalone entry signal",
        ],
        "reasons": [],
    }


def _records(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if hasattr(rows, "to_dict"):
        return list(rows.to_dict("records"))  # type: ignore[call-arg, union-attr]
    return list(rows)


def _get(row: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return default


def _timestamp_key(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return value
