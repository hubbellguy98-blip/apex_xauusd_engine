from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.multitimeframe_engine import (
    get_closed_candles_asof,
    run_multitimeframe_engine,
)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _candle(start: str, minutes: int, open_: float, high: float, low: float, close: float, *, is_closed: bool = True):
    opened = _dt(start)
    return {
        "timestamp": opened,
        "close_time": opened + timedelta(minutes=minutes),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000,
        "is_closed": is_closed,
    }


def _timeframes(*, include_active_h1: bool = False):
    h1 = [
        _candle("2026-06-04T08:00:00", 60, 2350, 2360, 2348, 2358),
        _candle("2026-06-04T09:00:00", 60, 2358, 2370, 2355, 2368),
    ]
    if include_active_h1:
        h1.append(_candle("2026-06-04T10:00:00", 60, 2368, 2385, 2367, 2380, is_closed=False))
    return {
        "daily": [
            _candle("2026-06-02T00:00:00", 1440, 2360, 2382, 2352, 2371),
            _candle("2026-06-03T00:00:00", 1440, 2371, 2381, 2353, 2366),
        ],
        "h1": h1,
        "m15": [
            _candle("2026-06-04T10:00:00", 15, 2365, 2366, 2354, 2361),
            _candle("2026-06-04T10:15:00", 15, 2361, 2367, 2358, 2365),
        ],
        "m5": [
            _candle("2026-06-04T10:20:00", 5, 2364, 2365, 2359.7, 2360.3),
            _candle("2026-06-04T10:25:00", 5, 2360.3, 2363, 2359.8, 2362.5),
            _candle("2026-06-04T10:30:00", 5, 2362.5, 2366, 2361.5, 2365),
        ],
    }


def _bullish_config(**overrides):
    config = {
        "symbol": "XAUUSD",
        "require_ltf_confirmation": True,
        "daily_context": {
            "bias": "neutral",
            "pdh": {"price": 2381.4, "direction": "buy_side", "swept_status": "unswept"},
            "pdl": {"price": 2352.1, "direction": "sell_side", "swept_status": "unswept"},
            "poi_zones": [],
        },
        "h1_context": {
            "h1_bias": "bullish",
            "expected_draw": "buy_side",
            "latest_bos": {
                "direction": "bullish",
                "confirmed_by_close": True,
                "confirmation_time": "2026-06-04T10:00:00+00:00",
                "source_timeframe": "1H",
            },
            "poi_zones": [
                {
                    "zone_id": "H1_BULLISH_FVG_004",
                    "source_timeframe": "1H",
                    "zone_type": "bullish_fvg",
                    "direction": "bullish",
                    "zone_low": 2358.4,
                    "zone_high": 2361.2,
                    "valid_from_time": "2026-06-04T10:00:00+00:00",
                    "active_status": True,
                }
            ],
        },
        "m15_setup": {
            "setup_detected": True,
            "confirmed": True,
            "setup_type": "sell_side_liquidity_sweep_bullish_mss",
            "direction": "bullish",
            "setup_valid_from_time": "2026-06-04T10:30:00+00:00",
            "poi_zones": [
                {
                    "zone_id": "M15_BULLISH_FVG_009",
                    "source_timeframe": "15M",
                    "zone_type": "bullish_fvg",
                    "direction": "bullish",
                    "zone_low": 2359.8,
                    "zone_high": 2361.6,
                    "valid_from_time": "2026-06-04T10:30:00+00:00",
                    "active_status": True,
                }
            ],
            "target_liquidity": {"blocked": False, "closer_target_meets_rr": True},
        },
        "m5_confirmation": {
            "confirmed": True,
            "direction": "bullish",
            "sell_side_sweep_inside_poi": True,
            "mss_confirmed": True,
            "displacement_confirmed": True,
            "entry_zone": {
                "zone_id": "M5_BULLISH_FVG_021",
                "zone_low": 2360.2,
                "zone_high": 2361.0,
            },
            "entry_valid_from_time": "2026-06-04T10:35:00+00:00",
        },
        "score_result": {"trade_allowed": True, "total_score": 8.6, "grade": "A"},
    }
    config.update(overrides)
    return config


def test_closed_candle_slicing_excludes_active_h1_candle() -> None:
    h1_closed = get_closed_candles_asof(_timeframes(include_active_h1=True)["h1"], "1H", _dt("2026-06-04T10:35:00"))

    assert len(h1_closed) == 2
    assert h1_closed[-1]["close_time"] == "2026-06-04T10:00:00+00:00"


def test_perfect_multitimeframe_bullish_alignment_allows_trade() -> None:
    result = run_multitimeframe_engine(
        _timeframes(),
        _dt("2026-06-04T10:35:00"),
        _bullish_config(),
    )

    assert result["trade_allowed"] is True
    assert result["decision"] == "trade_allowed"
    context = result["multi_timeframe_context"]
    assert context["lookahead_safe"] is True
    assert context["combined_bias"]["alignment_status"] == "bullish"
    assert context["trade_readiness"]["setup_ready"] is True
    assert context["trade_readiness"]["entry_confirmation_ready"] is True


def test_5m_entry_without_15m_setup_is_blocked() -> None:
    config = _bullish_config(m15_setup={"setup_detected": False, "confirmed": False, "direction": "none"})

    result = run_multitimeframe_engine(_timeframes(), _dt("2026-06-04T10:35:00"), config)

    assert result["trade_allowed"] is False
    assert result["trade_decision"]["reason"] == "no_15m_setup"
    assert result["multi_timeframe_context"]["trade_readiness"]["setup_ready"] is False


def test_bullish_setup_into_daily_bearish_blocker_is_blocked_without_rr() -> None:
    config = _bullish_config()
    config["daily_context"] = {
        "bias": "neutral",
        "poi_zones": [
            {
                "zone_id": "D1_BEARISH_OB_001",
                "source_timeframe": "1D",
                "zone_type": "bearish_order_block",
                "direction": "bearish",
                "zone_low": 2370.0,
                "zone_high": 2380.0,
                "valid_from_time": "2026-06-03T00:00:00+00:00",
                "active_status": True,
            }
        ],
    }
    config["m15_setup"]["target_liquidity"] = {
        "blocked": True,
        "blocked_by_daily_poi": True,
        "closer_target_meets_rr": False,
    }

    result = run_multitimeframe_engine(_timeframes(), _dt("2026-06-04T10:35:00"), config)

    assert result["trade_allowed"] is False
    assert result["trade_decision"]["reason"] == "target_blocked_by_htf_poi"


def test_unclosed_h1_context_time_blocks_lookahead_bias() -> None:
    config = _bullish_config()
    config["h1_context"]["latest_bos"]["confirmation_time"] = "2026-06-04T11:00:00+00:00"

    result = run_multitimeframe_engine(
        _timeframes(include_active_h1=True),
        _dt("2026-06-04T10:35:00"),
        config,
    )

    assert result["trade_allowed"] is False
    assert result["decision"] == "lookahead_blocked"
    assert result["multi_timeframe_context"]["lookahead_safe"] is False
    assert "unclosed_htf_candle_used" in result["warnings"]


def test_valid_htf_poi_waits_for_5m_confirmation() -> None:
    config = _bullish_config(
        m5_confirmation={
            "confirmed": False,
            "direction": "bullish",
            "sell_side_sweep_inside_poi": False,
            "mss_confirmed": False,
            "displacement_confirmed": False,
            "entry_zone": None,
        }
    )

    result = run_multitimeframe_engine(_timeframes(), _dt("2026-06-04T10:35:00"), config)

    assert result["trade_allowed"] is False
    assert result["trade_decision"]["reason"] == "waiting_for_5m_confirmation"
    readiness = result["multi_timeframe_context"]["trade_readiness"]
    assert readiness["setup_ready"] is True
    assert readiness["entry_confirmation_ready"] is False
