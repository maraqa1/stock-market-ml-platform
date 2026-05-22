import pandas as pd

from stockml.models.build_model_outputs import _combine_shard_artifacts
from stockml.models.gold_loader import load_gold_dataset
from stockml.models.ranking_model import ModelArtifacts, config_from_env, train_predict_from_gold


def synthetic_gold():
    dates = pd.date_range("2022-01-03", periods=700, freq="B")
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


def test_gold_loader_shards_cover_all_tickers_without_overlap(tmp_path):
    path = tmp_path / "gold.csv"
    frame = synthetic_gold()
    frame.to_csv(path, index=False)

    left = load_gold_dataset(path, shard_count=2, shard_index=0)
    right = load_gold_dataset(path, shard_count=2, shard_index=1)
    left_tickers = set(left["ticker"].unique())
    right_tickers = set(right["ticker"].unique())

    assert left_tickers
    assert right_tickers
    assert left_tickers.isdisjoint(right_tickers)
    assert left_tickers | right_tickers == set(frame["ticker"].unique())


def test_train_predict_from_gold_writes_ranking_artifacts():
    artifacts = train_predict_from_gold(synthetic_gold(), top_n=5)
    assert "rank_overall" in artifacts.signal_table.columns
    assert "model_score" in artifacts.signal_table.columns
    assert "directional_action" in artifacts.signal_table.columns
    assert "directional_strength" in artifacts.signal_table.columns
    assert "included" in artifacts.feature_audit.columns
    assert "exclusion_reason" in artifacts.rejected_features.columns
    assert isinstance(artifacts.model_config, dict)


def test_train_predict_from_gold_live_signal_mode_skips_walk_forward():
    artifacts = train_predict_from_gold(synthetic_gold(), top_n=5, live_signal_mode=True, baseline_only=True)
    assert artifacts.walk_forward_predictions.empty
    assert artifacts.fold_metrics.empty
    assert artifacts.model_status.iloc[0]["decision_grade"] == "decision_grade"
    assert artifacts.model_status.iloc[0]["reason"] == "live_signal_mode_validation_skipped"
    assert artifacts.model_status.iloc[0]["selected_model"] == "equal_weight_momentum_composite"


def test_live_signal_model_emits_short_side_by_default(monkeypatch):
    monkeypatch.delenv("STOCKML_ALLOW_SHORT_SELLING", raising=False)

    artifacts = train_predict_from_gold(synthetic_gold(), top_n=5, live_signal_mode=True, baseline_only=True)

    assert config_from_env().allow_short_selling is True
    assert "Short" in set(artifacts.signal_table["trade_action"])
    assert "Short" in set(artifacts.signal_table["directional_action"])
    assert not artifacts.top_short.empty


def test_shard_combine_does_not_require_walk_forward_history():
    base = train_predict_from_gold(synthetic_gold(), top_n=5)
    shards = []
    for shard_index in range(2):
        shard = ModelArtifacts(
            predictions=base.predictions.copy(),
            signal_table=base.signal_table.copy(),
            top_long=base.top_long.copy(),
            top_short=base.top_short.copy(),
            validation_leaderboard=base.validation_leaderboard.copy(),
            bucket_performance=base.bucket_performance.copy(),
            feature_importance=base.feature_importance.copy(),
            model_status=base.model_status.copy(),
            data_dictionary=base.data_dictionary.copy(),
            walk_forward_predictions=pd.DataFrame(columns=base.walk_forward_predictions.columns),
            fold_metrics=base.fold_metrics.copy(),
            feature_audit=base.feature_audit.copy(),
            rejected_features=base.rejected_features.copy(),
            model_config=dict(base.model_config),
        )
        shard.signal_table["ticker"] = shard.signal_table["ticker"].astype(str) + f"S{shard_index}"
        shard.predictions = shard.signal_table.copy()
        shards.append(shard)

    combined = _combine_shard_artifacts(shards, top_n=5)

    assert combined.walk_forward_predictions.empty
    assert "model_shard" in combined.signal_table.columns
    assert "directional_action" in combined.signal_table.columns
    assert combined.model_config["sharded_model"] is True
