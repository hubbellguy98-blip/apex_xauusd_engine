from datetime import datetime, timedelta

from src.analytics.ict_smc.stop_loss_logic import (
    StopStatus,
    calculate_smc_stop_loss,
)


BASE_TIME = datetime(2026, 6, 12, 10, 0)


def _row(index, open_, high, low, close, *, closed=True):
    return {
        "timestamp": BASE_TIME + timedelta(minutes=5 * index),
        "index": index,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100,
        "is_closed": closed,
    }


def _rows():
    return [
        _row(1, 2358.0, 2362.0, 2357.2, 2361.0),
        _row(2, 2361.0, 2364.2, 2358.7, 2362.8),
        _row(3, 2362.8, 2365.0, 2360.1, 2364.4),
        _row(4, 2364.4, 2366.0, 2361.2, 2363.5, closed=False),
    ]


def test_valid_bullish_sweep_stop() -> None:
    setup = {
        "setup_id": "LONDON_RAID_BULL_001",
        "direction": "bullish",
        "entry_price": 2360.70,
        "sweep_low": 2353.80,
        "entry_zone": {
            "zone_type": "bullish_fvg",
            "zone_low": 2359.80,
            "zone_high": 2361.60,
        },
        "target": 2378.0,
        "risk_config": {
            "stop_mode": "sweep_based",
            "stop_buffer_atr_multiplier": 0.10,
            "max_stop_atr_multiplier": 10.0,
        },
    }

    result = calculate_smc_stop_loss(setup, _rows(), atr=4.0, spread_buffer=0.20)

    assert result["stop_valid"] is True
    assert result["stop_loss"] == 2353.20
    assert result["invalidation_reason"] == "below_liquidity_sweep_low"
    assert result["risk_distance"] == 7.50


def test_valid_bearish_order_block_stop() -> None:
    setup = {
        "setup_id": "NY_RAID_BEAR_001",
        "direction": "bearish",
        "entry_price": 2372.0,
        "order_block": {
            "zone_type": "bearish_order_block",
            "zone_low": 2370.0,
            "zone_high": 2375.0,
        },
        "target": 2360.0,
        "risk_config": {
            "stop_mode": "entry_zone_based",
            "stop_buffer_atr_multiplier": 0.10,
            "max_stop_atr_multiplier": 10.0,
        },
    }

    result = calculate_smc_stop_loss(setup, _rows(), atr=5.0, spread_buffer=0.20)

    assert result["stop_valid"] is True
    assert result["stop_loss"] == 2375.70
    assert result["invalidation_reason"] == "above_bearish_order_block"
    assert result["risk_distance"] == 3.70


def test_invalid_proposed_stop_inside_bullish_fvg() -> None:
    setup = {
        "setup_id": "FVG_BULL_INSIDE_STOP",
        "direction": "bullish",
        "entry_price": 2360.70,
        "proposed_stop_loss": 2360.0,
        "fvg_zone": {
            "zone_type": "bullish_fvg",
            "zone_low": 2359.80,
            "zone_high": 2361.60,
        },
        "target": 2368.0,
    }

    result = calculate_smc_stop_loss(setup, _rows(), atr=4.0, spread_buffer=0.20)

    assert result["stop_valid"] is False
    assert result["invalidation_reason"] == StopStatus.STOP_INSIDE_BULLISH_POI.value
    assert result["corrected_stop_suggestion"]["suggested_stop_loss"] == 2359.20


def test_structural_stop_can_be_blocked_when_rr_is_poor() -> None:
    setup = {
        "setup_id": "NY_RAID_BEAR_WIDE_STOP",
        "direction": "bearish",
        "entry_price": 2372.0,
        "sweep_high": 2388.0,
        "target": 2358.0,
        "risk_config": {
            "stop_mode": "conservative",
            "stop_buffer_atr_multiplier": 0.0,
            "max_stop_atr_multiplier": 10.0,
            "min_rr": 1.0,
        },
    }

    result = calculate_smc_stop_loss(setup, _rows(), atr=5.0, spread_buffer=0.0)

    assert result["stop_loss"] == 2388.0
    assert result["risk_distance"] == 16.0
    assert result["stop_valid"] is False
    assert result["decision"]["status"] == StopStatus.STOP_TOO_WIDE_FOR_RR.value
    assert result["execution_allowed"] is False


def test_xauusd_spread_buffer_is_included_for_bullish_ob_stop() -> None:
    setup = {
        "setup_id": "OB_BULL_SPREAD_BUFFER",
        "direction": "bullish",
        "entry_price": 2360.0,
        "order_block": {
            "zone_type": "bullish_order_block",
            "zone_low": 2358.0,
            "zone_high": 2362.0,
        },
        "target": 2368.0,
        "risk_config": {
            "stop_mode": "entry_zone_based",
            "stop_buffer_atr_multiplier": 0.05,
            "max_stop_atr_multiplier": 10.0,
        },
    }

    result = calculate_smc_stop_loss(setup, _rows(), atr=4.0, spread_buffer=0.30)

    assert result["stop_valid"] is True
    assert result["stop_loss"] == 2357.50
    assert result["invalidation_reason"] == "below_bullish_order_block"
    assert result["buffer_details"]["spread_buffer"] == 0.30
