from __future__ import annotations

import pandas as pd

from stockml.trading.forward_paper_reports import (
    build_counterfactual_status_report,
    build_gate_funnel,
    build_source_direction_coverage,
)


def test_source_direction_coverage_distinguishes_abstain_from_missing_signal():
    frame = pd.DataFrame(
        [
            {"symbol": "AAA", "source_trade_action": "No Decision", "model_score": 0.2, "meta_label_probability": 0.6},
            {"symbol": "BBB", "source_trade_action": "No Decision"},
            {"symbol": "CCC", "source_trade_action": "Long", "model_score": 0.9},
        ]
    )

    out = build_source_direction_coverage(frame)

    reasons = dict(zip(out["symbol"], out["source_no_decision_reason"]))
    assert reasons["AAA"] == "scored_and_abstained"
    assert reasons["BBB"] == "source_signal_not_available"
    assert reasons["CCC"] == "source_approved"


def test_gate_funnel_includes_execution_integrity_stages():
    candidates = pd.DataFrame(
        [
            {"symbol": "AAA", "source_trade_action": "Long", "final_execution_side": "LONG", "primary_block_reason": "", "all_block_reasons": "", "net_expected_return_bps": 20},
            {"symbol": "BBB", "source_trade_action": "No Decision", "final_execution_side": "NONE", "primary_block_reason": "planner_derived_action_without_source_approval", "all_block_reasons": "planner_derived_action_without_source_approval", "net_expected_return_bps": 20},
        ]
    )
    results = pd.DataFrame([{"symbol": "AAA", "status": "submitted", "alpaca_status": "filled"}])

    out = build_gate_funnel(candidates, results)

    counts = dict(zip(out["stage"], out["count"]))
    assert counts["raw_pool"] == 2
    assert counts["source_approved_direction"] == 1
    assert counts["executable"] == 1
    assert counts["submitted"] == 1
    assert counts["filled"] == 1


def test_counterfactual_status_report_defaults_to_insufficient_data():
    frame = pd.DataFrame(
        [
            {"symbol": "AAA", "status": "executable", "side": "buy", "directional_forward_5d_bps": 25, "estimated_execution_cost_bps": 10},
            {"symbol": "BBB", "primary_block_reason": "planner_derived_action_without_source_approval", "side": "buy", "directional_forward_5d_bps": 15, "estimated_execution_cost_bps": 10},
        ]
    )

    out = build_counterfactual_status_report(frame)

    assert set(out["terminal_status"]) == {"executable", "planner_only_blocked"}
    assert set(out["verdict"]) == {"INSUFFICIENT DATA"}
