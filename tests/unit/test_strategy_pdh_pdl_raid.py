from src.strategy.ict_smc_strategies.pdh_pdl_raid import (
    calculate_previous_day_levels,
    detect_pdh_pdl_raid,
    detect_post_raid_fvg_or_ob,
    detect_post_raid_mss,
    detect_reclaim_or_rejection,
    generate_pdh_pdl_raid_signal,
    score_pdh_pdl_raid_setup,
)


def _c(index, open_, high, low, close, volume=1000, closed=True):
    return {
        "index": index,
        "timestamp": f"2026-06-04T00:{index:02d}:00Z",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "is_closed": closed,
    }


def _d(index, date, open_, high, low, close, closed=True):
    return {
        "index": index,
        "date": date,
        "timestamp": f"{date}T00:00:00Z",
        "session_start": f"{date}T00:00:00Z",
        "session_end": f"{date}T23:59:59Z",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "is_closed": closed,
    }


def _levels():
    return {
        "date": "2026-06-03",
        "pdh": 110.0,
        "pdl": 90.0,
        "pdh_price": 110.0,
        "pdl_price": 90.0,
        "previous_day_open": 100.0,
        "previous_day_close": 101.0,
        "valid_status": True,
        "rejection_reasons": [],
    }


def _base_config(**overrides):
    data = {
        "min_total_candles": 8,
        "raid_buffer": 0.05,
        "min_raid_depth": 0.10,
        "max_raid_atr_multiplier": 5.0,
        "reclaim_buffer": 0.05,
        "acceptance_buffer": 0.08,
        "max_reclaim_candles": 3,
        "max_mss_wait_candles": 6,
        "break_buffer": 0.05,
        "min_body_to_range": 0.50,
        "displacement_min_range_to_atr": 0.45,
        "max_poi_width": 5.0,
        "max_entry_wait_candles": 5,
        "stop_buffer": 0.10,
        "stop_atr_buffer": 0.04,
        "min_rr": 2.0,
        "minimum_target_distance": 0.50,
        "minimum_setup_score": 7.0,
    }
    data.update(overrides)
    return data


def _bullish_context():
    candles = [
        _c(0, 98.0, 99.0, 96.8, 97.5),
        _c(1, 97.5, 98.4, 96.4, 97.2),
        _c(2, 97.2, 98.0, 95.8, 96.7),
        _c(3, 96.7, 97.2, 94.5, 95.2),
        _c(4, 95.2, 92.4, 91.0, 91.8),
        _c(5, 91.8, 92.8, 88.8, 91.4),
        _c(6, 91.4, 96.2, 92.9, 95.4),
        _c(7, 95.4, 96.0, 92.5, 94.4),
        _c(8, 94.4, 95.0, 93.7, 94.6, closed=False),
    ]
    return {
        "symbol": "XAUUSD",
        "candles": candles,
        "previous_day_levels": _levels(),
        "raid_window": {"start_position": 0, "end_position": 8},
        "liquidity_pools": [{"id": "PDH_BUY_SIDE", "side": "buy_side", "price": 110.0}],
        "htf_bias": {"bias_direction": "bullish"},
        "news_status": {"restricted": False},
        "spread_status": {"spread_safe": True, "spread_points": 0.0},
    }


def _bearish_context():
    candles = [
        _c(0, 101.5, 103.0, 100.2, 102.4),
        _c(1, 102.4, 104.0, 101.0, 103.3),
        _c(2, 103.3, 105.0, 102.0, 104.1),
        _c(3, 104.1, 107.5, 103.0, 106.7),
        _c(4, 106.7, 109.0, 107.2, 108.2),
        _c(5, 108.2, 111.5, 107.8, 108.6),
        _c(6, 108.6, 106.7, 103.4, 104.1),
        _c(7, 104.1, 107.0, 103.9, 105.4),
        _c(8, 105.4, 106.0, 104.5, 105.0, closed=False),
    ]
    return {
        "symbol": "XAUUSD",
        "candles": candles,
        "previous_day_levels": _levels(),
        "raid_window": {"start_position": 0, "end_position": 8},
        "liquidity_pools": [{"id": "PDL_SELL_SIDE", "side": "sell_side", "price": 90.0}],
        "htf_bias": {"bias_direction": "bearish"},
        "news_status": {"restricted": False},
        "spread_status": {"spread_safe": True, "spread_points": 0.0},
    }


def test_calculate_previous_day_levels_uses_previous_completed_day_only():
    daily = [
        _d(0, "2026-06-02", 99.0, 107.0, 94.0, 101.0),
        _d(1, "2026-06-03", 101.0, 110.0, 90.0, 100.5),
        _d(2, "2026-06-04", 100.5, 120.0, 80.0, 111.0, closed=False),
    ]

    levels = calculate_previous_day_levels(daily, "2026-06-04T12:00:00Z")

    assert levels["valid_status"] is True
    assert levels["pdh"] == 110.0
    assert levels["pdl"] == 90.0
    assert levels["date"] == "2026-06-03"


def test_valid_bullish_pdl_raid_signal():
    signal = generate_pdh_pdl_raid_signal(_bullish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["direction"] == "bullish"
    assert signal["trade_allowed"] is True
    assert signal["raid"]["raid_type"] == "pdl_raid"
    assert signal["reclaim_or_rejection"]["status"] == "reclaimed"
    assert signal["mss"]["mss_confirmed"] is True
    assert signal["entry_poi"]["retest_status"] == "retested"
    assert signal["score"]["total_score"] >= 8


def test_valid_bearish_pdh_raid_signal():
    signal = generate_pdh_pdl_raid_signal(_bearish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["direction"] == "bearish"
    assert signal["trade_allowed"] is True
    assert signal["raid"]["raid_type"] == "pdh_raid"
    assert signal["reclaim_or_rejection"]["status"] == "rejected"
    assert signal["mss"]["mss_confirmed"] is True
    assert signal["entry_poi"]["retest_status"] == "retested"
    assert signal["score"]["total_score"] >= 8


def test_pdh_breaks_and_continues_is_rejected_as_accepted_breakout():
    context = _bearish_context()
    context["candles"][5] = _c(5, 108.2, 112.2, 108.0, 111.2)
    context["candles"][6] = _c(6, 111.2, 113.0, 110.8, 112.4)
    context["candles"][7] = _c(7, 112.4, 115.0, 112.0, 114.3)

    signal = generate_pdh_pdl_raid_signal(context, _base_config())

    assert signal["signal_status"] == "rejected"
    assert "pdh_accepted_breakout_not_raid" in signal["rejection_reasons"]
    assert signal["trade_allowed"] is False


def test_pdl_raid_without_post_raid_mss_is_context_only():
    context = _bullish_context()
    context["candles"][6] = _c(6, 91.4, 92.7, 90.8, 91.9)
    context["candles"][7] = _c(7, 91.9, 92.8, 90.9, 91.7)

    signal = generate_pdh_pdl_raid_signal(context, _base_config())

    assert signal["signal_status"] == "context_only"
    assert "no_post_raid_mss" in signal["rejection_reasons"]
    assert "no_bullish_mss_after_pdl_raid" in signal["rejection_reasons"]
    assert signal["trade_allowed"] is False


def test_news_spike_that_sweeps_both_sides_is_rejected():
    context = _bullish_context()
    context["candles"][5] = _c(5, 100.0, 113.0, 87.0, 100.5)
    context["news_status"] = {"restricted": True}
    context["spread_status"] = {"status": "high", "spread_safe": False, "spread_points": 1.5}

    signal = generate_pdh_pdl_raid_signal(context, _base_config(max_raid_atr_multiplier=10.0))

    assert signal["signal_status"] == "rejected"
    assert "news_restricted" in signal["rejection_reasons"]
    assert "spread_too_high" in signal["rejection_reasons"]
    assert "double_sided_raid_no_clear_direction" in signal["rejection_reasons"]
    assert signal["trade_allowed"] is False


def test_required_detectors_are_usable_independently():
    context = _bullish_context()
    config = _base_config()
    raid = detect_pdh_pdl_raid(context["candles"], context["previous_day_levels"], context["raid_window"], config)
    reclaim = detect_reclaim_or_rejection(context["candles"], raid, config)
    mss = detect_post_raid_mss(context["candles"], None, raid, reclaim, config)
    entry_poi = detect_post_raid_fvg_or_ob(context["candles"], mss, mss.get("displacement"), config)
    setup = {
        "direction": "bullish",
        "previous_day_levels": context["previous_day_levels"],
        "raid": raid,
        "reclaim_or_rejection": reclaim,
        "mss": mss,
        "entry_poi": entry_poi,
        "risk": {"rr_to_final_target": 2.4},
        "rejection_reasons": [],
    }
    score = score_pdh_pdl_raid_setup(setup, context, config)

    assert raid["raid_detected"] is True
    assert reclaim["reclaim_or_rejection_confirmed"] is True
    assert mss["mss_confirmed"] is True
    assert entry_poi["entry_poi_detected"] is True
    assert score["trade_allowed"] is True
