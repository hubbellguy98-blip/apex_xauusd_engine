from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.market_structure_shift import (
    ICTMSSDetector,
    MSSConfidenceGrade,
    MSSDetectionConfig,
    MSSDirection,
    MSSLiquidityEvent,
    MSSStatus,
    detect_mss,
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


def _swing_high(index: int = 3, price: float = 108.0, strength: float = 7.2) -> DetectedSwingPoint:
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
        used_for=("liquidity", "mss_reference"),
        reasons=("LH bearish structure reference",),
    )


def _swing_low(index: int = 3, price: float = 92.0, strength: float = 7.2) -> DetectedSwingPoint:
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
        used_for=("liquidity", "mss_reference"),
        reasons=("HL bullish structure reference",),
    )


def _sell_side_sweep(index: int = 5) -> MSSLiquidityEvent:
    ts = datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(minutes=index)
    return MSSLiquidityEvent(
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


def _buy_side_sweep(index: int = 5) -> MSSLiquidityEvent:
    ts = datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(minutes=index)
    return MSSLiquidityEvent(
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


def test_bullish_mss_detects_shift_after_sell_side_sweep() -> None:
    rows = [
        _row(0, 110, 111, 104, 105, "bearish"),
        _row(1, 105, 109, 100, 101, "bearish"),
        _row(2, 101, 106, 96, 98, "bearish"),
        _row(3, 98, 108, 97, 100, "bearish"),
        _row(4, 100, 103, 95, 96, "bearish"),
        _row(5, 96, 99, 94, 98, "bearish"),
        _row(6, 98, 114, 97, 113, "bearish"),
    ]
    detector = ICTMSSDetector(MSSDetectionConfig(break_buffer_atr_multiplier=0.0, previous_movement="bearish"))

    events = detector.detect(rows, [_swing_high()], [_sell_side_sweep()])

    confirmed = [event for event in events if event.detected and event.direction == MSSDirection.BULLISH]
    assert len(confirmed) == 1
    assert confirmed[0].status == MSSStatus.CONFIRMED
    assert confirmed[0].liquidity_context.sweep_before_mss is True
    assert confirmed[0].displacement.displacement_strength.value in {"strong", "very_strong"}
    assert confirmed[0].entry_confirmation_use.execute_trade_now is False
    assert confirmed[0].confidence_score >= 7.0


def test_bearish_mss_detects_shift_after_buy_side_sweep() -> None:
    rows = [
        _row(0, 90, 96, 89, 95, "bullish"),
        _row(1, 95, 100, 94, 99, "bullish"),
        _row(2, 99, 104, 97, 103, "bullish"),
        _row(3, 103, 105, 92, 101, "bullish"),
        _row(4, 101, 106, 98, 104, "bullish"),
        _row(5, 104, 107, 99, 102, "bullish"),
        _row(6, 102, 103, 87, 88, "bullish"),
    ]
    detector = ICTMSSDetector(MSSDetectionConfig(break_buffer_atr_multiplier=0.0, previous_movement="bullish"))

    events = detector.detect(rows, [_swing_low()], [_buy_side_sweep()])

    confirmed = [event for event in events if event.detected and event.direction == MSSDirection.BEARISH]
    assert len(confirmed) == 1
    assert confirmed[0].status == MSSStatus.CONFIRMED
    assert confirmed[0].liquidity_context.sweep_before_mss is True
    assert confirmed[0].confidence_score >= 7.0


def test_mss_without_liquidity_sweep_is_capped_and_warned() -> None:
    rows = [
        _row(0, 110, 111, 104, 105, "bearish"),
        _row(1, 105, 109, 100, 101, "bearish"),
        _row(2, 101, 106, 96, 98, "bearish"),
        _row(3, 98, 108, 97, 100, "bearish"),
        _row(4, 100, 103, 95, 96, "bearish"),
        _row(5, 96, 110, 95, 109, "bearish"),
    ]
    detector = ICTMSSDetector(MSSDetectionConfig(break_buffer_atr_multiplier=0.0, previous_movement="bearish"))

    events = detector.detect(rows, [_swing_high(strength=5.8)], [])

    assert len(events) == 1
    assert events[0].detected is True
    assert events[0].confidence_score <= 7.0
    assert "no_liquidity_sweep_before_mss" in events[0].warnings


def test_wick_only_mss_is_not_confirmed_when_close_required() -> None:
    rows = [
        _row(0, 90, 96, 89, 95, "bullish"),
        _row(1, 95, 100, 94, 99, "bullish"),
        _row(2, 99, 104, 97, 103, "bullish"),
        _row(3, 103, 105, 92, 101, "bullish"),
        _row(4, 101, 106, 98, 104, "bullish"),
        _row(5, 104, 106, 89, 94, "bullish"),
    ]
    detector = ICTMSSDetector(MSSDetectionConfig(break_buffer_atr_multiplier=0.0, previous_movement="bullish"))

    events = detector.detect(rows, [_swing_low()], [_buy_side_sweep()])

    assert len(events) == 1
    assert events[0].detected is False
    assert events[0].status == MSSStatus.INVALIDATED
    assert events[0].break_validation.wick_only_break is True
    assert "wick_only_break_not_confirmed_mss" in events[0].warnings


def test_confirmed_mss_can_be_marked_failed_after_reclaim_loss() -> None:
    rows = [
        _row(0, 110, 111, 104, 105, "bearish"),
        _row(1, 105, 109, 100, 101, "bearish"),
        _row(2, 101, 106, 96, 98, "bearish"),
        _row(3, 98, 108, 97, 100, "bearish"),
        _row(4, 100, 103, 95, 96, "bearish"),
        _row(5, 96, 99, 94, 98, "bearish"),
        _row(6, 98, 114, 97, 113, "bearish"),
        _row(7, 113, 114, 100, 106, "bearish"),
    ]
    detector = ICTMSSDetector(
        MSSDetectionConfig(break_buffer_atr_multiplier=0.0, previous_movement="bearish", failed_mss_lookahead=2)
    )

    events = detector.detect(rows, [_swing_high()], [_sell_side_sweep()])

    confirmed = [event for event in events if event.detected and event.direction == MSSDirection.BULLISH]
    assert confirmed[0].status == MSSStatus.FAILED
    assert "mss_failed_after_reclaim_loss" in confirmed[0].warnings


def test_detect_mss_helper_returns_dicts() -> None:
    rows = [
        _row(0, 110, 111, 104, 105, "bearish"),
        _row(1, 105, 109, 100, 101, "bearish"),
        _row(2, 101, 106, 96, 98, "bearish"),
        _row(3, 98, 108, 97, 100, "bearish"),
        _row(4, 100, 103, 95, 96, "bearish"),
        _row(5, 96, 99, 94, 98, "bearish"),
        _row(6, 98, 114, 97, 113, "bearish"),
    ]

    events = detect_mss(rows, [_swing_high().as_dict()], [_sell_side_sweep().as_dict()])

    assert len(events) == 1
    assert events[0]["direction"] == "bullish"
    assert events[0]["liquidity_context"]["sweep_before_mss"] is True
    assert events[0]["confidence_grade"] in {
        MSSConfidenceGrade.STRONG.value,
        MSSConfidenceGrade.HIGH_QUALITY.value,
    }
