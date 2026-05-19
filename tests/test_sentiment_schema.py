from datetime import datetime, timezone

from stockml.sentiment.build_sentiment_panel import aggregate_articles
from stockml.sentiment.build_sentiment_panel import _combine_provider_panels
from stockml.sentiment.cnbc_news_provider import _matches_ticker
from stockml.sentiment.eodhd_news_provider import EodhdNewsProvider, _normalize_eodhd_article
from stockml.sentiment.provider_factory import sentiment_providers_from_name
from stockml.sentiment.sentiment_schema import SENTIMENT_COLUMNS
from stockml.sentiment.yahoo_news_provider import _normalize_article


def test_sentiment_schema_from_synthetic_articles():
    article = {
        "providerPublishTime": int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp()),
        "title": "Company beats profit expectations in strong quarter",
    }
    panel = aggregate_articles("AAA", [article], "test")
    assert list(panel.columns) == SENTIMENT_COLUMNS
    assert panel.loc[0, "article_count"] == 1
    assert panel.loc[0, "sentiment_positive_count"] == 1


def test_yahoo_nested_article_shape_is_normalized_and_scored():
    article = _normalize_article(
        {
            "content": {
                "title": "FLEX shares rally after strong profit outlook",
                "summary": "Analysts upgrade the company.",
                "pubDate": "2026-05-08T12:30:00Z",
                "provider": {"displayName": "Yahoo Finance"},
                "canonicalUrl": {"url": "https://finance.yahoo.com/news/example"},
            }
        }
    )
    panel = aggregate_articles("FLEX", [article], "yahoo")
    assert panel.loc[0, "article_count"] == 1
    assert panel.loc[0, "sentiment_status"] == "ok"
    assert panel.loc[0, "sentiment_score_mean"] > 0


def test_cnbc_ticker_matching_is_exact_token():
    article = {"title": "AAPL shares rally after earnings beat", "summary": ""}
    assert _matches_ticker(article, "AAPL")
    assert not _matches_ticker(article, "APP")


def test_combines_provider_sentiment_without_fabricating_rows():
    article = {
        "providerPublishTime": int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp()),
        "title": "MSFT upgrade looks strong",
    }
    yahoo = aggregate_articles("MSFT", [article], "yahoo")
    cnbc = aggregate_articles("MSFT", [article], "cnbc_rss")
    combined = _combine_provider_panels("MSFT", [yahoo, cnbc], [])
    assert list(combined.columns) == SENTIMENT_COLUMNS
    assert combined.loc[0, "article_count"] == 2
    assert combined.loc[0, "sentiment_source"] == "cnbc_rss+yahoo"


def test_eodhd_article_sentiment_is_normalized_and_scored():
    article = _normalize_eodhd_article(
        {
            "date": "2026-05-19T12:00:00+00:00",
            "title": "AAPL shares rally",
            "content": "Provider sentiment is positive.",
            "sentiment": {"polarity": 0.8},
            "link": "https://example.com",
        }
    )

    panel = aggregate_articles("AAPL", [article], "eodhd_news")

    assert panel.loc[0, "article_count"] == 1
    assert panel.loc[0, "sentiment_source"] == "eodhd_news"
    assert panel.loc[0, "sentiment_score_mean"] == 0.8


def test_sentiment_provider_factory_selects_eodhd():
    providers = sentiment_providers_from_name("eodhd")

    assert len(providers) == 1
    assert isinstance(providers[0], EodhdNewsProvider)
