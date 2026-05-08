from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List
from xml.etree import ElementTree

import requests

from stockml.sentiment.news_provider_base import NewsProviderBase


class CnbcRssNewsProvider(NewsProviderBase):
    source_name = "cnbc_rss"

    def __init__(self, feed_url: str = "https://www.cnbc.com/?format=rss", timeout: int = 20):
        self.feed_url = feed_url
        self.timeout = timeout
        self._articles: List[Dict[str, object]] | None = None

    def fetch_articles(self, ticker: str) -> List[Dict[str, object]]:
        clean_ticker = str(ticker).upper().strip()
        articles = self._load_feed()
        return [article for article in articles if _matches_ticker(article, clean_ticker)]

    def _load_feed(self) -> List[Dict[str, object]]:
        if self._articles is not None:
            return self._articles

        response = requests.get(
            self.feed_url,
            timeout=self.timeout,
            headers={"User-Agent": "stockml-research-platform/1.0"},
        )
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        items = root.findall(".//item")
        rows = []
        for item in items:
            title = _text(item, "title")
            summary = _text(item, "description")
            link = _text(item, "link")
            published = _published_timestamp(_text(item, "pubDate"))
            rows.append(
                {
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "publisher": "CNBC",
                    "providerPublishTime": published,
                    "source": self.source_name,
                }
            )
        self._articles = rows
        return rows


def _text(item: ElementTree.Element, tag: str) -> str:
    node = item.find(tag)
    return "".join(node.itertext()).strip() if node is not None else ""


def _published_timestamp(value: str) -> int:
    if not value:
        return int(datetime.now(tz=timezone.utc).timestamp())
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except Exception:
        return int(datetime.now(tz=timezone.utc).timestamp())


def _matches_ticker(article: Dict[str, object], ticker: str) -> bool:
    if not ticker:
        return False
    text = f"{article.get('title', '')} {article.get('summary', '')}".upper()
    tokens = {token.strip(".,:;!?()[]{}'\"") for token in text.split()}
    return ticker in tokens or f"({ticker})" in text

