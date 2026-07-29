from __future__ import annotations

import pandas as pd

from stockml.candidates.execution_ranker import build_execution_ranked_candidates


def _candidate(**overrides):
    row = {
        "raw_rank": 1,
        "symbol": "AAA",
        "side": "sell",
        "source_trade_action": "Short",
        "trade_action": "Short",
        "directional_action": "Short",
        "ticker_direction_bias": "trust_short",
        "trade_quality_status": "reduced",
        "trade_quality_reason": "reduced",
        "order_eligible": True,
        "approved_notional": 100,
        "suggested_quantity": 1,
        "risk_tier": "medium",
        "volatility_tier": "normal",
        "expected_return_quality": "usable",
        "calibration_quality": "usable",
        "validated_expected_return_bps": -10,
        "validated_hit_rate": 0.45,
        "validated_profit_factor": 0.8,
    }
    row.update(overrides)
    return row


def test_short_side_validation_required_outranks_reduced_risk_reason():
    ranked = build_execution_ranked_candidates(pd.DataFrame([_candidate()]))

    row = ranked.iloc[0]
    assert row["status"] == "blocked"
    assert bool(row["research_only"]) is False
    assert row["primary_block_reason"] == "short_side_validation_required"
    assert "negative_validated_expected_return" in row["all_block_reasons"]
    assert "reduced_due_to_risk_tier" in row["all_block_reasons"]
    assert row["final_execution_side"] == "NONE"


def test_source_short_insufficient_memory_is_watch_not_research_only():
    ranked = build_execution_ranked_candidates(
        pd.DataFrame([
            _candidate(
                validated_expected_return_bps=35,
                ticker_direction_bias="insufficient_data",
                trade_quality_status="approved",
                trade_quality_reason="approved",
                risk_tier="high_quality",
            )
        ])
    )

    row = ranked.iloc[0]
    assert row["status"] == "watch"
    assert bool(row["research_only"]) is False
    assert row["primary_block_reason"] == "short_side_validation_required"
    assert "direction_memory_insufficient" in row["all_block_reasons"]
    assert row["final_execution_side"] == "NONE"


def test_market_cap_missing_outranks_direction_memory_for_source_approved_long():
    ranked = build_execution_ranked_candidates(
        pd.DataFrame(
            [
                _candidate(
                    symbol="ABTC",
                    side="buy",
                    source_trade_action="Long",
                    trade_action="Long",
                    directional_action="Long",
                    ticker_direction_bias="trust_long",
                    ticker_direction_sample_count=100,
                    market_cap=pd.NA,
                    trade_quality_status="approved",
                    trade_quality_reason="approved",
                    validated_expected_return_bps=42,
                    risk_tier="medium",
                )
            ]
        ),
        active_session_mode="regular_session",
    )

    row = ranked.iloc[0]
    assert row["status"] == "blocked"
    assert row["primary_block_reason"] == "market_cap_missing"
    assert "market_cap_missing" in row["all_block_reasons"]
    assert row["final_execution_side"] == "NONE"
