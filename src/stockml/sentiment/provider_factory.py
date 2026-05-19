from __future__ import annotations

from typing import List

from stockml.sentiment.cnbc_news_provider import CnbcRssNewsProvider
from stockml.sentiment.eodhd_news_provider import EodhdNewsProvider
from stockml.sentiment.news_provider_base import NewsProviderBase
from stockml.sentiment.yahoo_news_provider import YahooNewsProvider


def sentiment_providers_from_name(provider_name: str | None = None) -> List[NewsProviderBase]:
    clean = str(provider_name or "legacy").lower().strip()
    if clean in {"eodhd", "eodhd_news"}:
        return [EodhdNewsProvider()]
    if clean in {"yahoo", "legacy", "yahoo_cnbc"}:
        return [YahooNewsProvider(), CnbcRssNewsProvider()]
    raise ValueError(f"Unsupported sentiment provider: {provider_name}")
