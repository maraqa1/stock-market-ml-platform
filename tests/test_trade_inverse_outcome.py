from __future__ import annotations

import pandas as pd

from stockml.diagnostics.trade_inverse_outcome import REPORT_COLUMNS, build_trade_inverse_outcome, write_trade_inverse_outcome


def test_trade_inverse_outcome_reverses_closed_trade_pnl():
    ledger = pd.DataFrame([
        {
            "symbol": "AAA",
            "side": "short",
            "entry_time": "2026-06-30T13:00:00+00:00",
            "exit_time": "2026-06-30T14:00:00+00:00",
            "entry_price": 10.0,
            "exit_price": 11.0,
            "entry_quantity": 5,
            "position_status": "closed",
            "realised_pnl": -5.0,
            "realised_return_pct": -10.0,
            "candidate_source": "latest_candidate_pool",
            "strategy_mode": "paper_autopilot",
            "actual_submission_session_mode": "regular_session",
            "exit_reason": "operator_close",
            "lineage_quality": "medium",
        }
    ])
    result = build_trade_inverse_outcome(ledger)
    row = result.report.iloc[0]
    assert list(result.report.columns) == REPORT_COLUMNS
    assert row["actual_side"] == "short"
    assert row["inverse_side"] == "long"
    assert row["actual_pnl"] == -5.0
    assert row["inverse_pnl_before_incremental_costs"] == 5.0
    assert row["inversion_evidence"] == "inverse_would_win"
    summary = result.summary.iloc[0]
    assert summary["trade_count"] == 1
    assert summary["actual_total_pnl"] == -5.0
    assert summary["inverse_total_pnl_before_incremental_costs"] == 5.0
    assert bool(summary["all_actual_losers"]) is True
    assert "do_not_auto_reverse_small_sample" in summary["recommended_action"]


def test_trade_inverse_outcome_ignores_open_trades():
    ledger = pd.DataFrame([
        {"symbol": "AAA", "side": "long", "position_status": "open", "realised_pnl": 0},
    ])
    result = build_trade_inverse_outcome(ledger)
    assert result.report.empty
    assert result.summary.iloc[0]["recommended_action"] == "insufficient_data_no_closed_trades"


def test_trade_inverse_outcome_writes_report(tmp_path):
    ledger = pd.DataFrame([
        {"symbol": "AAA", "side": "long", "position_status": "closed", "realised_pnl": 2.5, "realised_return_pct": 1.0},
    ])
    written = write_trade_inverse_outcome(build_trade_inverse_outcome(ledger), out_stamp="20260630_120000", output_dir=tmp_path)
    assert written.report_path.exists()
    assert written.summary_path.exists()
    assert pd.read_csv(written.report_path).iloc[0]["inverse_pnl_before_incremental_costs"] == -2.5
