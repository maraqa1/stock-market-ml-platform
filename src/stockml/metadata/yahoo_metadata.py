from __future__ import annotations

import time
from typing import Dict, List, Optional

import pandas as pd

METADATA_COLUMNS = [
    "ticker",
    "company",
    "exchange",
    "sector",
    "industry",
    "market_cap",
    "beta",
    "trailing_pe",
    "forward_pe",
    "price_to_book",
    "dividend_yield",
    "average_volume",
    "quote_type",
    "currency",
    "country",
    "metadata_status",
    "metadata_error",
]


def empty_metadata_row(ticker: str, status: str, error: str = "", company: str = "", exchange: str = "") -> Dict[str, object]:
    row = {col: pd.NA for col in METADATA_COLUMNS}
    row.update(
        {
            "ticker": str(ticker).upper().strip(),
            "company": company,
            "exchange": exchange,
            "metadata_status": status,
            "metadata_error": error[:500],
        }
    )
    return row


def _info_value(info: Dict[str, object], *keys: str) -> object:
    for key in keys:
        value = info.get(key)
        if value not in (None, ""):
            return value
    return pd.NA


def fetch_yahoo_metadata(ticker: str, company: str = "", exchange: str = "") -> Dict[str, object]:
    clean_ticker = str(ticker).upper().strip()
    try:
        import yfinance as yf

        info = yf.Ticker(clean_ticker).get_info()
        if not isinstance(info, dict) or not info:
            return empty_metadata_row(clean_ticker, "empty_metadata", company=company, exchange=exchange)

        return {
            "ticker": clean_ticker,
            "company": company or _info_value(info, "longName", "shortName"),
            "exchange": exchange or _info_value(info, "exchange", "fullExchangeName"),
            "sector": _info_value(info, "sector"),
            "industry": _info_value(info, "industry"),
            "market_cap": _info_value(info, "marketCap"),
            "beta": _info_value(info, "beta"),
            "trailing_pe": _info_value(info, "trailingPE"),
            "forward_pe": _info_value(info, "forwardPE"),
            "price_to_book": _info_value(info, "priceToBook"),
            "dividend_yield": _info_value(info, "dividendYield"),
            "average_volume": _info_value(info, "averageVolume", "averageDailyVolume10Day"),
            "quote_type": _info_value(info, "quoteType"),
            "currency": _info_value(info, "currency"),
            "country": _info_value(info, "country"),
            "metadata_status": "ok",
            "metadata_error": "",
        }
    except Exception as exc:
        return empty_metadata_row(clean_ticker, "metadata_error", str(exc), company=company, exchange=exchange)


def fetch_metadata_for_universe(
    universe: pd.DataFrame,
    limit: Optional[int] = None,
    sleep_seconds: float = 0.25,
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
    for _, row in frame.iterrows():
        rows.append(
            fetch_yahoo_metadata(
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
