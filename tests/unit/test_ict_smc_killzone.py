from datetime import datetime, timezone

from src.analytics.ict_smc.killzone import (
    filter_setups_by_killzone,
    is_in_killzone,
)


def _config(filter_mode="score_modifier"):
    return {
        "timestamp_timezone": "UTC",
        "strategy_timezone": "America/New_York",
        "filter_mode": filter_mode,
        "inside_killzone_bonus": 1.0,
        "outside_killzone_penalty": 1.0,
        "allowed_days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        "killzones": [
            {
                "name": "london_killzone",
                "session": "london",
                "start_time": "02:00",
                "end_time": "05:00",
                "enabled": True,
                "priority_weight": 1.0,
            },
            {
                "name": "new_york_am_killzone",
                "session": "new_york",
                "start_time": "07:00",
                "end_time": "10:30",
                "enabled": True,
                "priority_weight": 1.2,
            },
            {
                "name": "london_new_york_overlap",
                "session": "overlap",
                "start_time": "08:00",
                "end_time": "11:00",
                "enabled": True,
                "priority_weight": 1.3,
            },
        ],
    }


def test_timestamp_inside_london_killzone_after_timezone_conversion() -> None:
    result = is_in_killzone(datetime(2026, 6, 4, 7, 30, tzinfo=timezone.utc), _config())

    assert result["in_killzone"] is True
    assert result["primary_killzone"] == "london_killzone"
    assert result["killzone_name"] == "london_killzone"
    assert result["session_name"] == "london"
    assert result["time_filter_passed"] is True
    assert result["timezone_used"] == "America/New_York"
    assert "03:30:00" in result["converted_timestamp"]


def test_new_york_setup_gets_weighted_score_boost() -> None:
    setup = {
        "setup_id": "SB_NY_001",
        "setup_type": "silver_bullet",
        "direction": "bullish",
        "sweep_timestamp": datetime(2026, 6, 4, 14, 15, tzinfo=timezone.utc),
        "base_score": 7.4,
        "valid_setup": True,
        "liquidity_sweep": True,
        "mss_confirmed": True,
        "displacement_confirmed": True,
    }

    result = filter_setups_by_killzone([setup], _config())
    enriched = result["filtered_setups"][0]

    assert result["summary"]["accepted"] == 1
    assert enriched["in_killzone"] is True
    assert enriched["killzone_name"] == "london_new_york_overlap"
    assert enriched["killzone_score_adjustment"] == 1.3
    assert enriched["final_score"] == 8.7
    assert enriched["time_filter_passed"] is True
    assert enriched["valid_trade"] is True


def test_fvg_inside_killzone_without_price_action_does_not_become_valid_trade() -> None:
    setup = {
        "setup_id": "FVG_ONLY_001",
        "setup_type": "fvg_retest",
        "direction": "bearish",
        "fvg_creation_timestamp": datetime(2026, 6, 4, 7, 45, tzinfo=timezone.utc),
        "base_score": 4.2,
        "valid_setup": False,
    }

    result = filter_setups_by_killzone([setup], _config())
    enriched = result["filtered_setups"][0]

    assert enriched["in_killzone"] is True
    assert enriched["killzone_name"] == "london_killzone"
    assert enriched["valid_trade"] is False
    assert enriched["entry_allowed_from_killzone_alone"] is False
    assert enriched["killzone_is_signal"] is False
    assert "killzone_alone_not_enough" in enriched["warnings"]


def test_strict_mode_rejects_good_setup_outside_killzone() -> None:
    setup = {
        "setup_id": "SB_OUTSIDE_001",
        "setup_type": "silver_bullet",
        "direction": "bullish",
        "sweep_timestamp": datetime(2026, 6, 4, 20, 30, tzinfo=timezone.utc),
        "base_score": 8.1,
        "valid_setup": True,
        "liquidity_sweep": True,
        "mss_confirmed": True,
        "displacement_confirmed": True,
    }

    result = filter_setups_by_killzone([setup], _config("strict"))
    rejected = result["rejected_setups"][0]

    assert result["summary"]["accepted"] == 0
    assert result["summary"]["rejected"] == 1
    assert rejected["in_killzone"] is False
    assert rejected["time_filter_passed"] is False
    assert rejected["rejection_reason"] == "outside_configured_killzone"


def test_unknown_timestamp_timezone_adds_warning_and_strict_rejects() -> None:
    config = _config("strict")
    config.pop("timestamp_timezone")
    setup = {
        "setup_id": "UNKNOWN_TZ_001",
        "setup_type": "silver_bullet",
        "direction": "bearish",
        "sweep_timestamp": datetime(2026, 6, 4, 20, 30),
        "base_score": 7.9,
        "valid_setup": True,
        "liquidity_sweep": True,
        "mss_confirmed": True,
    }

    result = filter_setups_by_killzone([setup], config)
    rejected = result["rejected_setups"][0]

    assert result["summary"]["rejected"] == 1
    assert rejected["in_killzone"] is False
    assert "timestamp_timezone_unknown_assumed_UTC" in rejected["warnings"]
    assert rejected["rejection_reason"] == "outside_configured_killzone"
