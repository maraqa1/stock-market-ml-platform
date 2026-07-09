from __future__ import annotations

import pandas as pd

from stockml.diagnostics.source_direction_coverage import (
    build_source_direction_coverage_detail,
    run_source_direction_coverage_diagnostic,
    source_no_decision_reason,
)


def _candidate(**overrides):
    row = {
        "raw_rank": 1,
        "rank_overall": 1,
        "symbol": "AAA",
        "side": "buy",
        "source_trade_action": "No Decision",
        "trade_action": "Long",
        "directional_action": "Long",
        "model_score": 0.8,
        "directional_strength": 0.8,
        "confidence_score": 0.8,
        "risk_adjusted_score": 0.7,
        "meta_label_probability": 0.8,
        "ticker_direction_bias": "trust_long",
        "ticker_direction_sample_count": 30,
        "trade_quality_status": "approved",
        "trade_quality_reason": "",
        "approved_notional": 100,
        "suggested_quantity": 1,
        "risk_tier": "high_quality",
        "volatility_tier": "normal",
        "liquidity_tier": "liquid",
        "expected_return_quality": "usable",
        "validation_quality": "usable",
        "validated_expected_return_bps": 42,
        "validated_hit_rate": 0.55,
        "validated_profit_factor": 1.4,
    }
    row.update(overrides)
    return row


def test_no_decision_reasons_are_assigned():
    assert source_no_decision_reason(pd.Series(_candidate(model_score="", rank_overall=""))) == "missing_model_score"
    assert source_no_decision_reason(pd.Series(_candidate(meta_label_probability=""))) == "meta_label_missing"
    assert source_no_decision_reason(pd.Series(_candidate(meta_label_probability=0.2, trade_quality_reason="meta_label_probability_below_threshold"))) == "meta_label_rejected"
    assert source_no_decision_reason(pd.Series(_candidate(ticker_direction_sample_count=1))) == "insufficient_direction_memory"
    assert source_no_decision_reason(pd.Series(_candidate(trade_quality_reason="risk_gate_failed"))) == "risk_gate_failed"


def test_planner_derived_rows_remain_non_executable():
    detail = build_source_direction_coverage_detail(pd.DataFrame([_candidate()]))

    row = detail.iloc[0]
    assert row["source_trade_action"] == "No Decision"
    assert row["execution_domain"] == "shadow_observation"
    assert row["source_no_decision_reason"] == "planner_only_without_source_authority"


def test_near_miss_long_candidates_are_detected():
    detail = build_source_direction_coverage_detail(
        pd.DataFrame([
            _candidate(symbol="NEAR", raw_rank=1),
            _candidate(symbol="HARD", raw_rank=2, trade_quality_reason="risk_gate_failed"),
            _candidate(symbol="SHORT", raw_rank=3, side="sell", trade_action="Short", directional_action="Short"),
        ])
    )

    near = detail.set_index("symbol")["long_near_miss"].to_dict()
    assert bool(near["NEAR"]) is True
    assert bool(near["HARD"]) is False
    assert bool(near["SHORT"]) is False


def test_source_approved_rows_are_not_reclassified():
    detail = build_source_direction_coverage_detail(
        pd.DataFrame([
            _candidate(symbol="DFTX", source_trade_action="Long", trade_action="Long", directional_action="Long")
        ])
    )

    row = detail.iloc[0]
    assert row["source_trade_action"] == "Long"
    assert row["source_no_decision_reason"] == ""
    assert row["execution_domain"] == "execution_candidate"


def test_diagnostic_writes_outputs_without_submitting_orders(tmp_path):
    output = run_source_direction_coverage_diagnostic(
        candidates=pd.DataFrame([_candidate(), _candidate(symbol="DFTX", source_trade_action="Long")]),
        output_dir=tmp_path,
        stamp="20260709_120000",
    )

    assert output.status == "ok"
    assert output.detail_path.exists()
    assert output.summary_path.exists()
    frame = pd.read_csv(output.detail_path)
    assert "source_no_decision_reason" in frame.columns
    assert output.long_near_miss_count == 1
    assert not any(path.name.startswith("08_alpaca_paper_order_results") for path in tmp_path.iterdir())
