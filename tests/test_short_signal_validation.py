from __future__ import annotations

import pandas as pd

from stockml.diagnostics.short_signal_validation import run_short_signal_validation


def test_insufficient_sample_disables_short_validation(tmp_path):
    candidates = pd.DataFrame([{"symbol": "AAA", "trade_action": "Short", "forward_5d_return": -0.01}])
    closed = pd.DataFrame([{"symbol": "AAA", "side": "short", "realized_pnl_usd": 1, "realized_net_bps": 10}])
    outputs = run_short_signal_validation(candidates, closed, output_dir=tmp_path, stamp="x")
    assert outputs.summary["short_policy_recommendation"] == "short_disabled_insufficient_data"
    assert "insufficient_data" in outputs.summary["warnings"]


def test_negative_short_edge_disables_short_execution(tmp_path):
    candidates = pd.DataFrame(
        [{"symbol": f"S{i}", "trade_action": "Short", "forward_5d_return": 0.01, "sector": "Tech"} for i in range(60)]
    )
    closed = pd.DataFrame(
        [{"symbol": f"S{i}", "side": "short", "realized_pnl_usd": -1, "realized_net_bps": -10} for i in range(60)]
    )
    outputs = run_short_signal_validation(candidates, closed, output_dir=tmp_path, stamp="x")
    assert outputs.summary["short_policy_recommendation"] == "short_disabled_negative_edge"
    assert outputs.summary["short_win_rate"] == 0.0


def test_short_candidates_remain_research_only_by_default(tmp_path):
    candidates = pd.DataFrame([{"symbol": "AAA", "trade_action": "Short", "forward_5d_return": -0.02}])
    outputs = run_short_signal_validation(candidates, pd.DataFrame(), output_dir=tmp_path, stamp="x")
    frame = pd.read_csv(outputs.validation_path)
    assert frame.iloc[0]["short_validation_status"] == "short_research_only"


def test_no_order_submission_fields_are_added(tmp_path):
    candidates = pd.DataFrame([{"symbol": "AAA", "side": "sell", "forward_5d_return": -0.02}])
    outputs = run_short_signal_validation(candidates, pd.DataFrame(), output_dir=tmp_path, stamp="x")
    frame = pd.read_csv(outputs.validation_path)
    assert "order_id" not in frame.columns
    assert "submitted" not in frame.columns
