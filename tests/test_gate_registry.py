from stockml.strategy.gate_registry import get_gate


def test_risk_gate_failed_classified_as_safety_trigger():
    gate = get_gate("risk_gate_failed")
    assert gate.gate_class == "must_have_safety"
    assert gate.position_management_trigger is True
    assert gate.mandatory_for_new_entries is True


def test_source_trade_action_mandatory_for_new_entries_not_forced_close():
    gate = get_gate("source_trade_action_not_executable")
    assert gate.mandatory_for_new_entries is True
    assert gate.mandatory_for_current_positions is False
    assert gate.default_position_action == "review"


def test_price_volatility_market_cap_are_safety_gates():
    for name in ["price_below_minimum", "volatility_extreme", "market_cap_below_minimum"]:
        assert get_gate(name).gate_class == "must_have_safety"


def test_asset_not_overnight_tradable_is_critical_for_overnight():
    gate = get_gate("asset_not_overnight_tradable")
    assert gate.mandatory_for_overnight is True
    assert gate.severity == "critical_for_overnight"
