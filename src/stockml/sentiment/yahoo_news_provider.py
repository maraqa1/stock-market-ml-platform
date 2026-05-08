from __future__ import annotations

from typing import Dict, List

from stockml.sentiment.news_provider_base import NewsProviderBase


class YahooNewsProvider(NewsProviderBase):
    source_name = "yahoo"

    def fetch_articles(self, ticker: str) -> List[Dict[str, object]]:
        try:
            import yfinance as yf

            news = yf.Ticker(str(ticker).upper().strip()).news
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
        return [_normalize_article(article) for article in news] if isinstance(news, list) else []


def _normalize_article(article: Dict[str, object]) -> Dict[str, object]:
    content = article.get("content") if isinstance(article.get("content"), dict) else {}
    title = article.get("title") or content.get("title") or ""
    summary = article.get("summary") or content.get("summary") or content.get("description") or ""
    provider = content.get("provider") if isinstance(content.get("provider"), dict) else {}
    publisher = article.get("publisher") or provider.get("displayName") or ""
    provider_publish_time = (
        article.get("providerPublishTime")
        or article.get("publishTime")
        or article.get("pubDate")
        or content.get("pubDate")
        or content.get("displayTime")
    )
    canonical = content.get("canonicalUrl") if isinstance(content.get("canonicalUrl"), dict) else {}
    link = article.get("link") or canonical.get("url") or ""
    return {
        **article,
        "title": title,
        "summary": summary,
        "publisher": publisher,
        "providerPublishTime": provider_publish_time,
        "link": link,
    }
