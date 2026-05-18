from __future__ import annotations

import time
from typing import Dict, List, Optional

import pandas as pd

from stockml.marketdata.providers.yahoo_legacy import YahooLegacyProvider, empty_fundamentals_row
from stockml.marketdata.providers.factory import provider_from_name
from stockml.marketdata.schemas import FUNDAMENTAL_COLUMNS

METADATA_COLUMNS = FUNDAMENTAL_COLUMNS


def empty_metadata_row(ticker: str, status: str, error: str = "", company: str = "", exchange: str = "") -> Dict[str, object]:
    return empty_fundamentals_row(ticker, status, error, company=company, exchange=exchange)


def fetch_yahoo_metadata(ticker: str, company: str = "", exchange: str = "") -> Dict[str, object]:
    return YahooLegacyProvider().fetch_fundamentals(ticker, company=company, exchange=exchange)


def fetch_metadata_for_universe(
    universe: pd.DataFrame,
    limit: Optional[int] = None,
    sleep_seconds: float = 0.25,
    provider_name: str | None = None,
) -> pd.DataFrame:
    if "yahoo_ticker" in universe.columns:
        ticker_col = "yahoo_ticker"
    elif "ticker" in universe.columns:
        ticker_col = "ticker"
    else:
        raise ValueError("Universe requires yahoo_ticker or ticker column")

    frame = universe.copy()
    frame[ticker_col] = frame[ticker_col].astype(str).str.upper().str.strip()
    frame = frame[frame[ticker_col].ne("")].drop_duplicates(ticker_col)
    if limit:
        frame = frame.head(limit)

    rows: List[Dict[str, object]] = []
    provider = provider_from_name(provider_name)
    for _, row in frame.iterrows():
        rows.append(
            provider.fetch_fundamentals(
                ticker=row[ticker_col],
                company=str(row.get("company", "") or ""),
                exchange=str(row.get("listing_exchange", row.get("exchange", "")) or ""),
            )
        )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return pd.DataFrame(rows, columns=METADATA_COLUMNS)


def build_metadata_quality(metadata: pd.DataFrame) -> pd.DataFrame:
    out = metadata[["ticker", "metadata_status", "metadata_error"]].copy()
    completeness_cols = [c for c in METADATA_COLUMNS if c not in {"ticker", "metadata_status", "metadata_error"}]
    out["metadata_missing_ratio"] = metadata[completeness_cols].isna().mean(axis=1).round(4)
    out["has_sector"] = metadata["sector"].notna() & metadata["sector"].astype(str).ne("")
    out["has_market_cap"] = metadata["market_cap"].notna()
    return out
