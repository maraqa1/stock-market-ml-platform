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
        "current_price": 100,
        "limit_price": 100,
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
        pd.DataFrame([_candidate(source_trade_action="No Decision", trade_action="Long", directional_action="Long")]),
        active_session_mode="regular_session",
    )

    row = out.iloc[0]
    assert row["execution_domain"] == "shadow_observation"
    assert bool(row["execution_eligible"]) is False
    assert row["final_execution_side"] == "NONE"
    assert row["shadow_reason"] == "planner_derived_action_without_source_approval"


def test_no_decision_planner_short_becomes_shadow_observation():
    out = build_execution_ranked_candidates(
        pd.DataFrame([_candidate(side="sell", source_trade_action="No Decision", trade_action="Short", directional_action="Short")]),
        active_session_mode="regular_session",
    )

    row = out.iloc[0]
    assert row["execution_domain"] == "shadow_observation"
    assert bool(row["execution_eligible"]) is False
    assert row["final_execution_side"] == "NONE"
    assert row["shadow_reason"] == "planner_derived_action_without_source_approval"


def test_source_long_aligned_and_approved_becomes_execution_candidate():
    out = build_execution_ranked_candidates(pd.DataFrame([_candidate(symbol="DFTX")]), active_session_mode="regular_session")

    row = out.iloc[0]
    assert row["execution_domain"] == "execution_candidate"
    assert bool(row["execution_eligible"]) is True
    assert bool(row["execution_pool_eligible"]) is True
    assert bool(row["watchlist_eligible"]) is False
    assert row["trade_authority_status"] == "authorized"
    assert row["execution_domain_reason"] == "execution_ready"
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
        ]),
        active_session_mode="regular_session",
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
        ]),
        active_session_mode="regular_session",
    )

    row = out.iloc[0]
    assert row["execution_domain"] == "blocked_candidate"
    assert row["primary_block_reason"] == "short_side_validation_required"
    assert "negative_validated_expected_return" in row["all_block_reasons"]


def test_reduced_valid_long_with_order_ready_becomes_execution_candidate():
    out = build_execution_ranked_candidates(
        pd.DataFrame([_candidate(trade_quality_status="reduced", approved_notional=50, suggested_quantity=1)]),
        active_session_mode="regular_session",
    )

    row = out.iloc[0]
    assert row["execution_domain"] == "execution_candidate"
    assert bool(row["execution_eligible"]) is True
    assert bool(row["execution_pool_eligible"]) is True
    assert bool(row["watchlist_eligible"]) is False
    assert bool(row["order_ready"]) is True


def test_reduced_valid_long_without_notional_becomes_blocked_candidate():
    out = build_execution_ranked_candidates(
        pd.DataFrame([_candidate(trade_quality_status="reduced", approved_notional=0, suggested_quantity=0)]),
        active_session_mode="regular_session",
    )

    row = out.iloc[0]
    assert row["execution_domain"] == "blocked_candidate"
    assert bool(row["execution_eligible"]) is False


def test_reduced_long_without_order_ready_is_blocked_not_research_only():
    out = build_execution_ranked_candidates(
        pd.DataFrame([_candidate(trade_quality_status="reduced", order_eligible=False, approved_notional=50, suggested_quantity=1)]),
        active_session_mode="regular_session",
    )

    row = out.iloc[0]
    assert row["execution_domain"] == "blocked_candidate"
    assert bool(row["research_only"]) is False
    assert row["order_ready_reason"] == "order_not_ready_order_eligible_false"
    assert row["primary_block_reason"] == "order_not_ready_order_eligible_false"


def test_source_short_watch_is_not_execution_eligible():
    out = build_execution_ranked_candidates(
        pd.DataFrame([
            _candidate(
                side="sell",
                source_trade_action="Short",
                trade_action="Short",
                directional_action="Short",
                ticker_direction_bias="trust_short",
                trade_quality_status="reduced",
                approved_notional=50,
                suggested_quantity=1,
                validated_expected_return_bps=30,
                short_side_validation_status="watch",
            )
        ]),
        active_session_mode="regular_session",
    )

    row = out.iloc[0]
    assert row["execution_domain"] in {"watch_candidate", "blocked_candidate"}
    assert bool(row["execution_eligible"]) is False
    assert bool(row["execution_pool_eligible"]) is False


def test_shadow_observation_has_domain_reason_and_no_pool_eligibility():
    out = build_execution_ranked_candidates(
        pd.DataFrame([_candidate(source_trade_action="No Decision", trade_action="Long", directional_action="Long")]),
        active_session_mode="regular_session",
    )

    row = out.iloc[0]
    assert row["execution_domain"] == "shadow_observation"
    assert row["trade_authority_status"] == "shadow"
    assert row["execution_domain_reason"] == "planner_derived_action_without_source_approval"
    assert bool(row["execution_pool_eligible"]) is False
    assert bool(row["watchlist_eligible"]) is False
