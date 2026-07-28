from __future__ import annotations

import pandas as pd

from stockml.diagnostics.source_approval_expansion_measurement import (
    BUCKET_EXPANSION_ELIGIBLE,
    BUCKET_SOURCE_APPROVED,
    BUCKET_STILL_BLOCKED,
    build_expansion_bucket_detail,
    build_expansion_edge_report,
    run_source_approval_expansion_measurement,
)
from stockml.trading.source_approval_expansion import SourceApprovalExpansionConfig


def _cfg() -> SourceApprovalExpansionConfig:
    return SourceApprovalExpansionConfig(enabled=False, mode="diagnostic_only", min_ticker_direction_sample_count=50)


def _candidate(**overrides):
    row = {
        "symbol": "AAA",
        "side": "buy",
        "trade_action": "Long",
        "directional_action": "Long",
        "source_trade_action": "No Decision",
        "source_no_decision_reason": "source_threshold_too_strict",
        "ticker_direction_sample_count": 75,
        "ticker_direction_bias": "trust_long",
        "validated_expected_return_bps": 42.0,
        "expected_return_scope": "side",
        "risk_tier": "medium",
        "volatility_tier": "normal",
        "primary_block_reason": "planner_derived_action_without_source_approval",
        "all_block_reasons": "planner_derived_action_without_source_approval",
        "final_execution_side": "NONE",
        "status": "research_only",
        "executable": False,
        "raw_rank": 10,
    }
    row.update(overrides)
    return row


def test_ticket13_buckets_source_expansion_and_still_blocked():
    candidates = pd.DataFrame(
        [
            _candidate(symbol="SRC", source_trade_action="Long", final_execution_side="LONG", status="executable", executable=True),
            _candidate(symbol="EXP"),
            _candidate(symbol="BLK", ticker_direction_sample_count=5),
        ]
    )

    out = build_expansion_bucket_detail(candidates, config=_cfg())

    buckets = dict(zip(out["symbol"], out["expansion_bucket"]))
    assert buckets["SRC"] == BUCKET_SOURCE_APPROVED
    assert buckets["EXP"] == BUCKET_EXPANSION_ELIGIBLE
    assert buckets["BLK"] == BUCKET_STILL_BLOCKED
    exp = out[out["symbol"].eq("EXP")].iloc[0]
    assert bool(exp["sample_count_pass"]) is True
    assert bool(exp["ticker_direction_bias_pass"]) is True
    assert bool(exp["positive_validated_expected_return_pass"]) is True
    assert bool(exp["executable"]) is False


def test_ticket13_spot_check_conditions_fail_when_config_condition_fails():
    candidates = pd.DataFrame([_candidate(ticker_direction_bias="trust_short")])

    out = build_expansion_bucket_detail(candidates, config=_cfg())

    row = out.iloc[0]
    assert row["expansion_bucket"] == BUCKET_STILL_BLOCKED
    assert bool(row["ticker_direction_bias_pass"]) is False
    assert row["source_expansion_reason"] == "ticker_direction_bias_not_trust_long"


def test_ticket14_reports_insufficient_data_until_powered():
    frame = pd.DataFrame(
        [
            _candidate(symbol="SRC", source_trade_action="Long", side="buy", directional_forward_5d_bps=30, estimated_execution_cost_bps=10),
            _candidate(symbol="EXP", side="buy", directional_forward_5d_bps=25, estimated_execution_cost_bps=10),
            _candidate(symbol="BLK", side="buy", ticker_direction_sample_count=1, directional_forward_5d_bps=-5, estimated_execution_cost_bps=10),
        ]
    )

    report, verdict, n = build_expansion_edge_report(frame, config=_cfg(), minimum_powered_rows=30)

    assert verdict == "INSUFFICIENT DATA"
    assert n[BUCKET_SOURCE_APPROVED] == 1
    assert n[BUCKET_EXPANSION_ELIGIBLE] == 1
    assert n[BUCKET_STILL_BLOCKED] == 1
    assert set(report["expansion_bucket"]) == {BUCKET_SOURCE_APPROVED, BUCKET_EXPANSION_ELIGIBLE, BUCKET_STILL_BLOCKED}


def test_measurement_writes_reports_without_promoting_rows(tmp_path):
    candidates = pd.DataFrame([_candidate(symbol="EXP")])
    counterfactual = tmp_path / "counterfactual_forward_returns_fixture.csv"
    pd.DataFrame([_candidate(symbol="EXP", side="buy", directional_forward_5d_bps=12, estimated_execution_cost_bps=10)]).to_csv(counterfactual, index=False)

    result = run_source_approval_expansion_measurement(
        candidates=candidates,
        counterfactual_path=counterfactual,
        output_dir=tmp_path,
        root=tmp_path,
        stamp="fixture",
        config=_cfg(),
    )

    assert result.daily_bucket_counts[BUCKET_EXPANSION_ELIGIBLE] == 1
    assert result.edge_verdict == "INSUFFICIENT DATA"
    detail = pd.read_csv(result.ticket13_detail_path)
    assert detail.iloc[0]["expansion_bucket"] == BUCKET_EXPANSION_ELIGIBLE
    assert str(detail.iloc[0]["final_execution_side"]) == "NONE"
    assert result.materiality_confirmation == "non_material_measurement_only_no_lane_change"
