from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.candidates.execution_ranker import build_execution_ranked_candidates
from stockml.candidates.short_side_policy import ShortSidePolicy
from stockml.trading.direction_gate import DirectionGateConfig, ShortDirectionGateConfig, evaluate_direction_gate


def row(**overrides):
    data = {
        "symbol": "AAA",
        "side": "buy",
        "source_trade_action": "Long",
        "trade_action": "Long",
        "directional_action": "",
        "validated_expected_return_bps": 25.0,
        "validated_profit_factor": 1.25,
        "validated_hit_rate": 0.55,
        "expected_return_quality": "usable",
        "calibration_quality": "usable",
        "ticker_direction_bias": "trust_long",
        "ticker_direction_sample_count": 100,
        "ticker_direction_confidence": 0.75,
        "trade_quality_status": "approved",
        "approved_notional": 100.0,
        "suggested_quantity": 1,
    }
    data.update(overrides)
    return data


def test_source_no_decision_trade_long_is_research_only():
    result = evaluate_direction_gate(
        row(source_trade_action="No Decision", trade_action="Long"),
        config=DirectionGateConfig(allow_planner_derived_no_decision_execution=False),
    )
    assert result["direction_decision"] == "direction_research_only"
    assert result["direction_gate_pass"] is False
    assert result["direction_primary_reason"] == "planner_derived_action_without_source_approval"


def test_source_no_decision_trade_short_is_research_only():
    result = evaluate_direction_gate(
        row(side="sell", source_trade_action="No Decision", trade_action="Short"),
        config=DirectionGateConfig(allow_planner_derived_no_decision_execution=False),
    )
    assert result["direction_decision"] == "direction_research_only"
    assert result["direction_gate_pass"] is False


def test_directional_action_long_cannot_execute_without_source_approval():
    result = evaluate_direction_gate(row(source_trade_action="No Decision", trade_action="No Decision", directional_action="Long"))
    assert result["direction_decision"] == "direction_research_only"
    assert result["direction_primary_reason"] == "directional_action_research_only"


def test_directional_action_short_cannot_execute_without_source_approval():
    result = evaluate_direction_gate(row(side="sell", source_trade_action="No Decision", trade_action="No Decision", directional_action="Short"))
    assert result["direction_decision"] == "direction_research_only"
    assert result["direction_primary_reason"] == "directional_action_research_only"


def test_source_long_with_positive_validated_return_passes():
    result = evaluate_direction_gate(row())
    assert result["direction_decision"] == "direction_pass"
    assert result["direction_gate_pass"] is True


def test_meta_label_take_trade_passes_when_acceptance_required():
    result = evaluate_direction_gate(
        row(meta_label_decision="Take Trade"),
        config=DirectionGateConfig(require_meta_label_acceptance=True),
    )
    assert result["direction_decision"] == "direction_pass"
    assert result["direction_gate_pass"] is True


def test_meta_label_skip_trade_blocks_when_acceptance_required():
    result = evaluate_direction_gate(
        row(meta_label_decision="Skip Trade"),
        config=DirectionGateConfig(require_meta_label_acceptance=True),
    )
    assert result["direction_decision"] == "direction_block"
    assert result["direction_primary_reason"] == "meta_label_not_accepted"


def test_source_long_with_negative_validated_return_blocks():
    result = evaluate_direction_gate(row(validated_expected_return_bps=-1.0))
    assert result["direction_decision"] == "direction_block"
    assert result["direction_primary_reason"] == "negative_validated_expected_return"


def test_long_profit_factor_below_threshold_blocks():
    result = evaluate_direction_gate(row(validated_profit_factor=0.99))
    assert result["direction_decision"] == "direction_block"
    assert result["direction_primary_reason"] == "validated_profit_factor_below_one"


def test_short_blocks_by_default_when_short_policy_disabled():
    result = evaluate_direction_gate(row(side="sell", source_trade_action="Short", trade_action="Short"))
    assert result["direction_decision"] == "direction_research_only"
    assert result["direction_primary_reason"] == "short_side_validation_required"


def test_short_with_negative_expected_return_blocks():
    result = evaluate_direction_gate(row(side="sell", source_trade_action="Short", trade_action="Short", validated_expected_return_bps=-5.0))
    assert result["direction_decision"] == "direction_block"
    assert "short_negative_edge" in result["direction_blocking_reasons"]


def test_short_with_inverse_warning_becomes_inverse_watch():
    result = evaluate_direction_gate(
        row(side="sell", source_trade_action="Short", trade_action="Short", inverse_watch_flag=True)
    )
    assert result["direction_decision"] == "direction_inverse_watch"
    assert result["direction_inverse_warning"] is True


def test_conflicting_source_trade_directional_actions_manual_review():
    result = evaluate_direction_gate(row(source_trade_action="Long", trade_action="Short", directional_action="Short"))
    assert result["direction_decision"] == "direction_manual_review"
    assert result["direction_primary_reason"] == "conflicting_direction_signals"


def test_execution_rank_only_assigned_after_direction_gate_pass():
    frame = pd.DataFrame(
        [
            row(symbol="PASS", rank_overall=1),
            row(symbol="NOPE", rank_overall=2, source_trade_action="No Decision", trade_action="No Decision", directional_action="Long"),
        ]
    )
    ranked = build_execution_ranked_candidates(frame, short_policy=ShortSidePolicy(), active_session_mode="regular_session")
    good = ranked[ranked["symbol"].eq("PASS")].iloc[0]
    bad = ranked[ranked["symbol"].eq("NOPE")].iloc[0]
    assert good["execution_rank"] == 1
    assert good["direction_decision"] == "direction_pass"
    assert pd.isna(bad["execution_rank"])
    assert bad["direction_decision"] == "direction_research_only"


def test_direction_gate_never_enables_live_trading_or_broker_submission_path():
    source = Path("src/stockml/trading/direction_gate.py").read_text(encoding="utf-8")
    assert "submit_order" not in source
    assert "Alpaca" not in source
    assert DirectionGateConfig().enabled is True
    assert ShortDirectionGateConfig().default_short_decision == "research_only"
