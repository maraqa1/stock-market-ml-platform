from __future__ import annotations

from stockml.trading.position_intelligence import enrich_positions, explain_position


def test_unknown_signal_winner_with_giveback_is_close_triggered():
    result = explain_position(
        {"symbol": "CAI", "unrealized_plpc": 0.00231},
        decision={"symbol": "CAI", "decision": "watch", "decision_reason": "latest_signal_unknown"},
        peak_plpc=0.03498,
    )

    assert result["management_state"] == "close_triggered"
    assert result["close_trigger_reason"] == "trailing_profit_giveback"
    assert result["trailing_active"] is True
    assert result["signal_state"] == "unknown"


def test_winner_near_peak_is_protected_but_not_closed():
    result = explain_position(
        {"symbol": "CHTR", "unrealized_plpc": 0.02977},
        decision={"symbol": "CHTR", "decision": "watch", "decision_reason": "latest_signal_unknown"},
        peak_plpc=0.03369,
    )

    assert result["management_state"] == "protect_profit"
    assert result["close_triggered"] is False
    assert result["distance_to_trailing_close"] > 0


def test_fresh_signal_winner_uses_wider_trailing_line():
    result = explain_position(
        {"symbol": "FRESH", "unrealized_plpc": 0.031},
        decision={"symbol": "FRESH", "decision": "hold", "decision_reason": "position_within_rules"},
        peak_plpc=0.045,
    )

    assert result["management_state"] == "protect_profit"
    assert result["close_triggered"] is False
    assert result["fresh_trailing_active"] is True
    assert result["distance_to_fresh_trailing_close"] > 0


def test_loser_above_defensive_line_is_watch_loss():
    result = explain_position(
        {"symbol": "CRVS", "unrealized_plpc": -0.01692},
        decision={"symbol": "CRVS", "decision": "watch", "decision_reason": "latest_signal_unknown"},
        peak_plpc=0.00924,
    )

    assert result["management_state"] == "watch_loss"
    assert result["close_triggered"] is False
    assert result["distance_to_defensive_close"] > 0


def test_loser_below_unknown_signal_line_is_close_triggered():
    result = explain_position(
        {"symbol": "CRVS", "unrealized_plpc": -0.021},
        decision={"symbol": "CRVS", "decision": "watch", "decision_reason": "latest_signal_unknown"},
        peak_plpc=0.00924,
    )

    assert result["management_state"] == "close_triggered"
    assert result["close_trigger_reason"] == "defensive_unknown_loss"


def test_enrich_positions_adds_nested_and_flat_fields():
    rows = enrich_positions(
        [{"symbol": "CAI", "unrealized_plpc": 0.00231}],
        decisions=[{"symbol": "CAI", "decision": "watch", "decision_reason": "latest_signal_unknown"}],
        autopilot_state={"position_peak_plpc": {"CAI": 0.03498}},
    )

    assert rows[0]["position_intelligence"]["close_trigger_reason"] == "trailing_profit_giveback"
    assert rows[0]["position_intelligence_management_state"] == "close_triggered"
