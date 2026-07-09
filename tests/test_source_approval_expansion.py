from __future__ import annotations

import pandas as pd

from stockml.candidates.execution_ranker import build_execution_ranked_candidates
from stockml.trading.direction_authority import resolve_direction_authority
from stockml.trading.source_approval_expansion import (
    SourceApprovalExpansionConfig,
    evaluate_source_approval_expansion,
)


def _cfg(**overrides):
    data = {
        "enabled": False,
        "mode": "diagnostic_only",
        "min_ticker_direction_sample_count": 50,
    }
    data.update(overrides)
    return SourceApprovalExpansionConfig(**data)


def _row(**overrides):
    row = {
        "symbol": "AAA",
        "side": "buy",
        "source_trade_action": "No Decision",
        "trade_action": "Long",
        "directional_action": "Long",
        "source_no_decision_reason": "source_threshold_too_strict",
        "ticker_direction_bias": "trust_long",
        "ticker_direction_sample_count": 80,
        "expected_return_scope": "side",
        "validated_expected_return_bps": 42,
        "risk_tier": "high_quality",
        "volatility_tier": "normal",
        "trade_quality_status": "approved",
        "approved_notional": 100,
        "suggested_quantity": 1,
        "validation_quality": "usable",
        "expected_return_quality": "usable",
        "validated_hit_rate": 0.55,
        "validated_profit_factor": 1.4,
    }
    row.update(overrides)
    return pd.Series(row)


def test_strong_planner_long_with_trust_long_is_flagged_as_would_upgrade():
    out = evaluate_source_approval_expansion(_row(), config=_cfg())

    assert out["source_expansion_candidate"] is True
    assert out["source_expansion_decision"] == "would_upgrade"
    assert out["would_upgrade_to_source_long"] is True


def test_weak_planner_long_is_not_upgraded():
    out = evaluate_source_approval_expansion(_row(source_no_decision_reason="weak_directional_strength"), config=_cfg())

    assert out["source_expansion_candidate"] is True
    assert out["source_expansion_decision"] == "blocked"
    assert out["would_upgrade_to_source_long"] is False


def test_trust_short_conflict_blocks_upgrade():
    out = evaluate_source_approval_expansion(
        _row(ticker_direction_bias="trust_short", all_block_reasons="direction_memory_conflict"),
        config=_cfg(),
    )

    assert out["source_expansion_decision"] == "blocked"
    assert out["source_expansion_reason"] == "direction_memory_conflict"


def test_model_evidence_missing_blocks_upgrade():
    out = evaluate_source_approval_expansion(_row(all_block_reasons="model_evidence_missing"), config=_cfg())

    assert out["source_expansion_decision"] == "blocked"
    assert out["source_expansion_reason"] == "model_evidence_missing"


def test_risk_gate_failed_blocks_upgrade():
    out = evaluate_source_approval_expansion(_row(all_block_reasons="risk_gate_failed"), config=_cfg())

    assert out["source_expansion_decision"] == "blocked"
    assert out["source_expansion_reason"] == "risk_gate_failed"


def test_planner_short_is_never_upgraded():
    out = evaluate_source_approval_expansion(
        _row(side="sell", trade_action="Short", directional_action="Short", ticker_direction_bias="trust_short"),
        config=_cfg(),
    )

    assert out["source_expansion_candidate"] is False
    assert out["source_expansion_decision"] == "not_candidate"
    assert out["would_upgrade_to_source_long"] is False


def test_diagnostic_only_mode_does_not_make_row_executable():
    authority = resolve_direction_authority(_row(), source_expansion_config=_cfg())

    assert authority["source_expansion_decision"] == "would_upgrade"
    assert authority["would_upgrade_to_source_long"] is True
    assert authority["direction_resolution"] == "research_only"
    assert authority["final_execution_side"] == "NONE"


def test_enabled_mode_can_only_create_watch_candidate_not_execution_candidate():
    cfg = _cfg(enabled=True, mode="watch_only")
    authority = resolve_direction_authority(_row(), source_expansion_config=cfg)

    assert authority["source_expansion_decision"] == "watch_candidate"
    assert authority["direction_resolution"] == "watch"
    assert authority["final_execution_side"] == "NONE"

    ranked_enabled = build_execution_ranked_candidates(
        pd.DataFrame([_row()]),
        source_expansion_config=cfg,
    )
    assert ranked_enabled.iloc[0]["execution_domain"] == "watch_candidate"
    assert bool(ranked_enabled.iloc[0]["execution_eligible"]) is False
