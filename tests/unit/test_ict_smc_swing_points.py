from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.swing_points import (
    ICTSwingPointDetector,
    SwingDetectionConfig,
    SwingLiquidityType,
    SwingPointType,
    detect_swings,
)
from src.core.domain.market_data import CandleNode


def _candle(
    index: int, open_p: float, high_p: float, low_p: float, close_p: float, *, closed: bool = True
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


def _detector() -> ICTSwingPointDetector:
    return ICTSwingPointDetector(
        SwingDetectionConfig(
            left_bars=2,
            right_bars=2,
            min_candle_gap=1,
            min_atr_reaction=0.25,
            require_min_atr_reaction=False,
        )
    )


def test_detects_confirmed_swing_high_and_low_with_confirmation_delay() -> None:
    candles = [
        _candle(0, 100, 101, 99, 100),
        _candle(1, 100, 103, 98, 102),
        _candle(2, 102, 108, 101, 104),
        _candle(3, 104, 105, 100, 101),
        _candle(4, 101, 102, 95, 96),
        _candle(5, 96, 99, 88, 91),
        _candle(6, 91, 96, 90, 95),
        _candle(7, 95, 98, 93, 97),
    ]

    swings = _detector().detect(candles)

    assert len(swings) == 2
    assert swings[0].type == SwingPointType.SWING_HIGH
    assert swings[0].index == 2
    assert swings[0].confirmation_index == 4
    assert swings[0].price == 108
    assert swings[0].liquidity_type == SwingLiquidityType.BUY_SIDE
    assert swings[1].type == SwingPointType.SWING_LOW
    assert swings[1].index == 5
    assert swings[1].confirmation_index == 7
    assert swings[1].price == 88
    assert swings[1].liquidity_type == SwingLiquidityType.SELL_SIDE
    assert "liquidity" in swings[1].used_for


def test_forming_candle_and_last_right_bars_do_not_repaint_swings() -> None:
    candles = [
        _candle(0, 100, 101, 99, 100),
        _candle(1, 100, 103, 98, 102),
        _candle(2, 102, 108, 101, 104),
        _candle(3, 104, 105, 100, 101),
        _candle(4, 101, 102, 99, 100),
        _candle(5, 100, 130, 98, 129, closed=False),
    ]

    swings = _detector().detect(candles)

    assert [swing.index for swing in swings] == [2]
    assert all(swing.index != 5 for swing in swings)


def test_equal_high_is_not_clean_swing_high_by_default() -> None:
    candles = [
        _candle(0, 100, 101, 99, 100),
        _candle(1, 100, 108, 98, 102),
        _candle(2, 102, 108, 101, 104),
        _candle(3, 104, 105, 100, 101),
        _candle(4, 101, 102, 99, 100),
    ]

    swings = _detector().detect(candles)

    assert swings == tuple()


def test_repeated_same_side_swings_keep_more_extreme_level() -> None:
    detector = ICTSwingPointDetector(
        SwingDetectionConfig(left_bars=1, right_bars=1, min_candle_gap=4, require_min_atr_reaction=False)
    )
    candles = [
        _candle(0, 100, 101, 99, 100),
        _candle(1, 100, 105, 99, 104),
        _candle(2, 104, 103, 100, 101),
        _candle(3, 101, 108, 100, 107),
        _candle(4, 107, 106, 102, 103),
        _candle(5, 103, 104, 98, 99),
        _candle(6, 99, 103, 99, 102),
    ]

    swings = detector.detect(candles)

    assert len(swings) == 1
    assert swings[0].type == SwingPointType.SWING_HIGH
    assert swings[0].index == 3
    assert swings[0].price == 108


def test_detect_swings_accepts_dataframe_style_rows() -> None:
    start = datetime(2026, 6, 3, tzinfo=timezone.utc)
    rows = [
        {
            "timestamp": start + timedelta(minutes=index),
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": 100,
            "timeframe": "15m",
        }
        for index, (open_p, high_p, low_p, close_p) in enumerate(
            [
                (100, 101, 99, 100),
                (100, 103, 98, 102),
                (102, 108, 101, 104),
                (104, 105, 100, 101),
                (101, 102, 99, 100),
            ]
        )
    ]

    swings = detect_swings(rows, left_bars=2, right_bars=2)

    assert len(swings) == 1
    assert swings[0]["type"] == "swing_high"
    assert swings[0]["timeframe"] == "15m"
    assert swings[0]["confirmation_index"] == 4
    assert swings[0]["liquidity_type"] == "buy_side_liquidity"
