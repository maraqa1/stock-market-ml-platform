from datetime import datetime, timezone

from stockml.sentiment.build_sentiment_panel import aggregate_articles
from stockml.sentiment.sentiment_schema import SENTIMENT_COLUMNS


def test_sentiment_schema_from_synthetic_articles():
    article = {
        "providerPublishTime": int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp()),
        "title": "Company beats profit expectations in strong quarter",
    }
    panel = aggregate_articles("AAA", [article], "test")
    assert list(panel.columns) == SENTIMENT_COLUMNS
    assert panel.loc[0, "article_count"] == 1
    assert panel.loc[0, "sentiment_positive_count"] == 1

