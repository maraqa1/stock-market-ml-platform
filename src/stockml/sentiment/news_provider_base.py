from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List


class NewsProviderBase(ABC):
    source_name = "base"

    @abstractmethod
    def fetch_articles(self, ticker: str) -> List[Dict[str, object]]:
        """Return timestamped article dictionaries for a ticker."""

