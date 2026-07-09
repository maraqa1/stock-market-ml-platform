from __future__ import annotations

import pandas as pd

from stockml.candidates.short_side_policy import ShortSidePolicy
from stockml.trading.direction_authority import DirectionAuthorityConfig, resolve_direction_authority


CFG = DirectionAuthorityConfig()


def row(**overrides):
    base = {
        "source_trade_action": "Long",
        "trade_action": "Long",
        "directional_action": "Long",
        "side": "buy",
        "ticker_direction_bias": "trust_long",
        "validated_expected_return_bps": 42,
        "validated_hit_rate": 0.55,
        "validated_profit_factor": 1.5,
        "side_probability": 0.9,
    }
    base.update(overrides)
    return pd.Series(base)


def test_source_long_memory_trust_long_is_aligned():
    out = resolve_direction_authority(row(), config=CFG)

    assert out["source_approved_direction"] == "LONG"
    assert out["final_proposed_side"] == "LONG"
    assert out["direction_alignment_status"] == "aligned"
    assert out["executable_direction_status"] == "source_approved_memory_aligned"


def test_source_short_memory_trust_short_is_aligned_when_short_policy_allows():
    out = resolve_direction_authority(
        row(source_trade_action="Short", trade_action="Short", directional_action="Short", side="sell", ticker_direction_bias="trust_short"),
        config=CFG,
        short_policy=ShortSidePolicy(enabled=True, allow_shorts_in_validation=True),
    )

    assert out["source_approved_direction"] == "SHORT"
    assert out["final_proposed_side"] == "SHORT"
    assert out["direction_alignment_status"] == "aligned"
    assert out["executable_direction_status"] == "source_approved_memory_aligned"


def test_source_long_memory_trust_short_is_conflict():
    out = resolve_direction_authority(row(ticker_direction_bias="trust_short"), config=CFG)

    assert out["direction_conflict"] is True
    assert out["direction_conflict_reason"] == "direction_memory_conflict"
    assert out["executable_direction_status"] == "source_approved_memory_conflict"


def test_source_short_memory_trust_long_is_conflict():
    out = resolve_direction_authority(
        row(source_trade_action="Short", trade_action="Short", directional_action="Short", side="sell", ticker_direction_bias="trust_long"),
        config=CFG,
    )

    assert out["direction_conflict"] is True
    assert out["direction_conflict_reason"] == "direction_memory_conflict"
    assert out["executable_direction_status"] == "source_approved_memory_conflict"


def test_no_decision_planner_long_is_research_only_not_executable():
    out = resolve_direction_authority(row(source_trade_action="No Decision", trade_action="Long", directional_action="Long"), config=CFG)

    assert out["final_proposed_side"] == "NONE"
    assert out["executable_direction_status"] == "planner_only_not_executable"
    assert out["direction_resolution"] == "research_only"
    assert out["direction_resolution_reason"] == "planner_derived_action_without_source_approval"


def test_no_decision_planner_short_is_research_only_not_executable():
    out = resolve_direction_authority(
        row(source_trade_action="No Decision", trade_action="Short", directional_action="Short", side="sell", ticker_direction_bias="trust_short"),
        config=CFG,
    )

    assert out["final_proposed_side"] == "NONE"
    assert out["executable_direction_status"] == "planner_only_not_executable"


def test_uncalibrated_side_probability_is_raw_score_not_probability_win():
    out = resolve_direction_authority(row(side_probability=0.99), config=CFG)

    assert out["raw_side_score"] == 0.99
    assert out["calibrated_probability_win"] == ""
    assert out["probability_calibration_status"] == "uncalibrated"


def test_negative_short_side_validation_blocks_short():
    out = resolve_direction_authority(
        row(
            source_trade_action="Short",
            trade_action="Short",
            directional_action="Short",
            side="sell",
            ticker_direction_bias="trust_short",
            validated_expected_return_bps=-29.7,
        ),
        config=CFG,
        short_policy=ShortSidePolicy(enabled=True, allow_shorts_in_validation=True),
    )

    assert out["final_proposed_side"] == "NONE"
    assert out["executable_direction_status"] == "side_validation_failed"
