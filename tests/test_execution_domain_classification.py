from __future__ import annotations

import pandas as pd

from stockml.candidates.execution_ranker import build_execution_ranked_candidates


def _candidate(**overrides):
    row = {
        "raw_rank": 1,
        "symbol": "AAA",
        "side": "buy",
        "source_trade_action": "Long",
        "trade_action": "Long",
        "directional_action": "Long",
        "ticker_direction_bias": "trust_long",
        "trade_quality_status": "approved",
        "trade_quality_reason": "approved",
        "order_eligible": True,
        "approved_notional": 100,
        "suggested_quantity": 1,
        "risk_tier": "high_quality",
        "expected_return_quality": "usable",
        "calibration_quality": "usable",
        "validated_expected_return_bps": 42,
        "validated_hit_rate": 0.55,
        "validated_profit_factor": 1.4,
    }
    row.update(overrides)
    return row


def test_no_decision_planner_long_becomes_shadow_observation():
    out = build_execution_ranked_candidates(
        pd.DataFrame([_candidate(source_trade_action="No Decision", trade_action="Long", directional_action="Long")])
    )

    row = out.iloc[0]
    assert row["execution_domain"] == "shadow_observation"
    assert bool(row["execution_eligible"]) is False
    assert row["final_execution_side"] == "NONE"
    assert row["shadow_reason"] == "planner_derived_action_without_source_approval"


def test_no_decision_planner_short_becomes_shadow_observation():
    out = build_execution_ranked_candidates(
        pd.DataFrame([_candidate(side="sell", source_trade_action="No Decision", trade_action="Short", directional_action="Short")])
    )

    row = out.iloc[0]
    assert row["execution_domain"] == "shadow_observation"
    assert bool(row["execution_eligible"]) is False
    assert row["final_execution_side"] == "NONE"
    assert row["shadow_reason"] == "planner_derived_action_without_source_approval"


def test_source_long_aligned_and_approved_becomes_execution_candidate():
    out = build_execution_ranked_candidates(pd.DataFrame([_candidate(symbol="DFTX")]))

    row = out.iloc[0]
    assert row["execution_domain"] == "execution_candidate"
    assert bool(row["execution_eligible"]) is True
    assert row["final_execution_side"] == "LONG"
    assert row["execution_rank"] == 1


def test_source_long_blocked_by_risk_becomes_blocked_candidate():
    out = build_execution_ranked_candidates(
        pd.DataFrame([
            _candidate(
                trade_quality_status="rejected",
                trade_quality_reason="risk_gate_failed",
                order_eligible=False,
                approved_notional=0,
                suggested_quantity=0,
            )
        ])
    )

    row = out.iloc[0]
    assert row["execution_domain"] == "blocked_candidate"
    assert bool(row["execution_eligible"]) is False
    assert row["primary_block_reason"] == "risk_gate_failed"


def test_source_short_blocked_by_validation_becomes_blocked_candidate():
    out = build_execution_ranked_candidates(
        pd.DataFrame([
            _candidate(
                side="sell",
                source_trade_action="Short",
                trade_action="Short",
                directional_action="Short",
                ticker_direction_bias="trust_short",
                validated_expected_return_bps=-20,
            )
        ])
    )

    row = out.iloc[0]
    assert row["execution_domain"] == "blocked_candidate"
    assert row["primary_block_reason"] == "short_side_validation_required"
    assert "negative_validated_expected_return" in row["all_block_reasons"]


def test_reduced_valid_long_with_notional_can_remain_execution_candidate():
    out = build_execution_ranked_candidates(
        pd.DataFrame([_candidate(trade_quality_status="reduced", approved_notional=50, suggested_quantity=1)])
    )

    row = out.iloc[0]
    assert row["execution_domain"] == "execution_candidate"
    assert bool(row["execution_eligible"]) is True


def test_reduced_valid_long_without_notional_becomes_blocked_candidate():
    out = build_execution_ranked_candidates(
        pd.DataFrame([_candidate(trade_quality_status="reduced", approved_notional=0, suggested_quantity=0)])
    )

    row = out.iloc[0]
    assert row["execution_domain"] == "blocked_candidate"
    assert bool(row["execution_eligible"]) is False

