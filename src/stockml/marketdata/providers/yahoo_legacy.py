from __future__ import annotations

from typing import Iterable

import pandas as pd

from stockml.marketdata.providers.base import MarketDataProvider
from stockml.marketdata.schemas import FUNDAMENTAL_COLUMNS, PRICE_COLUMNS


def empty_fundamentals_row(ticker: str, status: str, error: str = "", company: str = "", exchange: str = "") -> dict[str, object]:
    row = {column: pd.NA for column in FUNDAMENTAL_COLUMNS}
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


def _info_value(info: dict[str, object], *keys: str) -> object:
    for key in keys:
        value = info.get(key)
        if value not in (None, ""):
            return value
    return pd.NA


def normalize_yfinance_download(data: pd.DataFrame, tickers: list[str], download_timestamp: str) -> pd.DataFrame:
    rows = []

    if data is None or data.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)

    if isinstance(data.columns, pd.MultiIndex):
        top_level = list(data.columns.get_level_values(0).unique())
        ticker_first = any(t in top_level for t in tickers)

        for ticker in tickers:
            try:
                if ticker_first:
                    sub = data[ticker].copy()
                else:
                    sub = data.xs(ticker, axis=1, level=1).copy()
            except Exception:
                continue

            sub = sub.reset_index()
            sub["ticker"] = ticker
            rows.append(sub)
    else:
        if len(tickers) != 1:
            return pd.DataFrame(columns=PRICE_COLUMNS)
        sub = data.copy().reset_index()
        sub["ticker"] = tickers[0]
        rows.append(sub)

    if not rows:
        return pd.DataFrame(columns=PRICE_COLUMNS)

    out = pd.concat(rows, ignore_index=True)

    rename = {
        "Date": "date",
        "Datetime": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Adj_Close": "adj_close",
        "Volume": "volume",
    }

    out = out.rename(columns={column: rename.get(column, column) for column in out.columns})

    for column in PRICE_COLUMNS:
        if column not in out.columns:
            out[column] = pd.NA

    out = out[PRICE_COLUMNS].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()

    for column in ["open", "high", "low", "close", "adj_close", "volume"]:
        out[column] = pd.to_numeric(out[column], errors="coerce")

    out["source"] = "yahoo_legacy"
    out["download_timestamp"] = download_timestamp

    out = out.dropna(subset=["date", "ticker"])
    out = out.drop_duplicates(["ticker", "date"], keep="last")
    return out


class YahooLegacyProvider(MarketDataProvider):
    provider_name = "yahoo_legacy"

    def fetch_daily_prices(self, tickers: Iterable[str], *, start: str, download_timestamp: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        batch = [str(ticker).upper().strip() for ticker in tickers if str(ticker).strip()]
        try:
            import yfinance as yf

            data = yf.download(
                tickers=batch,
                start=start,
                auto_adjust=False,
                group_by="ticker",
                progress=False,
                threads=True,
            )
            normalized = normalize_yfinance_download(data, batch, download_timestamp)

            failures = []
            if normalized.empty:
                failures.extend({"ticker": ticker, "start": start, "reason": "empty_download"} for ticker in batch)
            else:
                got = set(normalized["ticker"].unique())
                failures.extend({"ticker": ticker, "start": start, "reason": "missing_from_batch_result"} for ticker in sorted(set(batch) - got))
            return normalized, pd.DataFrame(failures)
        except Exception as exc:
            failures = [{"ticker": ticker, "start": start, "reason": str(exc)[:500]} for ticker in batch]
            return pd.DataFrame(columns=PRICE_COLUMNS), pd.DataFrame(failures)

    def fetch_fundamentals(self, ticker: str, *, company: str = "", exchange: str = "") -> dict[str, object]:
        clean_ticker = str(ticker).upper().strip()
        try:
            import yfinance as yf

            info = yf.Ticker(clean_ticker).get_info()
            if not isinstance(info, dict) or not info:
                return empty_fundamentals_row(clean_ticker, "empty_metadata", company=company, exchange=exchange)

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
            return empty_fundamentals_row(clean_ticker, "metadata_error", str(exc), company=company, exchange=exchange)

