from __future__ import annotations

import pandas as pd

from stockml.diagnostics.short_candidate_outcome import (
    build_inverse_long_comparison,
    build_short_candidate_outcomes,
    summarize_short_bucket_performance,
)


def test_bottom_ranked_candidate_short_return_is_negative_forward_return():
    out = build_short_candidate_outcomes(pd.DataFrame([{"symbol": "AAA", "trade_action": "Short", "forward_5d_return": 0.03}]), estimated_spread_cost_bps=0, estimated_slippage_bps=0)
    assert out.iloc[0]["short_return_5d_bps"] == -300


def test_short_costs_reduce_net_return():
    out = build_short_candidate_outcomes(pd.DataFrame([{"symbol": "AAA", "side": "sell", "forward_5d_return": -0.03}]), estimated_spread_cost_bps=5, estimated_slippage_bps=7, borrow_cost_estimate_bps=3)
    assert out.iloc[0]["short_return_5d_bps"] == 300
    assert out.iloc[0]["net_short_return_bps"] == 285


def test_short_bucket_performance_calculates_win_rate_and_profit_factor():
    outcomes = pd.DataFrame(
        [
            {"symbol": "A", "rank_overall": 99, "net_short_return_bps": 100, "sector": "Tech"},
            {"symbol": "B", "rank_overall": 98, "net_short_return_bps": -50, "sector": "Tech"},
            {"symbol": "C", "rank_overall": 97, "net_short_return_bps": -50, "sector": "Tech"},
        ]
    )
    buckets = summarize_short_bucket_performance(outcomes)
    sector = buckets[buckets["bucket"].eq("sector")].iloc[0]
    assert sector["win_rate"] == 1 / 3
    assert sector["profit_factor"] == 1.0


def test_inverse_long_comparison_flips_sign():
    outcomes = build_short_candidate_outcomes(pd.DataFrame([{"symbol": "AAA", "side": "sell", "forward_5d_return": 0.02}]), estimated_spread_cost_bps=0, estimated_slippage_bps=0)
    inverse = build_inverse_long_comparison(outcomes)
    assert inverse.iloc[0]["short_net_pnl_bps"] == -200
    assert inverse.iloc[0]["inverse_long_net_pnl_bps"] == 200
    assert bool(inverse.iloc[0]["inverse_outperforms"]) is True
