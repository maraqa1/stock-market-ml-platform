from __future__ import annotations

from datetime import date, datetime, timezone
import os
from typing import Any, Dict, List

from stockml.marketdata.providers.eodhd import EODHD_BASE_URL, eodhd_api_key_from_env, to_eodhd_symbol
from stockml.sentiment.news_provider_base import NewsProviderBase


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(str(os.getenv(name, "")).strip() or default))
    except Exception:
        return default


class EodhdNewsProvider(NewsProviderBase):
    source_name = "eodhd_news"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        session: Any | None = None,
        base_url: str = EODHD_BASE_URL,
        default_exchange_suffix: str = "US",
        timeout: int | None = None,
        limit: int | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else eodhd_api_key_from_env()
        self.session = session
        self._owned_session: Any | None = None
        self.base_url = base_url.rstrip("/")
        self.default_exchange_suffix = default_exchange_suffix
        self.timeout = timeout if timeout is not None else _env_int("STOCKML_EODHD_NEWS_TIMEOUT", 10)
        self.limit = limit if limit is not None else _env_int("STOCKML_EODHD_NEWS_LIMIT", 25)

    def _session(self) -> Any:
        if self.session is not None:
            return self.session
        import requests

        if self._owned_session is None:
            self._owned_session = requests.Session()
        return self._owned_session

    def fetch_articles(self, ticker: str) -> List[Dict[str, object]]:
        return self.fetch_articles_between(ticker)

    def fetch_articles_between(
        self,
        ticker: str,
        *,
        from_date: date | str | None = None,
        to_date: date | str | None = None,
    ) -> List[Dict[str, object]]:
        if not self.api_key:
            raise RuntimeError("EODHD_API_KEY is not set")

        clean_ticker = str(ticker).upper().strip()
        if not clean_ticker:
            return []

        provider_symbol = to_eodhd_symbol(clean_ticker, default_exchange_suffix=self.default_exchange_suffix)
        params: dict[str, object] = {
            "s": provider_symbol,
            "limit": self.limit,
            "api_token": self.api_key,
            "fmt": "json",
        }
        if from_date is not None:
            params["from"] = str(from_date)
        if to_date is not None:
            params["to"] = str(to_date)
        response = self._session().get(
            f"{self.base_url}/news",
            params=params,
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
