from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

import pandas as pd


class MarketDataProvider(ABC):
    """Read-only market data provider contract.

    Provider adapters must return the canonical schemas defined in
    `stockml.marketdata.schemas`. Downstream feature, gold, model, and portal
    code should depend on those schemas rather than vendor SDKs.
    """

    provider_name: str

    @abstractmethod
    def fetch_daily_prices(self, tickers: Iterable[str], *, start: str, download_timestamp: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Return `(prices, failures)` for the requested symbols."""

    @abstractmethod
    def fetch_fundamentals(self, ticker: str, *, company: str = "", exchange: str = "") -> dict[str, object]:
        """Return one canonical fundamentals row."""

