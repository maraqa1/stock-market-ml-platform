import pandas as pd

from stockml.diagnostics.position_gate_degradation import classify_position


def test_agl_like_position_triggers_urgent_close_review():
    row = pd.Series({
        "symbol": "AGL",
        "unrealized_plpc": -0.1061,
        "trade_quality_status": "rejected",
        "trade_quality_reason": "expected_trade_return_below_threshold|risk_adjusted_score_below_threshold|risk_gate_failed",
    })
    result = classify_position(row)
    assert result["suggested_position_action"] == "urgent_close_review"
    assert result["diagnostics_only"] is True


def test_bny_like_approved_losing_position_triggers_close_candidate():
    row = pd.Series({"symbol": "BNY", "unrealized_plpc": -0.0362, "trade_quality_status": "approved", "trade_quality_reason": "approved", "risk_tier": "high_quality", "candidate_rank": 22, "validated_expected_return_bps": 41})
    result = classify_position(row)
    assert result["suggested_position_action"] == "close_candidate"
    assert result["primary_reason"] == "approved_position_breached_loss_threshold"


def test_rejected_losing_position_triggers_close_candidate():
    row = pd.Series({"symbol": "PENG", "unrealized_plpc": -0.025, "trade_quality_status": "rejected", "trade_quality_reason": "risk_gate_failed"})
    result = classify_position(row)
    assert result["suggested_position_action"] == "close_candidate"


def test_missing_quality_fields_trigger_manual_review():
    row = pd.Series({"symbol": "GEO", "unrealized_plpc": -0.006})
    result = classify_position(row)
    assert result["suggested_position_action"] == "manual_review"
    assert result["primary_reason"] == "missing_position_quality_evidence"


def test_source_trade_action_not_executable_losing_triggers_review_not_solo_forced_close():
    row = pd.Series({"symbol": "EFOR", "unrealized_plpc": -0.01, "trade_quality_status": "rejected", "trade_quality_reason": "source_trade_action_not_executable", "risk_tier": "medium", "candidate_rank": 81, "validated_expected_return_bps": -30})
    result = classify_position(row)
    assert result["position_management_trigger"] is True
    assert result["suggested_position_action"] == "reduce_candidate"


def test_all_positions_red_supports_block_new_entries():
    row = pd.Series({"symbol": "AAA", "unrealized_plpc": -0.01, "trade_quality_status": "approved", "risk_tier": "medium", "candidate_rank": 1, "validated_expected_return_bps": 10, "trade_quality_reason": ""})
    result = classify_position(row, portfolio_warning=True)
    assert result["should_block_new_entries"] is True
