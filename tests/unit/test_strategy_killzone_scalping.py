from src.strategy.ict_smc_strategies.killzone_scalping import (
    detect_killzone_fvg_or_ob,
    detect_killzone_liquidity_sweep,
    detect_killzone_mss,
    enforce_session_trade_limit,
    generate_killzone_scalp_signal,
    is_in_killzone,
    score_killzone_scalp_setup,
)


def _c(index, open_, high, low, close, minute=None, volume=1000, closed=True):
    minute = index if minute is None else minute
    return {
        "index": index,
        "timestamp": f"2026-06-04T08:{minute:02d}:00Z",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "is_closed": closed,
    }


def _base_config(**overrides):
    data = {
        "killzones": [
            {
                "name": "London Kill Zone",
                "start_time": "08:00",
                "end_time": "09:30",
                "timezone": "UTC",
            },
            {
                "name": "New York Kill Zone",
                "start_time": "13:30",
                "end_time": "16:00",
                "timezone": "UTC",
            },
        ],
        "min_total_candles": 8,
        "structure_lookback": 4,
        "sweep_buffer": 0.03,
        "mss_break_buffer": 0.03,
        "max_mss_wait_candles": 4,
        "min_body_to_range": 0.50,
        "displacement_min_range_to_atr": 0.45,
        "min_fvg_size": 0.05,
        "max_poi_width": 3.0,
        "max_entry_wait_candles": 4,
        "max_retracement_wait_candles": 4,
        "minimum_target_distance": 0.50,
        "minimum_target_distance_for_1m": 0.80,
        "min_rr": 1.4,
        "minimum_setup_score": 7.0,
        "max_spread": 0.35,
        "max_spread_to_target_ratio": 0.25,
        "max_candle_range": 8.0,
        "max_trades_per_killzone": 1,
    }
    data.update(overrides)
    return data


def _bullish_context(**overrides):
    candles = [
        _c(0, 99.4, 100.1, 99.0, 99.7),
        _c(1, 99.7, 100.3, 99.2, 100.0),
        _c(2, 100.0, 100.5, 99.4, 100.1),
        _c(3, 100.1, 100.2, 99.3, 99.7),
        _c(4, 99.7, 100.0, 98.4, 99.4),
        _c(5, 99.4, 101.6, 99.8, 101.3),
        _c(6, 101.3, 102.0, 100.4, 101.7),
        _c(7, 101.7, 101.9, 100.2, 100.8),
        _c(8, 100.8, 101.2, 100.5, 101.0),
        _c(9, 101.0, 106.0, 97.0, 105.0, closed=False),
    ]
    context = {
        "symbol": "XAUUSD",
        "timestamp": "2026-06-04T08:08:00Z",
        "candles": candles,
        "liquidity_pools": [
            {"id": "ASIAN_LOW", "side": "sell_side", "price": 99.0, "zone_low": 99.0, "zone_high": 99.05},
            {"id": "NEAREST_BUY_SIDE", "side": "buy_side", "price": 104.0},
        ],
        "timeframe": "5m",
        "mtf_confirmation": {"mss_confirmed": True},
        "spread_status": {"spread_points": 0.05},
        "news_status": {"restricted": False},
        "session_state": {"trades_by_session": {"London Kill Zone": 0}},
    }
    context.update(overrides)
    return context


def _bearish_context(**overrides):
    candles = [
        _c(0, 100.0, 100.6, 99.2, 99.8),
        _c(1, 99.8, 100.8, 99.4, 100.2),
        _c(2, 100.2, 100.7, 99.2, 100.4),
        _c(3, 100.4, 100.5, 99.5, 100.1),
        _c(4, 100.1, 101.5, 100.1, 100.7),
        _c(5, 100.7, 100.0, 98.4, 98.7),
        _c(6, 98.7, 99.5, 98.0, 98.6),
        _c(7, 98.6, 99.9, 98.2, 99.2),
        _c(8, 99.2, 99.4, 98.8, 99.0),
        _c(9, 99.0, 103.0, 95.0, 96.0, closed=False),
    ]
    context = {
        "symbol": "XAUUSD",
        "timestamp": "2026-06-04T08:08:00Z",
        "candles": candles,
        "liquidity_pools": [
            {"id": "ASIAN_HIGH", "side": "buy_side", "price": 101.0, "zone_low": 100.95, "zone_high": 101.0},
            {"id": "NEAREST_SELL_SIDE", "side": "sell_side", "price": 96.5},
        ],
        "timeframe": "5m",
        "mtf_confirmation": {"mss_confirmed": True},
        "spread_status": {"spread_points": 0.05},
        "news_status": {"restricted": False},
        "session_state": {"trades_by_session": {"London Kill Zone": 0}},
    }
    context.update(overrides)
    return context


def test_valid_bullish_killzone_scalp_signal():
    signal = generate_killzone_scalp_signal(_bullish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["trade_allowed"] is True
    assert signal["direction"] == "bullish"
    assert signal["killzone"]["active_killzone_name"] == "London Kill Zone"
    assert signal["sweep"]["swept_side"] == "sell_side"
    assert signal["mss"]["mss_confirmed"] is True
    assert signal["entry_poi"]["poi_type"] == "fvg"
    assert signal["entry_poi"]["retest_status"] == "retested"
    assert signal["target"]["target_id"] == "NEAREST_BUY_SIDE"
    assert signal["risk"]["rr"] >= 1.4


def test_valid_bearish_killzone_scalp_signal():
    signal = generate_killzone_scalp_signal(_bearish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["trade_allowed"] is True
    assert signal["direction"] == "bearish"
    assert signal["sweep"]["swept_side"] == "buy_side"
    assert signal["mss"]["mss_confirmed"] is True
    assert signal["entry_poi"]["retest_status"] == "retested"
    assert signal["target"]["target_id"] == "NEAREST_SELL_SIDE"


def test_good_setup_outside_killzone_is_rejected():
    context = _bullish_context(timestamp="2026-06-04T11:00:00Z")

    signal = generate_killzone_scalp_signal(context, _base_config())

    assert signal["signal_status"] == "outside_killzone"
    assert signal["trade_allowed"] is False
    assert "outside_killzone" in signal["rejection_reasons"]


def test_low_quality_1m_fvg_is_rejected_without_mtf_and_target_room():
    context = _bullish_context(
        timeframe="1m",
        mtf_confirmation={"mss_confirmed": False},
        liquidity_pools=[
            {"id": "ASIAN_LOW", "side": "sell_side", "price": 99.0, "zone_low": 99.0, "zone_high": 99.05},
            {"id": "TOO_CLOSE_BUY_SIDE", "side": "buy_side", "price": 101.35},
        ],
        spread_status={"spread_points": 0.20},
    )

    signal = generate_killzone_scalp_signal(context, _base_config())

    assert signal["signal_status"] == "rejected"
    assert signal["trade_allowed"] is False
    assert "low_quality_1m_setup" in signal["rejection_reasons"]
    assert "no_5m_mss_confirmation" in signal["rejection_reasons"]
    assert "target_distance_too_small" in signal["rejection_reasons"]
    assert "spread_too_large_relative_to_target" in signal["rejection_reasons"]


def test_news_spike_during_ny_killzone_is_rejected():
    candles = []
    for row in _bullish_context()["candles"]:
        updated = dict(row)
        updated["timestamp"] = updated["timestamp"].replace("T08:", "T13:")
        candles.append(updated)
    candles[4] = {
        **candles[4],
        "high": 106.5,
        "low": 94.0,
        "close": 99.4,
    }
    context = _bullish_context(
        timestamp="2026-06-04T13:38:00Z",
        candles=candles,
        news_status={"restricted": True, "first_news_spike_signal": True},
        spread_status={"spread_points": 0.80},
        session_state={"trades_by_session": {"New York Kill Zone": 0}},
    )

    signal = generate_killzone_scalp_signal(context, _base_config())

    assert signal["signal_status"] == "rejected"
    assert "news_restricted" in signal["rejection_reasons"]
    assert "first_news_spike_signal" in signal["rejection_reasons"]
    assert "spread_too_high" in signal["rejection_reasons"]
    assert "max_candle_size_exceeded" in signal["rejection_reasons"]


def test_required_detectors_are_usable_independently():
    context = _bullish_context()
    config = _base_config()
    killzone = is_in_killzone(context["timestamp"], config["killzones"])
    sweep = detect_killzone_liquidity_sweep(
        context["candles"],
        context["liquidity_pools"],
        killzone["active_killzone"],
        config,
    )
    mss = detect_killzone_mss(context["candles"], sweep, None, config)
    entry = detect_killzone_fvg_or_ob(context["candles"], mss, config)
    limit = enforce_session_trade_limit(context["session_state"], killzone, config)
    score = score_killzone_scalp_setup(
        {
            "killzone": killzone,
            "sweep": sweep,
            "mss": mss,
            "entry_poi": entry,
            "risk": {"rr": 2.0},
            "rejection_reasons": [],
        },
        context,
        config,
    )

    assert killzone["in_killzone"] is True
    assert sweep["sweep_detected"] is True
    assert mss["mss_confirmed"] is True
    assert entry["entry_poi_detected"] is True
    assert limit["trade_limit_ok"] is True
    assert score["trade_allowed"] is True
