import pandas as pd

from stockml.models.gold_loader import build_model_matrix, latest_gold_file, load_gold_dataset, safe_feature_columns


def test_safe_feature_columns_exclude_targets_and_outputs():
    columns = [
        "date",
        "ticker",
        "return_5d",
        "selection_score",
        "target_return_5d",
        "target_top_quintile_5d",
        "prediction_score",
        "signal_reason",
        "trade_action",
    ]
    features = safe_feature_columns(columns)
    assert "return_5d" in features
    assert "selection_score" in features
    assert "target_return_5d" not in features
    assert "prediction_score" not in features
    assert "signal_reason" not in features
    assert "trade_action" not in features


def test_model_matrix_uses_gold_numeric_features_only():
    gold = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=8),
            "ticker": ["AAA", "BBB"] * 4,
            "company": ["A", "B"] * 4,
            "return_5d": [0.01, -0.01, 0.02, -0.02, 0.03, -0.03, 0.04, -0.04],
            "selection_score": [0.8, 0.2, 0.7, 0.3, 0.9, 0.1, 0.6, 0.4],
            "target_return_5d": [0.03, -0.02, 0.04, -0.01, 0.02, -0.03, 0.05, -0.04],
            "target_top_quintile_5d": [1, 0, 1, 0, 1, 0, 1, 0],
            "target_trade_label_5d": ["Long", "Neutral"] * 4,
        }
    )
    x, y, cols = build_model_matrix(gold)
    assert "return_5d" in cols
    assert "selection_score" in cols
    assert "target_return_5d" not in cols
    assert len(x) == len(y) == 8


def test_load_gold_dataset_filters_shard_from_file(tmp_path):
    path = tmp_path / "gold.csv"
    pd.DataFrame(
        [
            {
                "date": "2024-01-01",
                "ticker": ticker,
                "target_return_5d": 0.01,
                "target_trade_label_5d": "Neutral",
            }
            for ticker in ["AAA", "BBB", "CCC", "DDD"]
        ]
    ).to_csv(path, index=False)

    shard_0 = load_gold_dataset(path, shard_count=2, shard_index=0)
    shard_1 = load_gold_dataset(path, shard_count=2, shard_index=1)
    combined = sorted(set(shard_0["ticker"]) | set(shard_1["ticker"]))
    assert combined == ["AAA", "BBB", "CCC", "DDD"]
    assert set(shard_0["ticker"]).isdisjoint(set(shard_1["ticker"]))


def test_latest_gold_file_prefers_v2_master_dataset(tmp_path):
    legacy = tmp_path / "06_us_gold_ml_dataset_20240101_000000.csv"
    v2 = tmp_path / "gold_stock_decision_daily_20240101_000000.csv"
    legacy.write_text("date,ticker,target_return_5d,target_trade_label_5d\n", encoding="utf-8")
    v2.write_text("date,ticker,target_return_5d,target_trade_label_5d\n", encoding="utf-8")

    assert latest_gold_file(tmp_path) == v2
