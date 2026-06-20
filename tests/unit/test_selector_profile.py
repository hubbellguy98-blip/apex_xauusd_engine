from src.strategy.selector_profile import normalize_selector_profile


def test_profile_normalizer_maps_selector_and_generator_thresholds() -> None:
    profile = {
        "profile_name": "unit",
        "minimum_rr": 3.0,
        "minimum_score": 70.0,
        "enabled_strategies": ["sweep_mss_fvg"],
        "session_filters": {"disabled_killzones": ["London Open"]},
        "strict_displacement": True,
        "displacement_thresholds": {
            "body_to_range_ratio": 0.6,
            "range_to_atr_ratio": 1.2,
            "close_position_score": 0.75,
        },
        "early_trap_filter": {"enabled": True},
        "setup_timeframe": "1m",
        "entry_timeframe": "1m",
        "minimum_risk_to_cost_ratio": 4.0,
    }

    config = normalize_selector_profile(profile)

    assert config["minimum_rr"] == 3.0
    assert config["min_rr"] == 3.0
    assert config["minimum_score"] == 70.0
    assert config["minimum_setup_score"] == 7.0
    assert config["enabled_strategies"] == ["sweep_mss_fvg"]
    assert config["session_filters"]["disabled_killzones"] == ["London Open"]
    assert config["displacement_mode"] == "reject_weak_or_unverified"
    assert config["displacement_min_body_to_range"] == 0.6
    assert config["setup_timeframe"] == "1m"
    assert config["minimum_risk_to_cost_ratio"] == 4.0
