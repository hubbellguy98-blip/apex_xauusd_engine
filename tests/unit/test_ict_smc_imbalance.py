from datetime import datetime, timedelta

from src.analytics.ict_smc.imbalance import detect_imbalances


def _candle(index, open_, high, low, close):
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 5) + timedelta(minutes=index),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100,
        "is_closed": True,
    }


def _structure(direction, event_type, index):
    return {
        "direction": direction,
        "event_type": event_type,
        "confirmation_candle_index": index,
    }


def _sweep(side, index):
    return {
        "sweep_type": f"{side}_liquidity_sweep",
        "sweep_candle_index": index,
    }


def _zone(direction, low, high):
    return {
        "direction": direction,
        "zone_low": low,
        "zone_high": high,
    }


def test_valid_bullish_fvg_imbalance_after_sell_side_sweep_and_mss() -> None:
    candles = [
        _candle(0, 2346.0, 2348.0, 2344.0, 2345.0),
        _candle(1, 2345.0, 2350.2, 2344.2, 2348.8),
        _candle(2, 2348.8, 2358.0, 2348.4, 2357.2),
        _candle(3, 2356.0, 2360.0, 2354.8, 2358.7),
    ]

    imbalance = next(
        item
        for item in detect_imbalances(
            candles,
            structure_events=[_structure("bullish", "MSS", 3)],
            liquidity_sweeps=[_sweep("sell_side", 0)],
            order_blocks=[_zone("bullish", 2350.2, 2354.8)],
            context={
                "premium_discount_location": "discount",
                "htf_bias": "bullish",
                "target_liquidity": "nearest_buy_side_liquidity_above",
            },
            symbol="XAUUSD",
            timeframe="15m",
        )
        if item["creation_index"] == 3 and item["detection_method"] == "fvg_three_candle"
    )

    assert imbalance["imbalance_type"] == "bullish_fvg_imbalance"
    assert imbalance["zone_low"] == 2350.2
    assert imbalance["zone_high"] == 2354.8
    assert imbalance["active_status"] == "unfilled"
    assert imbalance["filled_percent"] == 0.0
    assert imbalance["displacement_strength"] in {"moderate", "strong", "very_strong"}
    assert 8.0 <= imbalance["quality_score"] <= 10.0


def test_valid_bearish_fvg_imbalance_after_buy_side_sweep_and_mss() -> None:
    candles = [
        _candle(0, 2376.0, 2378.0, 2372.0, 2377.0),
        _candle(1, 2377.0, 2378.4, 2370.1, 2372.0),
        _candle(2, 2372.0, 2372.4, 2361.0, 2362.0),
        _candle(3, 2362.2, 2365.4, 2359.0, 2360.5),
    ]

    imbalance = next(
        item
        for item in detect_imbalances(
            candles,
            structure_events=[_structure("bearish", "MSS", 3)],
            liquidity_sweeps=[_sweep("buy_side", 0)],
            order_blocks=[_zone("bearish", 2365.4, 2370.1)],
            context={
                "premium_discount_location": "premium",
                "htf_bias": "bearish",
                "target_liquidity": "nearest_sell_side_liquidity_below",
            },
            symbol="XAUUSD",
            timeframe="15m",
        )
        if item["creation_index"] == 3 and item["detection_method"] == "fvg_three_candle"
    )

    assert imbalance["imbalance_type"] == "bearish_fvg_imbalance"
    assert imbalance["zone_low"] == 2365.4
    assert imbalance["zone_high"] == 2370.1
    assert imbalance["active_status"] == "unfilled"
    assert imbalance["filled_percent"] == 0.0
    assert imbalance["displacement_strength"] in {"moderate", "strong", "very_strong"}
    assert 8.0 <= imbalance["quality_score"] <= 10.0


def test_half_filled_bullish_imbalance_without_invalidation() -> None:
    candles = [
        _candle(0, 2346.0, 2348.0, 2344.0, 2345.0),
        _candle(1, 2345.0, 2350.0, 2344.2, 2348.8),
        _candle(2, 2348.8, 2358.0, 2348.4, 2357.2),
        _candle(3, 2356.0, 2360.0, 2354.0, 2358.7),
        _candle(4, 2353.5, 2359.0, 2352.0, 2352.8),
    ]

    imbalance = next(
        item
        for item in detect_imbalances(candles, symbol="XAUUSD", timeframe="5m")
        if item["creation_index"] == 3 and item["imbalance_type"] == "bullish_fvg_imbalance"
    )

    assert imbalance["zone_low"] == 2350.0
    assert imbalance["zone_high"] == 2354.0
    assert imbalance["filled_percent"] == 50.0
    assert imbalance["active_status"] == "half_filled"
    assert imbalance["invalidated"] is False


def test_fully_filled_bearish_imbalance_is_not_invalidation_without_close_above_zone() -> None:
    candles = [
        _candle(0, 2376.0, 2378.0, 2372.0, 2377.0),
        _candle(1, 2377.0, 2378.4, 2370.0, 2372.0),
        _candle(2, 2372.0, 2372.4, 2361.0, 2362.0),
        _candle(3, 2362.2, 2365.0, 2359.0, 2360.5),
        _candle(4, 2360.5, 2371.0, 2360.0, 2368.0),
    ]

    imbalance = next(
        item
        for item in detect_imbalances(candles, symbol="XAUUSD", timeframe="5m")
        if item["creation_index"] == 3 and item["imbalance_type"] == "bearish_fvg_imbalance"
    )

    assert imbalance["zone_low"] == 2365.0
    assert imbalance["zone_high"] == 2370.0
    assert imbalance["filled_percent"] == 100.0
    assert imbalance["active_status"] == "fully_filled"
    assert imbalance["invalidated"] is False
    assert "full_fill_without_close_invalidation" in imbalance["warnings"]


def test_invalidated_bullish_imbalance_caps_quality_score() -> None:
    candles = [
        _candle(0, 2346.0, 2348.0, 2344.0, 2345.0),
        _candle(1, 2345.0, 2350.0, 2344.2, 2348.8),
        _candle(2, 2348.8, 2358.0, 2348.4, 2357.2),
        _candle(3, 2356.0, 2360.0, 2354.0, 2358.7),
        _candle(4, 2358.7, 2359.0, 2348.0, 2348.0),
    ]

    imbalance = next(
        item
        for item in detect_imbalances(candles, symbol="XAUUSD", timeframe="5m")
        if item["creation_index"] == 3 and item["imbalance_type"] == "bullish_fvg_imbalance"
    )

    assert imbalance["active_status"] == "invalidated"
    assert imbalance["filled_percent"] == 100.0
    assert imbalance["invalidated"] is True
    assert imbalance["quality_score"] <= 3.0
    assert "bullish_imbalance_invalidated" in imbalance["warnings"]
