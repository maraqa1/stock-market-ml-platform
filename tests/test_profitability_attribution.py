import pandas as pd

from stockml.diagnostics.profitability_attribution import build_profitability_attribution


def _ledger():
    return pd.DataFrame([
        {
            "trade_id": "t1", "symbol": "AAA", "side": "long", "candidate_source": "main_model",
            "strategy_mode": "multi_day", "event_session_mode": "regular_session", "actual_submission_session_mode": "regular_session",
            "position_status": "closed", "realised_pnl": 10.0, "realised_return_pct": 2.0,
            "lineage_quality": "high", "model_score": 0.9, "exit_reason": "take_profit",
        },
        {
            "trade_id": "t2", "symbol": "BBB", "side": "short", "candidate_source": "near_miss",
            "strategy_mode": "multi_day", "event_session_mode": "overnight_24_5", "actual_submission_session_mode": "overnight_24_5",
            "position_status": "closed", "realised_pnl": -4.0, "realised_return_pct": -1.0,
            "lineage_quality": "high", "model_score": 0.2, "exit_reason": "stop_loss",
        },
        {
            "trade_id": "t3", "symbol": "CCC", "side": "long", "candidate_source": "main_model",
            "strategy_mode": "same_day", "event_session_mode": "regular_session", "actual_submission_session_mode": "regular_session",
            "position_status": "open", "unrealised_pnl": 3.0, "unrealised_return_pct": 0.5,
            "lineage_quality": "low", "model_score": 0.5, "exit_reason": "",
        },
    ])


def test_empty_ledger_returns_not_enough_trades():
    result = build_profitability_attribution(pd.DataFrame())
    assert result.attribution.empty
    assert result.summary["attribution_decision"] == "NOT_ENOUGH_TRADES"


def test_all_bucket_sums_realised_and_unrealised_pnl():
    result = build_profitability_attribution(_ledger())
    all_row = result.attribution[(result.attribution["dimension"] == "ALL") & (result.attribution["bucket"] == "ALL")].iloc[0]
    assert all_row["trades"] == 3
    assert all_row["realised_pnl"] == 6.0
    assert all_row["unrealised_pnl"] == 3.0
    assert all_row["total_pnl"] == 9.0


def test_side_attribution_separates_long_and_short():
    result = build_profitability_attribution(_ledger())
    side_rows = result.attribution[result.attribution["dimension"] == "side"].set_index("bucket")
    assert side_rows.loc["long", "trades"] == 2
    assert side_rows.loc["short", "trades"] == 1
    assert side_rows.loc["short", "realised_pnl"] == -4.0


def test_session_and_candidate_source_attribution_present():
    result = build_profitability_attribution(_ledger())
    dims = set(result.attribution["dimension"])
    assert "event_session_mode" in dims
    assert "candidate_source" in dims
    assert "model_score_bucket" in dims


def test_low_confidence_trades_make_partial_decision():
    result = build_profitability_attribution(_ledger())
    assert result.summary["low_confidence_trades"] == 1
    assert result.summary["attribution_decision"] == "PARTIAL_ATTRIBUTION_ONLY"


def test_all_insufficient_data_not_fit():
    ledger = pd.DataFrame([{"trade_id": "x", "position_status": "insufficient_data", "lineage_quality": "high"}])
    result = build_profitability_attribution(ledger)
    assert result.summary["attribution_decision"] == "NOT_FIT_INSUFFICIENT_DATA"
