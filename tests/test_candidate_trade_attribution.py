import pandas as pd

from stockml.diagnostics.candidate_trade_attribution import attribute_trades_to_candidates


def test_candidate_attribution_joins_by_candidate_id():
    result = attribute_trades_to_candidates(
        pd.DataFrame([{"trade_id": "t1", "symbol": "AAA", "candidate_id": "cand", "position_status": "closed"}]),
        pd.DataFrame([{"candidate_id": "cand", "symbol": "AAA", "candidate_rank": 3, "trade_quality_status": "approved"}]),
    )
    row = result.frame.iloc[0]
    assert row["join_quality"] == "high"
    assert row["candidate_rank"] == "3"
    assert row["trade_quality_status"] == "approved"
    assert result.summary["status"] == "ok"


def test_candidate_attribution_joins_by_client_order_id():
    result = attribute_trades_to_candidates(
        pd.DataFrame([{"trade_id": "t1", "symbol": "AAA", "client_order_id": "cid"}]),
        pd.DataFrame([{"client_order_id": "cid", "symbol": "AAA", "risk_adjusted_score": 0.7}]),
    )
    assert result.frame.iloc[0]["join_quality"] == "high"
    assert result.frame.iloc[0]["risk_adjusted_score"] == "0.7"


def test_candidate_attribution_uses_symbol_fallback_as_low_quality():
    result = attribute_trades_to_candidates(
        pd.DataFrame([{"trade_id": "t1", "symbol": "AAA"}]),
        pd.DataFrame([{"symbol": "AAA", "candidate_rank": 5}]),
    )
    assert result.frame.iloc[0]["join_quality"] == "low"
    assert result.summary["low_quality_matches"] == 1


def test_candidate_attribution_marks_missing_context():
    result = attribute_trades_to_candidates(
        pd.DataFrame([{"trade_id": "t1", "symbol": "AAA"}]),
        pd.DataFrame([{"symbol": "BBB", "candidate_rank": 5}]),
    )
    assert result.frame.iloc[0]["join_quality"] == "missing"
    assert result.frame.iloc[0]["join_warning"] == "candidate_context_missing"
    assert result.summary["status"] == "partial"


def test_candidate_attribution_empty_ledger_is_insufficient_data():
    result = attribute_trades_to_candidates(pd.DataFrame(), pd.DataFrame([{"symbol": "AAA"}]))
    assert result.summary["status"] == "insufficient_data"
    assert result.summary["trades"] == 0
