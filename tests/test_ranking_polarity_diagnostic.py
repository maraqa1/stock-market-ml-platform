import pandas as pd

from stockml.diagnostics.ranking_polarity_diagnostic import build_ranking_polarity


def test_rank_polarity_diagnostic_detects_ascending_descending_interpretation():
    frame = pd.DataFrame([
        {"ticker": "AAA", "rank_overall": 1, "forward_5d_return": 0.05},
        {"ticker": "BBB", "rank_overall": 2, "forward_5d_return": 0.04},
        {"ticker": "CCC", "rank_overall": 9, "forward_5d_return": -0.03},
        {"ticker": "DDD", "rank_overall": 10, "forward_5d_return": -0.04},
    ])
    out = build_ranking_polarity(frame, cost=0.0)
    current = out[out["strategy"].eq("current_top_long_bottom_short")].iloc[0]
    inverse = out[out["strategy"].eq("inverse_top_short_bottom_long")].iloc[0]
    assert current["net_after_cost"] > inverse["net_after_cost"]
    assert out["rank_interpretation"].iloc[0] == "ascending_rank_best"
    assert bool(out["polarity_bug_likely"].iloc[0]) is False


def test_rank_polarity_diagnostic_flags_possible_inversion():
    frame = pd.DataFrame([
        {"ticker": "AAA", "rank_overall": 1, "forward_5d_return": -0.05},
        {"ticker": "BBB", "rank_overall": 2, "forward_5d_return": -0.04},
        {"ticker": "CCC", "rank_overall": 9, "forward_5d_return": 0.03},
        {"ticker": "DDD", "rank_overall": 10, "forward_5d_return": 0.04},
    ])
    out = build_ranking_polarity(frame, cost=0.0)
    assert bool(out["polarity_bug_likely"].iloc[0]) is True
