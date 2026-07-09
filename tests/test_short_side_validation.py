from __future__ import annotations

import pandas as pd

from stockml.diagnostics.short_side_validation import (
    MIN_SHORT_SAMPLES,
    build_short_side_validation_report,
    run_short_side_validation,
)


def _short(**overrides):
    row = {
        "symbol": "AAA",
        "side": "sell",
        "source_trade_action": "Short",
        "validated_expected_return_bps": 25,
        "sector": "Technology",
        "volatility_tier": "normal",
        "market_regime": "neutral",
        "primary_block_reason": "short_side_validation_required",
        "all_block_reasons": "short_side_validation_required",
    }
    row.update(overrides)
    return row


def test_short_side_validation_reports_core_metrics():
    frame, summary = build_short_side_validation_report(
        pd.DataFrame([
            _short(symbol="WIN", validated_expected_return_bps=35),
            _short(symbol="LOSS", validated_expected_return_bps=-20),
            {"symbol": "LONG", "side": "buy", "source_trade_action": "Long", "validated_expected_return_bps": 50},
        ])
    )

    assert summary["short_candidate_count"] == 2
    assert summary["source_approved_short_count"] == 2
    assert summary["short_win_rate"] == 0.5
    assert summary["short_profit_factor"] > 0
    assert summary["short_execution_allowed"] is False
    assert "insufficient_sample_size" in summary["decision"]
    assert {"summary", "by_sector", "by_volatility_tier", "by_market_regime", "candidate_detail"}.issubset(set(frame["section"]))


def test_negative_short_edge_remains_disabled():
    _, summary = build_short_side_validation_report(
        pd.DataFrame([_short(symbol=f"S{i}", validated_expected_return_bps=-5) for i in range(MIN_SHORT_SAMPLES)])
    )

    assert summary["short_expected_value_after_cost_bps"] < 0
    assert summary["short_execution_allowed"] is False
    assert "expected_return_not_positive_after_cost" in summary["decision"]


def test_short_squeeze_risk_blocks_even_positive_edge():
    rows = [_short(symbol=f"S{i}", validated_expected_return_bps=40, short_squeeze_risk_tier="low", walk_forward_status="pass") for i in range(MIN_SHORT_SAMPLES)]
    rows[0]["short_squeeze_risk_tier"] = "high"

    _, summary = build_short_side_validation_report(pd.DataFrame(rows))

    assert summary["short_squeeze_risk_flags"] == 1
    assert summary["short_execution_allowed"] is False
    assert "severe_short_squeeze_risk" in summary["decision"]


def test_short_execution_requires_walk_forward_survival():
    rows = [_short(symbol=f"S{i}", validated_expected_return_bps=40) for i in range(MIN_SHORT_SAMPLES)]

    _, summary = build_short_side_validation_report(pd.DataFrame(rows))

    assert summary["short_execution_allowed"] is False
    assert "walk_forward_not_proven" in summary["decision"]


def test_short_execution_allowed_only_when_all_acceptance_criteria_pass():
    rows = [_short(symbol=f"S{i}", validated_expected_return_bps=40, walk_forward_status="pass") for i in range(MIN_SHORT_SAMPLES)]

    _, summary = build_short_side_validation_report(pd.DataFrame(rows))

    assert summary["short_expected_value_after_cost_bps"] > 0
    assert summary["short_profit_factor"] > 1.1
    assert summary["short_execution_allowed"] is True


def test_runner_writes_outputs_without_enabling_trading(tmp_path):
    output = run_short_side_validation(
        pd.DataFrame([_short(symbol="AAA", validated_expected_return_bps=-20)]),
        output_dir=tmp_path,
        stamp="20260709_120000",
    )

    assert output.csv_path.exists()
    assert output.markdown_path.exists()
    assert output.summary["short_execution_allowed"] is False
    assert not any(path.name.startswith("08_alpaca_paper_order_results") for path in tmp_path.iterdir())
