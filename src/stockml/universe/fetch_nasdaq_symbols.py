from __future__ import annotations

from io import StringIO
from typing import Final

import pandas as pd
import requests

NASDAQ_LISTED_URL: Final[str] = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL: Final[str] = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"

REQUEST_HEADERS = {
    "User-Agent": "stock-market-ml-platform/0.1 research",
    "Accept": "text/plain,*/*",
}


def _download_text(url: str, timeout: int = 30) -> str:
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.text


def _read_pipe_text(text: str) -> pd.DataFrame:
    df = pd.read_csv(StringIO(text), sep="|", dtype=str)
    first_col = df.columns[0]
    df = df[~df[first_col].astype(str).str.contains("File Creation Time", na=False)].copy()
    return df


def _clean_symbol(symbol: object) -> str:
    if pd.isna(symbol):
        return ""
    return str(symbol).strip().upper()


def _to_yahoo_ticker(symbol: object) -> str:
    return _clean_symbol(symbol).replace(".", "-")


def fetch_nasdaq_listed() -> pd.DataFrame:
    raw = _read_pipe_text(_download_text(NASDAQ_LISTED_URL))

    df = pd.DataFrame()
    df["symbol"] = raw.get("Symbol", "").map(_clean_symbol)
    df["yahoo_ticker"] = raw.get("Symbol", "").map(_to_yahoo_ticker)
    df["company"] = raw.get("Security Name", "").fillna("").astype(str).str.strip()
    df["security_name"] = df["company"]
    df["listing_exchange"] = "NASDAQ"
    df["market_category"] = raw.get("Market Category", "").fillna("").astype(str).str.strip()
    df["test_issue"] = raw.get("Test Issue", "").fillna("").astype(str).str.upper().str.strip()
    df["financial_status"] = raw.get("Financial Status", "").fillna("").astype(str).str.upper().str.strip()
    df["round_lot_size"] = pd.to_numeric(raw.get("Round Lot Size", ""), errors="coerce")
    df["etf_flag"] = raw.get("ETF", "").fillna("").astype(str).str.upper().str.strip()
    df["source"] = "nasdaqlisted"

    return df[df["symbol"] != ""].drop_duplicates("symbol")


def fetch_other_listed() -> pd.DataFrame:
    raw = _read_pipe_text(_download_text(OTHER_LISTED_URL))

    exchange_map = {
        "A": "NYSEAMERICAN",
        "N": "NYSE",
        "P": "NYSEARCA",
        "Z": "BATS",
        "V": "IEX",
    }

    symbol = raw.get("ACT Symbol", raw.get("CQS Symbol", ""))

    df = pd.DataFrame()
    df["symbol"] = symbol.map(_clean_symbol)
    df["yahoo_ticker"] = symbol.map(_to_yahoo_ticker)
    df["company"] = raw.get("Security Name", "").fillna("").astype(str).str.strip()
    df["security_name"] = df["company"]
    df["listing_exchange"] = raw.get("Exchange", "").fillna("").astype(str).str.upper().str.strip().map(exchange_map).fillna(raw.get("Exchange", ""))
    df["market_category"] = ""
    df["test_issue"] = raw.get("Test Issue", "").fillna("").astype(str).str.upper().str.strip()
    df["financial_status"] = ""
    df["round_lot_size"] = pd.to_numeric(raw.get("Round Lot Size", ""), errors="coerce")
    df["etf_flag"] = raw.get("ETF", "").fillna("").astype(str).str.upper().str.strip()
    df["source"] = "otherlisted"

    return df[df["symbol"] != ""].drop_duplicates("symbol")


def fetch_us_equity_universe() -> pd.DataFrame:
    frames = [fetch_nasdaq_listed(), fetch_other_listed()]
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates("symbol", keep="first")
    df = df.sort_values(["listing_exchange", "symbol"]).reset_index(drop=True)
    return df
