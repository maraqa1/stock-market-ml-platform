from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from stockml.marketdata.providers.eodhd import EODHD_BASE_URL, eodhd_api_key_from_env, to_eodhd_symbol
from stockml.sentiment.news_provider_base import NewsProviderBase


class EodhdNewsProvider(NewsProviderBase):
    source_name = "eodhd_news"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        session: Any | None = None,
        base_url: str = EODHD_BASE_URL,
        default_exchange_suffix: str = "US",
        timeout: int = 30,
        limit: int = 25,
    ) -> None:
        self.api_key = api_key if api_key is not None else eodhd_api_key_from_env()
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.default_exchange_suffix = default_exchange_suffix
        self.timeout = timeout
        self.limit = limit

    def _session(self) -> Any:
        if self.session is not None:
            return self.session
        import requests

        return requests

    def fetch_articles(self, ticker: str) -> List[Dict[str, object]]:
        if not self.api_key:
            raise RuntimeError("EODHD_API_KEY is not set")

        clean_ticker = str(ticker).upper().strip()
        if not clean_ticker:
            return []

        provider_symbol = to_eodhd_symbol(clean_ticker, default_exchange_suffix=self.default_exchange_suffix)
        response = self._session().get(
            f"{self.base_url}/news",
            params={
                "s": provider_symbol,
                "limit": self.limit,
                "api_token": self.api_key,
                "fmt": "json",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and (payload.get("message") or payload.get("error")):
            raise RuntimeError(str(payload.get("message") or payload.get("error")))
        if not isinstance(payload, list):
            return []
        return [_normalize_eodhd_article(article) for article in payload if isinstance(article, dict)]


def _normalize_eodhd_article(article: Dict[str, object]) -> Dict[str, object]:
    return {
        **article,
        "title": article.get("title") or "",
        "summary": article.get("content") or "",
        "publisher": "EODHD",
        "providerPublishTime": article.get("date") or int(datetime.now(tz=timezone.utc).timestamp()),
        "link": article.get("link") or "",
        "providerSentiment": _sentiment_value(article.get("sentiment")),
    }


def _sentiment_value(value: object) -> float | None:
    if isinstance(value, dict):
        for key in ("polarity", "score", "normalized", "sentiment"):
            parsed = _float(value.get(key))
            if parsed is not None:
                return parsed
    return _float(value)


def _float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None
