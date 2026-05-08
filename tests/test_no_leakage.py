from stockml.gold.target_engineering import leakage_columns, model_feature_columns


def test_leakage_columns_are_excluded_from_feature_matrix():
    columns = ["ticker", "return_5d", "target_return_5d", "future_return", "prediction_score", "trade_action"]
    features = model_feature_columns(columns)
    assert "return_5d" in features
    assert "target_return_5d" not in features
    assert "future_return" not in features
    assert "prediction_score" not in features
    assert "trade_action" not in features
    assert set(leakage_columns(columns)) == {"target_return_5d", "future_return", "prediction_score", "trade_action"}

