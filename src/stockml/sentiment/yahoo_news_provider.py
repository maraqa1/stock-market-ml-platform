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
        return news if isinstance(news, list) else []
