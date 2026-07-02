from __future__ import annotations

import pandas as pd

from stockml.candidates.short_side_policy import ShortSidePolicy
from stockml.trading.short_intelligence_decision import build_short_intelligence_decisions


def closed(rows=60, pnl=1.0, bps=10.0):
    return pd.DataFrame([{"symbol": f"S{i}", "side": "short", "realized_pnl_usd": pnl, "realized_net_bps": bps} for i in range(rows)])


def test_short_side_policy_disabled_makes_every_short_non_executable():
    out = build_short_intelligence_decisions(pd.DataFrame([{"symbol": "AAA", "trade_action": "Short", "forward_5d_return": -0.01}]), closed(), policy=ShortSidePolicy(enabled=False))
    assert bool(out.iloc[0]["paper_short_allowed"]) is False
    assert bool(out.iloc[0]["would_submit_order"]) is False
    assert bool(out.iloc[0]["diagnostics_only"]) is True
    assert "short_side_execution_disabled" in out.iloc[0]["blocking_reasons"]


def test_no_decision_plus_directional_short_is_research_only():
    out = build_short_intelligence_decisions(pd.DataFrame([{"symbol": "AAA", "source_trade_action": "No Decision", "directional_action": "Short"}]), closed(), policy=ShortSidePolicy(enabled=True, allow_shorts_in_validation=True))
    assert out.iloc[0]["short_decision"] == "research_only"
    assert out.iloc[0]["primary_reason"] == "source_trade_action_not_executable"


def test_insufficient_closed_short_sample_blocks_paper_eligible():
    out = build_short_intelligence_decisions(pd.DataFrame([{"symbol": "AAA", "trade_action": "Short", "forward_5d_return": -0.01}]), closed(rows=10), policy=ShortSidePolicy(enabled=True, allow_shorts_in_validation=True))
    assert "insufficient_short_trade_sample" in out.iloc[0]["blocking_reasons"]
    assert out.iloc[0]["short_decision"] != "paper_short_eligible"


def test_negative_short_pnl_blocks_paper_eligible():
    out = build_short_intelligence_decisions(pd.DataFrame([{"symbol": "AAA", "trade_action": "Short", "forward_5d_return": -0.01}]), closed(pnl=-1, bps=-10), policy=ShortSidePolicy(enabled=True, allow_shorts_in_validation=True))
    assert "short_negative_edge" in out.iloc[0]["blocking_reasons"]
    assert out.iloc[0]["short_decision"] == "inverse_watch"


def test_profit_factor_below_threshold_blocks_paper_eligible():
    trades = pd.DataFrame(
        [{"symbol": f"L{i}", "side": "short", "realized_pnl_usd": -2, "realized_net_bps": -20} for i in range(30)]
        + [{"symbol": f"W{i}", "side": "short", "realized_pnl_usd": 1, "realized_net_bps": 10} for i in range(30)]
    )
    out = build_short_intelligence_decisions(pd.DataFrame([{"symbol": "AAA", "trade_action": "Short", "forward_5d_return": -0.01}]), trades, policy=ShortSidePolicy(enabled=True, allow_shorts_in_validation=True))
    assert "short_profit_factor_below_threshold" in out.iloc[0]["blocking_reasons"]


def test_high_squeeze_risk_produces_inverse_watch():
    row = {"symbol": "AAA", "trade_action": "Short", "forward_5d_return": -0.01, "volatility_20d": 0.1, "gap_pct": 0.08, "return_5d": 0.2}
    out = build_short_intelligence_decisions(pd.DataFrame([row]), closed(), policy=ShortSidePolicy(enabled=True, allow_shorts_in_validation=True))
    assert out.iloc[0]["short_decision"] == "inverse_watch"
    assert out.iloc[0]["primary_reason"] == "squeeze_risk_high"


def test_inverse_long_outperforming_short_produces_inverse_watch():
    out = build_short_intelligence_decisions(pd.DataFrame([{"symbol": "AAA", "trade_action": "Short", "forward_5d_return": 0.02}]), closed(), policy=ShortSidePolicy(enabled=True, allow_shorts_in_validation=True))
    assert out.iloc[0]["short_decision"] == "inverse_watch"
    assert bool(out.iloc[0]["inverse_watch_flag"]) is True


def test_missing_forward_marks_produces_insufficient_data_warning():
    out = build_short_intelligence_decisions(pd.DataFrame([{"symbol": "AAA", "trade_action": "Short"}]), closed(), policy=ShortSidePolicy(enabled=True, allow_shorts_in_validation=True))
    assert out.iloc[0]["data_quality_status"] == "insufficient_data"
    assert "missing_forward_marks" in out.iloc[0]["blocking_reasons"]


def test_shortable_false_blocks_paper_eligible():
    out = build_short_intelligence_decisions(pd.DataFrame([{"symbol": "AAA", "trade_action": "Short", "forward_5d_return": -0.01, "shortable": False}]), closed(), policy=ShortSidePolicy(enabled=True, allow_shorts_in_validation=True))
    assert "not_shortable" in out.iloc[0]["blocking_reasons"]


def test_paper_short_eligible_only_when_required_conditions_pass_but_does_not_submit():
    out = build_short_intelligence_decisions(pd.DataFrame([{"symbol": "AAA", "trade_action": "Short", "forward_5d_return": -0.01, "shortable": True}]), closed(), policy=ShortSidePolicy(enabled=True, allow_shorts_in_validation=True))
    row = out.iloc[0]
    assert row["short_decision"] == "paper_short_eligible"
    assert bool(row["paper_short_allowed"]) is False
    assert bool(row["would_submit_order"]) is False
    assert bool(row["diagnostics_only"]) is True


def test_no_broker_submission_path_is_called():
    out = build_short_intelligence_decisions(pd.DataFrame([{"symbol": "AAA", "trade_action": "Short", "forward_5d_return": -0.01}]), closed())
    assert "broker_order_id" not in out.columns
