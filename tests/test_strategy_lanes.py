from stockml.strategy.strategy_lanes import MAIN_STRATEGY_POLICY, assign_lane, get_lane


def test_main_lane_is_long_only_executable_validation():
    lane = get_lane("nightly_swing_long")
    assert lane.executable is True
    assert lane.allowed_sides == "Long"
    assert lane.requires_calibrated_expected_return is True


def test_short_lane_is_research_only():
    lane = get_lane("short_research")
    assert lane.executable is False
    assert lane.default_mode == "research_only"


def test_main_strategy_policy_disables_shorts_and_raw_experiment():
    assert MAIN_STRATEGY_POLICY["allow_shorts"] is False
    assert "raw_candidate_experiment" in MAIN_STRATEGY_POLICY["disabled_lanes"]


def test_assign_lane_rejected_candidate_goes_to_diagnostics():
    assert assign_lane({"trade_quality_status": "rejected", "trade_action": "Long"}) == "rejected_diagnostics"
    assert assign_lane({"trade_action": "Short"}) == "short_research"
