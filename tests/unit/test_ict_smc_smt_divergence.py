from datetime import datetime, timedelta

from src.analytics.ict_smc.smt_divergence import detect_smt_divergence


BASE_TIME = datetime(2026, 6, 4, 9, 0)


def _bars(closes, *, symbol, lows=None, highs=None, shift_minutes=0):
    rows = []
    lows = lows or [close - 0.4 for close in closes]
    highs = highs or [close + 0.4 for close in closes]
    for index, close in enumerate(closes):
        rows.append(
            {
                "timestamp": BASE_TIME + timedelta(minutes=5 * index + shift_minutes),
                "index": index,
                "open": close - 0.15,
                "high": highs[index],
                "low": lows[index],
                "close": close,
                "volume": 100 + index,
                "symbol": symbol,
                "timeframe": "5m",
                "is_closed": True,
            }
        )
    rows.append(
        {
            "timestamp": BASE_TIME + timedelta(minutes=5 * len(closes) + shift_minutes),
            "index": len(closes),
            "open": closes[-1],
            "high": closes[-1] + 0.2,
            "low": closes[-1] - 0.2,
            "close": closes[-1],
            "volume": 0,
            "symbol": symbol,
            "timeframe": "5m",
            "is_closed": False,
        }
    )
    return rows


def _swing(swing_id, index, swing_type, price, strength=8.0):
    return {
        "swing_id": swing_id,
        "timestamp": BASE_TIME + timedelta(minutes=5 * index),
        "index": index,
        "type": swing_type,
        "price": price,
        "strength_score": strength,
        "confirmed_status": True,
        "timeframe": "5m",
    }


def test_valid_bullish_smt_positive_correlation() -> None:
    asset_a = _bars(
        [103, 102, 100, 101, 99, 98.4, 100.2, 104, 105],
        symbol="EURUSD",
        lows=[102.6, 101.6, 100.0, 100.4, 98.8, 98.0, 99.8, 103.0, 104.3],
        highs=[103.3, 102.5, 100.7, 101.5, 99.6, 99.2, 100.7, 104.6, 105.5],
    )
    asset_b = _bars(
        [203, 202, 200.4, 201, 202, 201.7, 203, 206, 207],
        symbol="GBPUSD",
        lows=[202.6, 201.6, 200.0, 200.7, 201.6, 201.0, 202.5, 205.5, 206.4],
        highs=[203.4, 202.4, 200.8, 201.4, 202.5, 202.2, 203.5, 206.4, 207.4],
    )

    result = detect_smt_divergence(
        asset_a,
        asset_b,
        [_swing("A_LOW_PREV", 2, "swing_low", 100.0), _swing("A_LOW_CUR", 5, "swing_low", 98.0)],
        [_swing("B_LOW_PREV", 2, "swing_low", 200.0), _swing("B_LOW_CUR", 5, "swing_low", 201.0)],
        primary_asset_symbol="EURUSD",
        comparison_asset_symbol="GBPUSD",
        correlation_type="positive",
    )

    assert result["divergence_type"] == "bullish_smt_positive_correlation"
    assert result["direction_bias"] == "bullish_for_asset_a"
    assert result["reference_swings"]["asset_a"]["current_swing"]["relationship"] == "lower_low"
    assert result["liquidity_context"]["swept_side"] == "sell_side"
    assert result["confirmation"]["mss_confirmed"] is True
    assert result["entry_allowed_from_smt_alone"] is False
    assert result["confidence_score"] >= 7


def test_valid_bearish_smt_positive_correlation() -> None:
    asset_a = _bars(
        [97, 98, 100, 99, 101, 103.4, 99.5, 97.2, 96.5],
        symbol="NAS100",
        lows=[96.6, 97.6, 99.1, 98.5, 100.2, 102.2, 98.7, 96.8, 96.0],
        highs=[97.4, 98.4, 100.0, 99.5, 101.5, 103.8, 100.0, 97.7, 96.9],
    )
    asset_b = _bars(
        [197, 198, 200, 199, 198.5, 199.3, 198.2, 197.4, 196.8],
        symbol="SPX",
        lows=[196.5, 197.5, 199.4, 198.5, 197.8, 198.7, 197.5, 196.8, 196.1],
        highs=[197.5, 198.5, 200.0, 199.6, 199.2, 199.8, 198.8, 197.9, 197.2],
    )

    result = detect_smt_divergence(
        asset_a,
        asset_b,
        [
            _swing("A_HIGH_PREV", 2, "swing_high", 100.0),
            _swing("A_HIGH_CUR", 5, "swing_high", 103.8),
        ],
        [
            _swing("B_HIGH_PREV", 2, "swing_high", 200.0),
            _swing("B_HIGH_CUR", 5, "swing_high", 199.8),
        ],
        primary_asset_symbol="NAS100",
        comparison_asset_symbol="SPX",
        correlation_type="positive",
    )

    assert result["divergence_type"] == "bearish_smt_positive_correlation"
    assert result["direction_bias"] == "bearish_for_asset_a"
    assert result["liquidity_context"]["swept_side"] == "buy_side"
    assert result["confirmation"]["mss_direction"] == "bearish"
    assert result["confidence_score"] >= 7


def test_valid_bullish_smt_inverse_correlation_xauusd_vs_dxy() -> None:
    xauusd = _bars(
        [103, 102, 100, 101, 99, 98.5, 100.3, 104, 105],
        symbol="XAUUSD",
        lows=[102.4, 101.3, 100.0, 100.5, 98.8, 98.0, 99.7, 103.0, 104.2],
        highs=[103.4, 102.5, 100.8, 101.6, 99.7, 99.2, 100.9, 104.7, 105.4],
    )
    dxy = _bars(
        [100, 101, 104, 103, 104.2, 104.4, 103, 99, 98],
        symbol="DXY",
        lows=[99.6, 100.6, 103.4, 102.6, 103.6, 103.8, 102.4, 98.4, 97.6],
        highs=[100.4, 101.4, 105.0, 103.7, 104.6, 104.8, 103.4, 99.4, 98.4],
    )

    result = detect_smt_divergence(
        xauusd,
        dxy,
        [
            _swing("XAU_LOW_PREV", 2, "swing_low", 100.0),
            _swing("XAU_LOW_CUR", 5, "swing_low", 98.0),
        ],
        [
            _swing("DXY_HIGH_PREV", 2, "swing_high", 105.0),
            _swing("DXY_HIGH_CUR", 5, "swing_high", 104.8),
        ],
        primary_asset_symbol="XAUUSD",
        comparison_asset_symbol="DXY",
        correlation_type="inverse",
    )

    assert result["divergence_type"] == "bullish_smt_inverse_correlation"
    assert result["direction_bias"] == "bullish_for_asset_a"
    assert result["data_quality"]["rolling_correlation"] < 0
    assert result["confidence_score"] >= 7


def test_valid_bearish_smt_inverse_correlation_xauusd_vs_dxy() -> None:
    xauusd = _bars(
        [97, 98, 100, 99, 101, 103.6, 99.4, 97.2, 96.5],
        symbol="XAUUSD",
        lows=[96.5, 97.5, 99.2, 98.4, 100.2, 102.2, 98.7, 96.8, 96.0],
        highs=[97.4, 98.4, 100.0, 99.5, 101.5, 104.0, 100.1, 97.6, 96.8],
    )
    dxy = _bars(
        [103, 102, 96, 97, 96.5, 96.7, 99, 102, 104],
        symbol="DXY",
        lows=[102.4, 101.4, 95.0, 96.4, 95.8, 95.7, 98.3, 101.3, 103.3],
        highs=[103.5, 102.4, 96.6, 97.5, 97.1, 97.2, 99.5, 102.5, 104.5],
    )

    result = detect_smt_divergence(
        xauusd,
        dxy,
        [
            _swing("XAU_HIGH_PREV", 2, "swing_high", 100.0),
            _swing("XAU_HIGH_CUR", 5, "swing_high", 104.0),
        ],
        [
            _swing("DXY_LOW_PREV", 2, "swing_low", 95.0),
            _swing("DXY_LOW_CUR", 5, "swing_low", 95.7),
        ],
        primary_asset_symbol="XAUUSD",
        comparison_asset_symbol="DXY",
        correlation_type="inverse",
    )

    assert result["divergence_type"] == "bearish_smt_inverse_correlation"
    assert result["direction_bias"] == "bearish_for_asset_a"
    assert result["liquidity_context"]["swept_side"] == "buy_side"
    assert result["confirmation"]["mss_confirmed"] is True
    assert result["confidence_score"] >= 7


def test_false_smt_from_bad_synchronization_is_rejected() -> None:
    asset_a = _bars([103, 102, 100, 101, 99, 98, 100], symbol="XAUUSD")
    asset_b = _bars([203, 202, 200, 201, 199, 198, 200], symbol="DXY", shift_minutes=30)

    result = detect_smt_divergence(
        asset_a,
        asset_b,
        [_swing("A_LOW_PREV", 2, "swing_low", 100.0), _swing("A_LOW_CUR", 5, "swing_low", 98.0)],
        [_swing("B_LOW_PREV", 2, "swing_low", 200.0), _swing("B_LOW_CUR", 5, "swing_low", 198.0)],
        primary_asset_symbol="XAUUSD",
        comparison_asset_symbol="DXY",
        correlation_type="positive",
    )

    assert result["divergence_type"] == "invalid_or_weak_smt"
    assert result["direction_bias"] == "unclear"
    assert result["confidence_score"] <= 4
    assert "synchronization_or_delayed_confirmation_issue" in result["false_positive_flags"]
