import pandas as pd

from stockml.diagnostics.missed_better_candidates import find_missed_better_candidates, normalize_candidates


def test_missed_better_candidates_flags_stronger_nonheld_candidate():
    ledger = pd.DataFrame([{"symbol": "AAA", "side": "long", "position_status": "open", "risk_adjusted_score": 0.01, "unrealised_pnl": -5}])
    positions = pd.DataFrame([{"symbol": "BBB", "side": "long", "unrealized_pl": -2, "risk_adjusted_score": 0.02}])
    candidates = pd.DataFrame([
        {"symbol": "AAA", "trade_quality_status": "approved", "risk_adjusted_score": 0.50, "candidate_rank": 1},
        {"symbol": "CCC", "side": "buy", "trade_quality_status": "approved", "risk_adjusted_score": 0.60, "candidate_rank": 2},
    ])
    result = find_missed_better_candidates(ledger, positions, candidates)
    assert result.summary["status"] == "ok"
    assert result.frame.iloc[0]["candidate_symbol"] == "CCC"
    assert "AAA" not in set(result.frame["candidate_symbol"])
    assert result.frame.iloc[0]["diagnostic_decision"] == "review_candidate"


def test_missed_better_candidates_excludes_rejected_and_ineligible_candidates():
    candidates = pd.DataFrame([
        {"symbol": "BAD", "trade_quality_status": "rejected", "risk_adjusted_score": 0.9},
        {"symbol": "OFF", "trade_quality_status": "approved", "order_eligible": False, "risk_adjusted_score": 0.9},
        {"symbol": "OK", "trade_quality_status": "reduced", "order_eligible": True, "risk_adjusted_score": 0.2},
    ])
    out = normalize_candidates(candidates)
    assert out["candidate_symbol"].tolist() == ["OK"]


def test_missed_better_candidates_without_baseline_reports_insufficient_but_lists_candidates():
    result = find_missed_better_candidates(pd.DataFrame(), pd.DataFrame(), pd.DataFrame([{"symbol": "CCC", "trade_quality_status": "approved", "risk_adjusted_score": 0.6}]))
    assert result.summary["status"] == "insufficient_data"
    assert result.frame.iloc[0]["candidate_symbol"] == "CCC"
    assert result.frame.iloc[0]["why_not_traded"] == "no_open_or_ledger_baseline_to_compare"


def test_missed_better_candidates_missing_candidate_pool_is_stable_schema():
    result = find_missed_better_candidates(pd.DataFrame([{"symbol": "AAA"}]), pd.DataFrame(), pd.DataFrame())
    expected = [
        "status", "baseline_symbol", "baseline_side", "baseline_source", "baseline_pnl", "baseline_score",
        "candidate_symbol", "candidate_side", "candidate_rank", "candidate_source", "candidate_id", "client_order_id",
        "cycle_id", "trade_quality_status", "order_eligible", "risk_tier", "expected_trade_return", "risk_adjusted_score",
        "model_score", "directional_expected_edge_bps", "edge_gap_bps", "why_not_traded", "diagnostic_decision",
    ]
    assert list(result.frame.columns) == expected
    assert result.frame.iloc[0]["status"] == "insufficient_data"
