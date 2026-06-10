from stockml.trading.profit_taking import ProfitTakingRules, classify_profit_taking


def test_stale_signal_uses_configured_trailing_thresholds():
    result = classify_profit_taking(
        current_plpc=0.016,
        peak_plpc=0.022,
        decision="watch",
        decision_reason="signal_stale",
        rules=ProfitTakingRules.from_percentages(2.0, 0.5),
    )

    assert result["close_triggered"] is True
    assert result["close_trigger_reason"] == "trailing_profit_giveback"


def test_fresh_signal_winner_gets_more_room_than_stale_signal():
    rules = ProfitTakingRules.from_percentages(2.0, 1.0)
    stale = classify_profit_taking(
        current_plpc=0.031,
        peak_plpc=0.045,
        decision="watch",
        decision_reason="signal_stale",
        rules=rules,
    )
    fresh = classify_profit_taking(
        current_plpc=0.031,
        peak_plpc=0.045,
        decision="hold",
        decision_reason="position_within_rules",
        rules=rules,
    )

    assert stale["close_triggered"] is True
    assert fresh["close_triggered"] is False
    assert fresh["management_state"] == "protect_profit"


def test_fresh_signal_large_giveback_still_closes():
    result = classify_profit_taking(
        current_plpc=0.018,
        peak_plpc=0.045,
        decision="hold",
        decision_reason="position_within_rules",
        rules=ProfitTakingRules.from_percentages(2.0, 1.0),
    )

    assert result["close_triggered"] is True
    assert result["close_trigger_reason"] == "fresh_signal_profit_giveback"
