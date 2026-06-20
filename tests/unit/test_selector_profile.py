from src.core.domain.constants import OrderDirection
from scripts.run_ict_smc_backtest import _target_ladder
from src.strategy.selector_profile import load_selector_profile, normalize_selector_profile


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


def test_v4_candidate_safety_2r_exists_without_changing_v3() -> None:
    v3 = load_selector_profile("v3_candidate_safety")
    v4 = load_selector_profile("v4_candidate_safety_2r")

    assert v3["minimum_rr"] == 3.0
    assert v3["strategy_min_rr"]["sweep_mss_fvg"] == 3.0
    assert v3["target_ladder"]["final_rr"] == 3.0
    assert v4["minimum_rr"] == 2.0
    assert v4["strategy_min_rr"]["sweep_mss_fvg"] == 2.0
    assert v4["target_ladder"]["final_rr"] == 2.0
    assert v4["target_ladder"]["milestones"] == [1, 1.5, 2]
    assert v4["target_ladder"]["close_percents"] == [0.30, 0.30, 0.40]
    assert v4["enabled_strategies"] == ["sweep_mss_fvg"]
    assert v4["session_filters"]["disabled_killzones"] == ["London Open"]


def test_v4_normalizer_and_target_ladder_use_two_r() -> None:
    profile = load_selector_profile("v4_candidate_safety_2r")
    config = normalize_selector_profile(profile)

    targets = _target_ladder(_Setup(), _Selected(), {"target_ladder": config["target_ladder"]})

    assert config["minimum_rr"] == 2.0
    assert config["min_rr"] == 2.0
    assert config["minimum_setup_score"] == 7.0
    assert targets[-1]["rr"] == 2.0
    assert targets[-1]["price"] == 104.0


class _Setup:
    direction = OrderDirection.BUY
    entry_price = 100.0
    stop_loss = 98.0


class _Selected:
    estimated_rr = 2.0
