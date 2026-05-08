import pandas as pd

from stockml.models.gold_loader import build_model_matrix, safe_feature_columns


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

