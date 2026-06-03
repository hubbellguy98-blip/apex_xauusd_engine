from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.break_of_structure import (
    BOSBreakType,
    BOSConfidenceGrade,
    BOSDetectionConfig,
    BOSDirection,
    BOSStatus,
    ICTBOSDetector,
    detect_bos,
)
from src.analytics.ict_smc.swing_points import (
    DetectedSwingPoint,
    SwingLiquidityType,
    SwingPointStatus,
    SwingPointType,
    SwingStrengthLabel,
)
from src.core.domain.market_data import CandleNode


def _candle(
    index: int,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    *,
    trend_state: str | None = None,
    closed: bool = True,
) -> CandleNode:
    start = datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(minutes=index)
    return CandleNode(
        symbol="XAUUSD",
        timeframe="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open_p=open_p,
        high_p=high_p,
        low_p=low_p,
        close_p=close_p,
        volume=100 + index,
        is_closed=closed,
        sequence_id=index,
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


def _swing_high(index: int = 2, price: float = 108.0, strength: float = 8.2) -> DetectedSwingPoint:
    ts = datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(minutes=index)
    return DetectedSwingPoint(
        index=index,
        timestamp=ts,
        confirmation_index=index + 2,
        confirmation_timestamp=ts + timedelta(minutes=2),
        price=price,
        type=SwingPointType.SWING_HIGH,
        strength_score=strength,
        strength_label=SwingStrengthLabel.STRONG,
        timeframe="1m",
        timeframe_weight=0.5,
        liquidity_type=SwingLiquidityType.BUY_SIDE,
        status=SwingPointStatus.UNSWEPT,
        used_for=("liquidity", "bos_reference", "target"),
    )


def _swing_low(index: int = 2, price: float = 92.0, strength: float = 7.5) -> DetectedSwingPoint:
    ts = datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(minutes=index)
    return DetectedSwingPoint(
        index=index,
        timestamp=ts,
        confirmation_index=index + 2,
        confirmation_timestamp=ts + timedelta(minutes=2),
        price=price,
        type=SwingPointType.SWING_LOW,
        strength_score=strength,
        strength_label=SwingStrengthLabel.STRONG,
        timeframe="1m",
        timeframe_weight=0.5,
        liquidity_type=SwingLiquidityType.SELL_SIDE,
        status=SwingPointStatus.UNSWEPT,
        used_for=("liquidity", "mss_reference", "target"),
    )


def test_bullish_bos_requires_close_above_confirmed_swing_high() -> None:
    candles = [
        _row(0, 100, 102, 99, 101, "bullish"),
        _row(1, 101, 105, 100, 104, "bullish"),
        _row(2, 104, 108, 103, 105, "bullish"),
        _row(3, 105, 106, 101, 102, "bullish"),
        _row(4, 102, 104, 100, 103, "bullish"),
        _row(5, 103, 112, 102, 111, "bullish"),
    ]
    detector = ICTBOSDetector(BOSDetectionConfig(break_buffer_atr_multiplier=0.0, trend_state="bullish"))

    events = detector.detect(candles, [_swing_high()])

    confirmed = [event for event in events if event.detected and not event.wick_break_only]
    assert len(confirmed) == 1
    assert confirmed[0].direction == BOSDirection.BULLISH
    assert confirmed[0].break_type == BOSBreakType.BULLISH_BOS
    assert confirmed[0].status == BOSStatus.CONFIRMED
    assert confirmed[0].broken_swing.index == 2
    assert confirmed[0].quality_score >= 5.0


def test_bearish_bos_requires_close_below_confirmed_swing_low() -> None:
    candles = [
        _row(0, 100, 102, 96, 97, "bearish"),
        _row(1, 97, 99, 94, 95, "bearish"),
        _row(2, 95, 97, 92, 94, "bearish"),
        _row(3, 94, 98, 93, 97, "bearish"),
        _row(4, 97, 99, 95, 96, "bearish"),
        _row(5, 96, 97, 88, 89, "bearish"),
    ]
    detector = ICTBOSDetector(BOSDetectionConfig(break_buffer_atr_multiplier=0.0, trend_state="bearish"))

    events = detector.detect(candles, [_swing_low()])

    confirmed = [event for event in events if event.detected and not event.wick_break_only]
    assert len(confirmed) == 1
    assert confirmed[0].direction == BOSDirection.BEARISH
    assert confirmed[0].break_type == BOSBreakType.BEARISH_BOS
    assert confirmed[0].status == BOSStatus.CONFIRMED


def test_wick_only_break_is_not_confirmed_bos_when_close_required() -> None:
    candles = [
        _row(0, 100, 102, 99, 101, "bullish"),
        _row(1, 101, 105, 100, 104, "bullish"),
        _row(2, 104, 108, 103, 105, "bullish"),
        _row(3, 105, 106, 101, 102, "bullish"),
        _row(4, 102, 104, 100, 103, "bullish"),
        _row(5, 103, 112, 102, 106, "bullish"),
    ]
    detector = ICTBOSDetector(BOSDetectionConfig(break_buffer_atr_multiplier=0.0, close_required=True))

    events = detector.detect(candles, [_swing_high()])

    assert len(events) == 1
    assert events[0].detected is False
    assert events[0].wick_break_only is True
    assert events[0].break_type == BOSBreakType.BUY_SIDE_WICK_BREAK_ONLY
    assert "wick_only_break_not_bos" in events[0].warnings


def test_aggressive_wick_mode_marks_low_confidence_candidate() -> None:
    candles = [
        _row(0, 100, 102, 99, 101, "bullish"),
        _row(1, 101, 105, 100, 104, "bullish"),
        _row(2, 104, 108, 103, 105, "bullish"),
        _row(3, 105, 106, 101, 102, "bullish"),
        _row(4, 102, 104, 100, 103, "bullish"),
        _row(5, 103, 112, 102, 106, "bullish"),
    ]
    detector = ICTBOSDetector(BOSDetectionConfig(break_buffer_atr_multiplier=0.0, close_required=False))

    events = detector.detect(candles, [_swing_high(strength=4.8)])

    assert len(events) == 1
    assert events[0].detected is True
    assert events[0].wick_break_only is True
    assert events[0].break_type == BOSBreakType.AGGRESSIVE_BULLISH_CANDIDATE
    assert events[0].quality_score <= 4.5
    assert events[0].confidence_grade in {BOSConfidenceGrade.INVALID, BOSConfidenceGrade.LOW}


def test_failed_bos_updates_status_after_close_back_inside_level() -> None:
    candles = [
        _row(0, 100, 102, 99, 101, "bullish"),
        _row(1, 101, 105, 100, 104, "bullish"),
        _row(2, 104, 108, 103, 105, "bullish"),
        _row(3, 105, 106, 101, 102, "bullish"),
        _row(4, 102, 104, 100, 103, "bullish"),
        _row(5, 103, 112, 102, 111, "bullish"),
        _row(6, 111, 112, 104, 106, "bullish"),
    ]
    detector = ICTBOSDetector(
        BOSDetectionConfig(break_buffer_atr_multiplier=0.0, trend_state="bullish", failed_bos_lookahead=2)
    )

    events = detector.detect(candles, [_swing_high()])

    confirmed = [event for event in events if event.detected and not event.wick_break_only]
    assert confirmed[0].status == BOSStatus.FAILED
    assert "failed_bos_risk" in confirmed[0].warnings


def test_detect_bos_dataframe_helper_returns_dicts() -> None:
    rows = [
        _row(0, 100, 102, 99, 101, "bearish"),
        _row(1, 101, 105, 100, 104, "bearish"),
        _row(2, 104, 108, 103, 105, "bearish"),
        _row(3, 105, 106, 101, 102, "bearish"),
        _row(4, 102, 104, 100, 103, "bearish"),
        _row(5, 103, 112, 102, 111, "bearish"),
    ]

    events = detect_bos(rows, [_swing_high().as_dict()], close_required=True)

    assert len(events) == 1
    assert events[0]["direction"] == "bullish"
    assert events[0]["break_type"] == "possible_bullish_MSS_or_CHoCH"
    assert "against prior trend" in ",".join(events[0]["warnings"])
