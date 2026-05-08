import pandas as pd

from stockml.models.ranking_model import train_predict_from_gold


def synthetic_gold():
    dates = pd.date_range("2022-01-03", periods=360, freq="B")
    rows = []
    for ticker_no in range(8):
        ticker = f"T{ticker_no}"
        for i, date in enumerate(dates):
            signal = (ticker_no + 1) / 10 + (i % 20) / 100
            target_return = signal / 20 if i < len(dates) - 10 else None
            rows.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "company": ticker,
                    "exchange": "NASDAQ",
                    "sector": "Tech" if ticker_no < 4 else "Health",
                    "industry": "Software",
                    "close": 10 + signal,
                    "volume": 100000 + i,
                    "return_5d": signal,
                    "return_20d": signal * 1.2,
                    "selection_score": signal,
                    "candidate_rank_overall": ticker_no + 1,
                    "candidate_rank_by_sector": ticker_no + 1,
                    "target_return_5d": target_return,
                    "target_top_quintile_5d": ticker_no >= 6 if target_return is not None else None,
                    "target_trade_label_5d": "Long" if ticker_no >= 6 else "Neutral",
                }
            )
    return pd.DataFrame(rows)


def test_train_predict_from_gold_writes_expected_artifact_frames():
    artifacts = train_predict_from_gold(synthetic_gold(), top_n=5)
    assert not artifacts.predictions.empty
    assert not artifacts.signal_table.empty
    assert set(["decision_grade", "selected_model", "gold_input_rows"]).issubset(artifacts.model_status.columns)
    assert "icir_5d" in artifacts.validation_leaderboard.columns
    assert "turnover_adjusted_avg_gain_5d" in artifacts.validation_leaderboard.columns
    assert "feature" in artifacts.feature_importance.columns


def test_diagnostic_paper_mode_can_emit_research_candidates(monkeypatch):
    monkeypatch.setenv("STOCKML_ALLOW_DIAGNOSTIC_PAPER_TRADES", "true")
    artifacts = train_predict_from_gold(synthetic_gold(), top_n=5)
    if artifacts.model_status.iloc[0]["decision_grade"] == "diagnostic_only":
        assert "diagnostic_paper_mode" in artifacts.model_status.columns
        assert artifacts.signal_table["trade_action"].isin(["Long", "Short"]).any()
        research = artifacts.signal_table[artifacts.signal_table["trade_action"].isin(["Long", "Short"])]
        assert research["signal_reason"].str.contains("diagnostic_paper_candidate").all()
