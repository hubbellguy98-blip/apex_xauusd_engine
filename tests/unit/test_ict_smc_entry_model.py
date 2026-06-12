from datetime import datetime, timedelta

from src.analytics.ict_smc.entry_model import (
    EntryStatus,
    EntryType,
    generate_entry_signal,
)


BASE_TIME = datetime(2026, 6, 12, 9, 0)


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


def _base_rows(last=None):
    rows = []
    for index in range(10):
        base = 103.0 + (index % 2) * 0.2
        rows.append(_row(index, base, base + 0.5, base - 0.5, base + 0.1))
    if last is not None:
        rows.append(last)
    return rows


def _confirmed_setup(**overrides):
    setup = {
        "setup_id": "LONDON_RAID_BULL_001",
        "setup_type": "liquidity_sweep_mss_fvg",
        "confirmed": True,
        "direction": "bullish",
        "setup_score": 8.7,
        "liquidity_sweep": {"swept_side": "sell_side"},
        "mss_event": {"confirmed": True},
        "displacement": {"confirmed": True},
        "fvg_zones": [
            {
                "zone_id": "FVG_BULL_001",
                "zone_type": "bullish_fvg",
                "direction": "bullish",
                "zone_low": 100.0,
                "zone_high": 102.0,
                "zone_mid": 101.0,
                "quality_score": 8.4,
                "fresh_status": "fresh",
                "created_after_mss": True,
                "created_by_displacement": True,
                "premium_discount_aligned": True,
            }
        ],
        "order_blocks": [],
        "target_liquidity": {
            "liquidity_id": "BSL_001",
            "target_price": 110.0,
            "liquidity_type": "buy_side_liquidity",
        },
        "sweep_extreme": 98.8,
        "invalidation_level": 99.0,
        "news_filter_status": {"restricted": False},
        "spread_status": {"spread_status": "normal", "spread_safe": True},
        "killzone_status": {"status": "allowed"},
    }
    setup.update(overrides)
    return setup


def _risk(**overrides):
    config = {
        "entry_mode": "aggressive",
        "min_rr": 1.5,
        "preferred_rr": 2.0,
        "minimum_entry_score": 6.5,
        "stop_buffer_atr_multiplier": 0.0,
        "max_stop_atr_multiplier": 20.0,
        "allow_limit_order": True,
        "allow_market_order": True,
    }
    config.update(overrides)
    return config


def test_valid_bullish_fvg_limit_entry() -> None:
    result = generate_entry_signal(
        _confirmed_setup(),
        _base_rows(),
        _risk(entry_mode="aggressive"),
    )

    assert result["entry_signal"] is True
    assert result["direction"] == "bullish"
    assert result["entry_type"] == EntryType.FVG_LIMIT_MIDPOINT.value
    assert result["order_type"] == "limit_order"
    assert result["entry_price"] == 101.0
    assert result["stop_loss"] < result["entry_price"]
    assert result["target"] == 110.0
    assert result["rr"] >= 2.0
    assert result["confidence_score"] >= 8.0


def test_valid_bearish_ob_confirmation_entry() -> None:
    setup = _confirmed_setup(
        setup_id="NY_RAID_BEAR_001",
        direction="bearish",
        setup_score=7.6,
        fvg_zones=[],
        order_blocks=[
            {
                "order_block_id": "OB_BEAR_001",
                "zone_type": "bearish_order_block",
                "direction": "bearish",
                "zone_low": 104.0,
                "zone_high": 106.0,
                "zone_mid": 105.0,
                "quality_score": 7.8,
                "fresh_status": "fresh",
                "retest_status": "confirmed_reaction",
                "created_after_mss": True,
                "created_by_displacement": True,
            }
        ],
        target_liquidity={"liquidity_id": "SSL_001", "target_price": 95.0},
        sweep_extreme=108.0,
        invalidation_level=107.5,
    )
    rows = _base_rows(_row(11, 105.4, 105.8, 102.0, 103.0))

    result = generate_entry_signal(setup, rows, _risk(entry_mode="conservative"))

    assert result["entry_signal"] is True
    assert result["direction"] == "bearish"
    assert result["entry_type"] == EntryType.OB_CONFIRMATION.value
    assert result["order_type"] == "market_order_after_closed_candle_confirmation"
    assert result["stop_loss"] > result["entry_price"]
    assert result["target"] == 95.0
    assert result["rr"] >= 1.5


def test_confirmed_setup_without_retest_does_not_chase_displacement() -> None:
    result = generate_entry_signal(
        _confirmed_setup(setup_score=7.4),
        _base_rows(_row(11, 106.5, 108.0, 106.0, 107.5)),
        _risk(entry_mode="conservative"),
    )

    assert result["entry_signal"] is False
    assert result["status"] == EntryStatus.WAITING_FOR_RETEST.value
    assert result["selected_zone"]["zone_type"] == "bullish_fvg"


def test_good_zone_reaction_but_poor_rr_is_blocked() -> None:
    setup = _confirmed_setup(
        setup_score=7.9,
        target_liquidity={"liquidity_id": "NEAR_BSL", "target_price": 103.0},
        sweep_extreme=97.0,
    )
    rows = _base_rows(_row(11, 100.5, 102.4, 99.8, 102.1))

    result = generate_entry_signal(setup, rows, _risk(entry_mode="conservative", min_rr=1.5))

    assert result["entry_signal"] is False
    assert result["status"] == EntryStatus.POOR_RR.value
    assert result["rr"] < 1.5
    assert result["position_allowed"] is False


def test_htf_zone_with_ltf_confirmation_improves_precision() -> None:
    setup = _confirmed_setup(
        setup_id="HTF_LTF_BULL_001",
        setup_score=8.8,
        ltf_confirmation={
            "confirmed": True,
            "direction": "bullish",
            "mss_confirmed": True,
            "ltf_sweep_low": 100.2,
        },
        sweep_extreme=100.2,
        invalidation_level=100.2,
    )
    rows = _base_rows(_row(11, 100.7, 102.9, 100.2, 102.6))

    result = generate_entry_signal(
        setup,
        rows,
        _risk(entry_mode="conservative", use_ltf_confirmation=True, min_rr=1.5),
    )

    assert result["entry_signal"] is True
    assert result["direction"] == "bullish"
    assert result["entry_type"] in {
        EntryType.FVG_CONFIRMATION.value,
        EntryType.LTF_CONFIRMATION_FVG.value,
    }
    assert result["stop_loss"] <= 100.2
    assert result["target"] == 110.0
    assert result["confidence_score"] >= 8.0
