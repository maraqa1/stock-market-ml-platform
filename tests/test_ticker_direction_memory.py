from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.diagnostics.ticker_direction_memory import run_ticker_direction_memory
from stockml.trading.direction_gate import evaluate_direction_gate
from stockml.trading.ticker_direction_memory import (
    BIAS_INSUFFICIENT_DATA,
    BIAS_INVERSE_WATCH,
    BIAS_TRUST_ORIGINAL,
    TickerDirectionMemoryConfig,
    apply_ticker_direction_memory,
    normalize_direction_outcomes,
    summarize_ticker_direction_memory,
)


def candidate(**overrides):
    data = {
        "symbol": "AAA",
        "side": "buy",
        "source_trade_action": "Long",
        "trade_action": "Long",
        "validated_expected_return_bps": 25.0,
        "validated_profit_factor": 1.25,
        "validated_hit_rate": 0.55,
        "expected_return_quality": "usable",
        "calibration_quality": "usable",
    }
    data.update(overrides)
    return data


def test_ticker_memory_flags_inverse_when_opposite_side_consistently_wins():
    outcomes = pd.DataFrame(
        {
            "symbol": ["AAA"] * 6,
            "original_return_bps": [-50, -40, -35, -25, -30, -45],
            "inverse_return_bps": [50, 40, 35, 25, 30, 45],
        }
    )
    memory = summarize_ticker_direction_memory(outcomes, config=TickerDirectionMemoryConfig(min_ticker_samples=5))

    row = memory.iloc[0]
    assert row["symbol"] == "AAA"
    assert row["ticker_direction_bias"] == BIAS_INVERSE_WATCH
    assert row["inverse_win_rate"] == 1.0


def test_ticker_memory_trusts_original_when_original_side_consistently_wins():
    outcomes = pd.DataFrame(
        {
            "symbol": ["AAA"] * 6,
            "original_return_bps": [50, 40, 35, 25, 30, 45],
            "inverse_return_bps": [-50, -40, -35, -25, -30, -45],
        }
    )
    memory = summarize_ticker_direction_memory(outcomes, config=TickerDirectionMemoryConfig(min_ticker_samples=5))

    assert memory.iloc[0]["ticker_direction_bias"] == BIAS_TRUST_ORIGINAL


def test_ticker_memory_is_insufficient_when_ticker_sample_is_sparse():
    outcomes = pd.DataFrame({"symbol": ["AAA"], "original_return_bps": [-100], "inverse_return_bps": [100]})
    memory = summarize_ticker_direction_memory(outcomes, config=TickerDirectionMemoryConfig(min_ticker_samples=5))

    assert memory.iloc[0]["ticker_direction_bias"] == BIAS_INSUFFICIENT_DATA
    assert memory.iloc[0]["ticker_direction_reason"] == "insufficient_ticker_samples"


def test_apply_ticker_memory_adds_candidate_evidence_fields():
    candidates = pd.DataFrame([candidate(symbol="AAA"), candidate(symbol="BBB")])
    memory = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "sample_count": 6,
                "inverse_advantage_bps": 80.0,
                "ticker_direction_bias": BIAS_INVERSE_WATCH,
                "ticker_direction_confidence": 0.7,
                "ticker_direction_reason": "inverse_side_has_ticker_edge",
            }
        ]
    )

    out = apply_ticker_direction_memory(candidates, memory)

    assert out[out["symbol"].eq("AAA")].iloc[0]["ticker_direction_bias"] == BIAS_INVERSE_WATCH
    assert out[out["symbol"].eq("BBB")].iloc[0]["ticker_direction_bias"] == BIAS_INSUFFICIENT_DATA


def test_direction_gate_inverse_watch_when_ticker_memory_prefers_inverse():
    result = evaluate_direction_gate(
        candidate(
            ticker_direction_bias=BIAS_INVERSE_WATCH,
            ticker_direction_confidence=0.75,
            ticker_direction_sample_count=8,
            ticker_direction_reason="inverse_side_has_ticker_edge",
        )
    )

    assert result["direction_decision"] == "direction_inverse_watch"
    assert result["direction_gate_pass"] is False
    assert result["direction_source"] == "ticker_direction_memory"


def test_direction_gate_keeps_ticker_original_support_as_evidence_only():
    result = evaluate_direction_gate(
        candidate(
            ticker_direction_bias=BIAS_TRUST_ORIGINAL,
            ticker_direction_confidence=0.74,
            ticker_direction_sample_count=8,
            ticker_direction_reason="original_side_has_ticker_edge",
        )
    )

    assert result["direction_decision"] == "direction_pass"
    assert "ticker_direction_memory_supports_original" in result["direction_supporting_reasons"]


def test_normalize_direction_outcomes_can_use_open_position_inversion_rows():
    frame = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "actual_side": "long",
                "entry_price": 100.0,
                "current_price": 99.0,
                "actual_plpc": -0.01,
                "simulated_opposite_pl": 10.0,
                "gross_basis": 1000.0,
            }
        ]
    )
    out = normalize_direction_outcomes(frame)

    assert out.iloc[0]["original_return_bps"] == -100.0
    assert out.iloc[0]["inverse_return_bps"] == 100.0


def test_ticker_direction_memory_runner_writes_reports(tmp_path: Path):
    diag = tmp_path / "data" / "trading" / "diagnostics"
    diag.mkdir(parents=True)
    pd.DataFrame(
        {
            "symbol": ["AAA"] * 6,
            "actual_return_bps": [-50, -40, -35, -25, -30, -45],
            "inverse_return_bps": [50, 40, 35, 25, 30, 45],
        }
    ).to_csv(diag / "trade_inverse_outcome_20260101_000000.csv", index=False)

    result = run_ticker_direction_memory(root=tmp_path, stamp="20260101_010000")

    assert result["status"] == "ok"
    assert result["inverse_watch"] == 1
    assert Path(result["csv_path"]).exists()
    assert Path(result["markdown_path"]).exists()
