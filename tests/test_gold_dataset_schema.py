import pandas as pd

from stockml.gold.build_gold_dataset import GOLD_COLUMNS, build_gold_dataset_from_frames


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
