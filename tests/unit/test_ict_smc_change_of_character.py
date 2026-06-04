from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.change_of_character import (
    CHoCHConfidenceGrade,
    CHoCHDetectionConfig,
    CHoCHDirection,
    CHoCHLiquidityEvent,
    CHoCHStatus,
    ICTCHoCHDetector,
    detect_choch,
)
from src.analytics.ict_smc.swing_points import (
    DetectedSwingPoint,
    SwingLiquidityType,
    SwingPointStatus,
    SwingPointType,
    SwingStrengthLabel,
)


def _row(index: int, open_p: float, high_p: float, low_p: float, close_p: float, trend_state: str) -> dict:
    start = datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(minutes=index)
    return {
        "index": index,
        "timestamp": start,
        "symbol": "XAUUSD",
        "timeframe": "1m",
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": 100 + index,
        "is_closed": True,
        "trend_state": trend_state,
    }


def _swing_high(index: int = 3, price: float = 108.0, strength: float = 5.2) -> DetectedSwingPoint:
    ts = datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(minutes=index)
    return DetectedSwingPoint(
        index=index,
        timestamp=ts,
        confirmation_index=index + 1,
        confirmation_timestamp=ts + timedelta(minutes=1),
        price=price,
        type=SwingPointType.SWING_HIGH,
        strength_score=strength,
        strength_label=SwingStrengthLabel.STRONG,
        timeframe="1m",
        timeframe_weight=0.5,
        liquidity_type=SwingLiquidityType.BUY_SIDE,
        status=SwingPointStatus.UNSWEPT,
        used_for=("liquidity", "choch_reference"),
        reasons=("LH bearish internal structure reference",),
    )


def _swing_low(index: int = 3, price: float = 92.0, strength: float = 5.2) -> DetectedSwingPoint:
    ts = datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(minutes=index)
    return DetectedSwingPoint(
        index=index,
        timestamp=ts,
        confirmation_index=index + 1,
        confirmation_timestamp=ts + timedelta(minutes=1),
        price=price,
        type=SwingPointType.SWING_LOW,
        strength_score=strength,
        strength_label=SwingStrengthLabel.STRONG,
        timeframe="1m",
        timeframe_weight=0.5,
        liquidity_type=SwingLiquidityType.SELL_SIDE,
        status=SwingPointStatus.UNSWEPT,
        used_for=("liquidity", "choch_reference"),
        reasons=("HL bullish internal structure reference",),
    )


def _sell_side_sweep(index: int = 5) -> CHoCHLiquidityEvent:
    ts = datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(minutes=index)
    return CHoCHLiquidityEvent(
        event_index=index,
        timestamp=ts,
        type="sell_side_sweep",
        swept_level=95.0,
        swept_swing_index=2,
        direction="bullish_reversal_context",
        valid=True,
        strength_score=7.4,
        sweep_candle_low=94.0,
        sweep_candle_close=96.0,
    )


def _buy_side_sweep(index: int = 5) -> CHoCHLiquidityEvent:
    ts = datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(minutes=index)
    return CHoCHLiquidityEvent(
        event_index=index,
        timestamp=ts,
        type="buy_side_sweep",
        swept_level=105.0,
        swept_swing_index=2,
        direction="bearish_reversal_context",
        valid=True,
        strength_score=7.4,
        sweep_candle_high=106.0,
        sweep_candle_close=104.0,
    )


def test_valid_bullish_choch_after_sell_side_sweep_is_warning_only() -> None:
    rows = [
        _row(0, 110, 111, 104, 105, "bearish"),
        _row(1, 105, 109, 100, 101, "bearish"),
        _row(2, 101, 106, 96, 98, "bearish"),
        _row(3, 98, 108, 97, 100, "bearish"),
        _row(4, 100, 103, 95, 96, "bearish"),
        _row(5, 96, 99, 94, 98, "bearish"),
        _row(6, 98, 114, 97, 113, "bearish"),
    ]
    detector = ICTCHoCHDetector(CHoCHDetectionConfig(break_buffer_atr_multiplier=0.0, previous_movement="bearish"))

    events = detector.detect(rows, [_swing_high()], [_sell_side_sweep()])

    confirmed = [event for event in events if event.detected and event.direction == CHoCHDirection.BULLISH]
    assert len(confirmed) == 1
    assert confirmed[0].status == CHoCHStatus.CONFIRMED
    assert confirmed[0].liquidity_context.sweep_before_choch is True
    assert confirmed[0].signal_usage.warning_signal is True
    assert confirmed[0].signal_usage.entry_allowed is False
    assert confirmed[0].quality_score >= 6.0


def test_valid_bearish_choch_after_buy_side_sweep_is_warning_only() -> None:
    rows = [
        _row(0, 90, 96, 89, 95, "bullish"),
        _row(1, 95, 100, 94, 99, "bullish"),
        _row(2, 99, 104, 97, 103, "bullish"),
        _row(3, 103, 105, 92, 101, "bullish"),
        _row(4, 101, 106, 98, 104, "bullish"),
        _row(5, 104, 107, 99, 102, "bullish"),
        _row(6, 102, 103, 87, 88, "bullish"),
    ]
    detector = ICTCHoCHDetector(CHoCHDetectionConfig(break_buffer_atr_multiplier=0.0, previous_movement="bullish"))

    events = detector.detect(rows, [_swing_low()], [_buy_side_sweep()])

    confirmed = [event for event in events if event.detected and event.direction == CHoCHDirection.BEARISH]
    assert len(confirmed) == 1
    assert confirmed[0].status == CHoCHStatus.CONFIRMED
    assert confirmed[0].liquidity_context.sweep_before_choch is True
    assert confirmed[0].signal_usage.entry_allowed is False


def test_noisy_choch_without_sweep_is_low_quality_warning() -> None:
    rows = [
        _row(0, 110, 111, 104, 105, "bearish"),
        _row(1, 105, 109, 100, 101, "bearish"),
        _row(2, 101, 106, 96, 98, "bearish"),
        _row(3, 98, 108, 97, 100, "bearish"),
        _row(4, 100, 103, 95, 96, "bearish"),
        _row(5, 96, 109, 95, 108.5, "bearish"),
    ]
    detector = ICTCHoCHDetector(CHoCHDetectionConfig(break_buffer_atr_multiplier=0.0, previous_movement="bearish"))

    events = detector.detect(rows, [_swing_high(strength=3.8)], [])

    assert len(events) == 1
    assert events[0].detected is True
    assert events[0].signal_usage.entry_allowed is False
    assert events[0].quality_score <= 6.0
    assert "no_liquidity_sweep_before_choch" in events[0].warnings
    assert "weak_internal_swing_level" in events[0].warnings


def test_wick_only_choch_candidate_is_not_confirmed() -> None:
    rows = [
        _row(0, 90, 96, 89, 95, "bullish"),
        _row(1, 95, 100, 94, 99, "bullish"),
        _row(2, 99, 104, 97, 103, "bullish"),
        _row(3, 103, 105, 92, 101, "bullish"),
        _row(4, 101, 106, 98, 104, "bullish"),
        _row(5, 104, 106, 89, 94, "bullish"),
    ]
    detector = ICTCHoCHDetector(CHoCHDetectionConfig(break_buffer_atr_multiplier=0.0, previous_movement="bullish"))

    events = detector.detect(rows, [_swing_low()], [_buy_side_sweep()])

    assert len(events) == 1
    assert events[0].detected is False
    assert events[0].status == CHoCHStatus.WICK_ONLY_CANDIDATE
    assert events[0].break_validation.wick_only_break is True
    assert events[0].confidence_grade == CHoCHConfidenceGrade.WEAK
    assert "wick_only_choch_candidate_not_confirmed" in events[0].warnings


def test_strong_choch_can_upgrade_to_mss_candidate() -> None:
    rows = [
        _row(0, 110, 111, 104, 105, "bearish"),
        _row(1, 105, 109, 100, 101, "bearish"),
        _row(2, 101, 106, 96, 98, "bearish"),
        _row(3, 98, 108, 97, 100, "bearish"),
        _row(4, 100, 103, 95, 96, "bearish"),
        _row(5, 96, 99, 94, 98, "bearish"),
        _row(6, 98, 116, 97, 115, "bearish"),
    ]
    detector = ICTCHoCHDetector(CHoCHDetectionConfig(break_buffer_atr_multiplier=0.0, previous_movement="bearish"))

    events = detector.detect(rows, [_swing_high(strength=7.0)], [_sell_side_sweep()])

    assert len(events) == 1
    assert events[0].status == CHoCHStatus.UPGRADED_TO_MSS_CANDIDATE
    assert events[0].signal_usage.possible_upgrade == "mss_candidate"
    assert events[0].signal_usage.entry_allowed is False


def test_detect_choch_helper_returns_dicts() -> None:
    rows = [
        _row(0, 110, 111, 104, 105, "bearish"),
        _row(1, 105, 109, 100, 101, "bearish"),
        _row(2, 101, 106, 96, 98, "bearish"),
        _row(3, 98, 108, 97, 100, "bearish"),
        _row(4, 100, 103, 95, 96, "bearish"),
        _row(5, 96, 99, 94, 98, "bearish"),
        _row(6, 98, 114, 97, 113, "bearish"),
    ]

    events = detect_choch(
        rows,
        [_swing_high().as_dict()],
        [_sell_side_sweep().as_dict()],
        break_buffer_atr_multiplier=0.0,
        previous_movement="bearish",
    )

    assert len(events) == 1
    assert events[0]["concept_name"] == "CHoCH"
    assert events[0]["direction"] == "bullish"
    assert events[0]["signal_usage"]["entry_allowed"] is False
