from __future__ import annotations

import pandas as pd

from stockml.trading.candidate_direction_resolution import apply_direction_authority


def _candidate(**overrides):
    row = {
        "symbol": "DFTX",
        "side": "buy",
        "source_trade_action": "Long",
        "trade_action": "Long",
        "directional_action": "Long",
        "ticker_direction_bias": "trust_long",
        "validated_expected_return_bps": 41.8,
        "trade_quality_status": "approved",
        "candidate_status": "approved",
        "order_eligible": True,
        "approved_notional": 500,
        "suggested_quantity": 1,
    }
    row.update(overrides)
    return row


def test_dftx_style_row_remains_eligible_if_all_direction_inputs_align():
    out = apply_direction_authority(pd.DataFrame([_candidate()]))

    row = out.iloc[0]
    assert row["symbol"] == "DFTX"
    assert row["executable_direction_status"] == "source_approved_memory_aligned"
    assert row["direction_alignment_status"] == "aligned"
    assert row["final_execution_side"] == "LONG"
    assert row["trade_quality_status"] == "approved"
    assert bool(row["order_eligible"]) is True


def test_blze_style_reduced_row_remains_reduced_when_direction_aligns():
    out = apply_direction_authority(pd.DataFrame([_candidate(symbol="BLZE", trade_quality_status="reduced", approved_notional=125)]))

    row = out.iloc[0]
    assert row["symbol"] == "BLZE"
    assert row["trade_quality_status"] == "reduced"
    assert row["executable_direction_status"] == "source_approved_memory_aligned"
    assert row["final_execution_side"] == "LONG"


def test_reduced_is_not_used_as_primary_block_reason():
    out = apply_direction_authority(
        pd.DataFrame([
            _candidate(
                symbol="BLZE",
                trade_quality_status="rejected",
                candidate_status="rejected",
                order_eligible=False,
                approved_notional=0,
                suggested_quantity=0,
                trade_quality_reason="reduced",
                primary_block_reason="reduced",
                risk_tier="medium",
            )
        ])
    )

    row = out.iloc[0]
    assert row["primary_block_reason"] == "reduced_due_to_risk_tier"


def test_cast_style_clear_long_can_stay_blocked_by_prior_gate():
    out = apply_direction_authority(
        pd.DataFrame([
            _candidate(symbol="CAST", trade_quality_status="rejected", order_eligible=False, approved_notional=0, suggested_quantity=0, trade_quality_reason="volatility_extreme")
        ])
    )

    row = out.iloc[0]
    assert row["symbol"] == "CAST"
    assert row["executable_direction_status"] == "source_approved_memory_aligned"
    assert row["trade_quality_status"] == "rejected"


def test_no_decision_with_planner_action_is_research_only_and_blocked():
    out = apply_direction_authority(pd.DataFrame([_candidate(source_trade_action="No Decision", trade_action="Long")]))

    row = out.iloc[0]
    assert row["executable_direction_status"] == "planner_only_not_executable"
    assert bool(row["research_only"]) is True
    assert bool(row["order_eligible"]) is False
    assert row["final_execution_side"] == "NONE"
    assert row["approved_notional"] == 0
    assert "planner_derived_action_without_source_approval" in row["trade_quality_reason"]


def test_direction_conflict_gets_primary_block_reason():
    out = apply_direction_authority(pd.DataFrame([_candidate(ticker_direction_bias="trust_short")]))

    row = out.iloc[0]
    assert bool(row["direction_conflict"]) is True
    assert row["primary_block_reason"] == "direction_memory_conflict"
    assert bool(row["order_eligible"]) is False


def test_missing_market_cap_outranks_softer_direction_reason_in_candidate_pool():
    out = apply_direction_authority(
        pd.DataFrame([
            _candidate(
                symbol="ADVB",
                ticker_direction_bias="insufficient_data",
                market_cap=pd.NA,
                trade_quality_reason="approved",
            )
        ])
    )

    row = out.iloc[0]
    assert row["primary_block_reason"] == "market_cap_missing"
    assert "market_cap_missing" in row["trade_quality_reason"]
    assert bool(row["order_eligible"]) is False
    assert row["final_execution_side"] == "NONE"
