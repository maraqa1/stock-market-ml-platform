import pandas as pd

from stockml.gold.build_gold_dataset import GOLD_COLUMNS, _read_feature_panel, build_gold_dataset, build_gold_dataset_from_frames


def test_gold_dataset_required_columns():
    dates = pd.date_range("2024-01-01", periods=30, freq="B")
    features = pd.DataFrame(
        [
            {
                "date": date,
                "ticker": ticker,
                "company": ticker,
                "exchange": "NASDAQ",
                "sector": "Tech",
                "industry": "Software",
                "open": 10 + i,
                "high": 11 + i,
                "low": 9 + i,
                "close": 10 + i,
                "adj_close": 10 + i,
                "volume": 1000,
                "feature_missing_ratio": 0,
                "liquidity_score": 0.8,
                "sector_relative_momentum_score": 0.7,
                "volume_confirmation_score": 0.6,
            }
            for ticker in ["AAA", "BBB", "CCC", "DDD", "EEE"]
            for i, date in enumerate(dates)
        ]
    )
    gold = build_gold_dataset_from_frames(features)
    assert set(GOLD_COLUMNS).issubset(gold.columns)
    assert "target_vol_adjusted_return_5d" in gold.columns
    assert "target_decay_weighted_return_5d" in gold.columns
    assert "target_trade_label_tier_5d" in gold.columns
    assert {"Long", "Short", "Neutral"}.intersection(set(gold["target_trade_label_5d"]))


def test_gold_dataset_deduplicates_sentiment_before_merge():
    dates = pd.date_range("2024-01-01", periods=8, freq="B")
    features = pd.DataFrame(
        [
            {
                "date": date,
                "ticker": ticker,
                "company": ticker,
                "exchange": "NASDAQ",
                "sector": "Tech",
                "industry": "Software",
                "open": 10 + i,
                "high": 11 + i,
                "low": 9 + i,
                "close": 10 + i,
                "adj_close": 10 + i,
                "volume": 1000,
                "feature_missing_ratio": 0,
                "liquidity_score": 0.8,
                "sector_relative_momentum_score": 0.7,
                "volume_confirmation_score": 0.6,
            }
            for ticker in ["AAA", "BBB"]
            for i, date in enumerate(dates)
        ]
    )
    sentiment = pd.DataFrame(
        [
            {"date": dates[0], "ticker": "AAA", "article_count": 1, "sentiment_score_mean": 0.1},
            {"date": dates[0], "ticker": "AAA", "article_count": 2, "sentiment_score_mean": 0.2},
        ]
    )

    gold = build_gold_dataset_from_frames(features, sentiment)
    assert len(gold) == len(features)


def test_gold_dataset_fills_categorical_sentiment_defaults():
    dates = pd.date_range("2024-01-01", periods=8, freq="B")
    features = pd.DataFrame(
        [
            {
                "date": date,
                "ticker": "AAA",
                "company": "AAA",
                "exchange": "NASDAQ",
                "sector": "Tech",
                "industry": "Software",
                "open": 10 + i,
                "high": 11 + i,
                "low": 9 + i,
                "close": 10 + i,
                "adj_close": 10 + i,
                "volume": 1000,
                "feature_missing_ratio": 0,
                "liquidity_score": 0.8,
                "sector_relative_momentum_score": 0.7,
                "volume_confirmation_score": 0.6,
            }
            for i, date in enumerate(dates)
        ]
    )
    sentiment = pd.DataFrame(
        [
            {
                "date": dates[0],
                "ticker": "AAA",
                "article_count": pd.NA,
                "sentiment_score_mean": pd.NA,
                "sentiment_status": pd.NA,
                "sentiment_source": pd.NA,
            }
        ]
    )
    sentiment["sentiment_status"] = pd.Categorical(sentiment["sentiment_status"], categories=["ok"])
    sentiment["sentiment_source"] = pd.Categorical(sentiment["sentiment_source"], categories=["eodhd"])

    gold = build_gold_dataset_from_frames(features, sentiment)

    assert "unavailable" in set(gold["sentiment_status"].astype(str))
    assert "none" in set(gold["sentiment_source"].astype(str))


def test_read_feature_panel_uses_only_gold_input_columns(tmp_path):
    path = tmp_path / "features.csv"
    pd.DataFrame(
        [
            {
                "date": "2024-01-01",
                "ticker": "AAA",
                "adj_close": 10.0,
                "feature_missing_ratio": 0.0,
                "unused_debug_column": "drop-me",
            }
        ]
    ).to_csv(path, index=False)

    frame = _read_feature_panel(path)
    assert "unused_debug_column" not in frame.columns
    assert {"date", "ticker", "adj_close", "feature_missing_ratio"}.issubset(frame.columns)


def test_sharded_gold_build_writes_complete_dataset(tmp_path, monkeypatch):
    for attr, name in [
        ("GOLD_DIR", "gold"),
        ("INTERIM_DIR", "interim"),
        ("PORTAL_OUTPUTS_DIR", "portal_outputs"),
    ]:
        path = tmp_path / name
        path.mkdir()
        monkeypatch.setattr(f"stockml.gold.build_gold_dataset.{attr}", path)

    feature_path = tmp_path / "features.csv"
    dates = pd.date_range("2024-01-01", periods=14, freq="B")
    pd.DataFrame(
        [
            {
                "date": date,
                "ticker": ticker,
                "company": ticker,
                "exchange": "NASDAQ",
                "sector": "Tech",
                "industry": "Software",
                "open": 10 + i,
                "high": 11 + i,
                "low": 9 + i,
                "close": 10 + i,
                "adj_close": 10 + i,
                "volume": 1000,
                "feature_missing_ratio": 0,
                "liquidity_score": 0.8,
                "sector_relative_momentum_score": 0.7,
                "volume_confirmation_score": 0.6,
            }
            for ticker in ["AAA", "BBB", "CCC"]
            for i, date in enumerate(dates)
        ]
    ).to_csv(feature_path, index=False)

    paths = build_gold_dataset(
        exchange="NASDAQ",
        feature_file=feature_path,
        skip_sentiment=True,
        shard_rows=15,
    )
    gold = pd.read_csv(paths["gold_dataset"])
    assert len(gold) == 42
    assert set(GOLD_COLUMNS).issubset(gold.columns)


def test_sharded_gold_build_merges_sentiment_by_date_window(tmp_path, monkeypatch):
    for attr, name in [
        ("GOLD_DIR", "gold"),
        ("INTERIM_DIR", "interim"),
        ("PORTAL_OUTPUTS_DIR", "portal_outputs"),
    ]:
        path = tmp_path / name
        path.mkdir()
        monkeypatch.setattr(f"stockml.gold.build_gold_dataset.{attr}", path)

    feature_path = tmp_path / "features.csv"
    sentiment_path = tmp_path / "sentiment.csv"
    dates = pd.date_range("2024-01-01", periods=14, freq="B")
    pd.DataFrame(
        [
            {
                "date": date,
                "ticker": ticker,
                "company": ticker,
                "exchange": "NASDAQ",
                "sector": "Tech",
                "industry": "Software",
                "open": 10 + i,
                "high": 11 + i,
                "low": 9 + i,
                "close": 10 + i,
                "adj_close": 10 + i,
                "volume": 1000,
                "feature_missing_ratio": 0,
                "liquidity_score": 0.8,
                "sector_relative_momentum_score": 0.7,
                "volume_confirmation_score": 0.6,
            }
            for ticker in ["AAA", "BBB"]
            for i, date in enumerate(dates)
        ]
    ).to_csv(feature_path, index=False)
    pd.DataFrame(
        [
            {
                "date": dates[2],
                "ticker": "AAA",
                "article_count": 7,
                "sentiment_score_mean": 0.5,
                "sentiment_status": "ok",
                "sentiment_source": "yahoo",
            }
        ]
    ).to_csv(sentiment_path, index=False)

    paths = build_gold_dataset(
        exchange="NASDAQ",
        feature_file=feature_path,
        sentiment_file=sentiment_path,
        skip_sentiment=False,
        shard_rows=10,
    )
    gold = pd.read_csv(paths["gold_dataset"])
    gold["date"] = pd.to_datetime(gold["date"], errors="coerce")
    row = gold[(gold["ticker"].eq("AAA")) & (gold["date"].eq(dates[2]))]

    assert int(row.iloc[0]["article_count"]) == 7
    assert row.iloc[0]["sentiment_status"] == "ok"
