from __future__ import annotations

import os
import re
from typing import Any, Iterable

import pandas as pd

from stockml.common.paths import PROJECT_ROOT
from stockml.marketdata.providers.base import MarketDataProvider
from stockml.marketdata.providers.yahoo_legacy import empty_fundamentals_row
from stockml.marketdata.schemas import FUNDAMENTAL_COLUMNS, PRICE_COLUMNS


EODHD_BASE_URL = "https://eodhd.com/api"
INTRADAY_COLUMNS = ["symbol", "timestamp", "open", "high", "low", "close", "volume", "vwap", "spread_bps", "source", "download_timestamp"]


def to_eodhd_symbol(ticker: str, *, default_exchange_suffix: str = "US") -> str:
    clean = str(ticker or "").upper().strip()
    if not clean:
        return ""
    if "." in clean:
        return clean
    suffix = str(default_exchange_suffix or "").upper().strip()
    return f"{clean}.{suffix}" if suffix else clean


def from_eodhd_symbol(symbol: str) -> str:
    clean = str(symbol or "").upper().strip()
    if clean.endswith(".US"):
        return clean[:-3]
    return clean


def eodhd_api_key_from_env() -> str:
    existing = os.getenv("EODHD_API_KEY", "").strip()
    if existing:
        return existing

    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return ""

    for line in env_path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#") or "=" not in clean:
            continue
        key, value = clean.split("=", 1)
        if key.strip() == "EODHD_API_KEY":
            return value.strip().strip("'\"")
    return ""


def scrub_eodhd_secret(text: object) -> str:
    return re.sub(r"(api_token=)[^&\s]+", r"\1<redacted>", str(text))


def normalize_eodhd_eod_rows(rows: list[dict[str, Any]], ticker: str, download_timestamp: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=PRICE_COLUMNS)

    out = pd.DataFrame(rows)
    rename = {
        "adjusted_close": "adj_close",
        "adjustedClose": "adj_close",
    }
    out = out.rename(columns={column: rename.get(column, column) for column in out.columns})
    out["ticker"] = str(ticker).upper().strip()
    out["source"] = "eodhd"
    out["download_timestamp"] = download_timestamp

    for column in PRICE_COLUMNS:
        if column not in out.columns:
            out[column] = pd.NA

    out = out[PRICE_COLUMNS].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()
    for column in ["open", "high", "low", "close", "adj_close", "volume"]:
        out[column] = pd.to_numeric(out[column], errors="coerce")

    out = out.dropna(subset=["date", "ticker"])
    out = out.drop_duplicates(["ticker", "date"], keep="last")
    return out


def _unix_seconds(value: object) -> int:
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        raise ValueError(f"Invalid timestamp: {value}")
    return int(parsed.timestamp())


def normalize_eodhd_intraday_rows(rows: list[dict[str, Any]], ticker: str, download_timestamp: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=INTRADAY_COLUMNS)

    out = pd.DataFrame(rows)
    if "datetime" in out.columns and "timestamp" not in out.columns:
        out = out.rename(columns={"datetime": "timestamp"})
    if "date" in out.columns and "timestamp" not in out.columns:
        out = out.rename(columns={"date": "timestamp"})
    if "gmtoffset" in out.columns:
        out = out.drop(columns=["gmtoffset"])
    out["symbol"] = str(ticker).upper().strip()
    out["source"] = "eodhd"
    out["download_timestamp"] = download_timestamp
    for column in ["open", "high", "low", "close", "volume", "vwap", "spread_bps"]:
        if column not in out.columns:
            out[column] = pd.NA
    out["timestamp"] = pd.to_datetime(out.get("timestamp"), errors="coerce", utc=True)
    for column in ["open", "high", "low", "close", "volume", "vwap", "spread_bps"]:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out["vwap"] = out["vwap"].fillna(out["close"])
    out = out[INTRADAY_COLUMNS].copy()
    out = out.dropna(subset=["symbol", "timestamp", "open", "high", "low", "close"])
    out = out.drop_duplicates(["symbol", "timestamp"], keep="last")
    return out


def _nested(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return pd.NA
        current = current.get(key)
    return current if current not in (None, "") else pd.NA


def _first(data: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = _nested(data, *path)
        if value is not pd.NA:
            return value
    return pd.NA


class EodhdProvider(MarketDataProvider):
    provider_name = "eodhd"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        session: Any | None = None,
        base_url: str = EODHD_BASE_URL,
        default_exchange_suffix: str = "US",
        timeout: int = 30,
    ) -> None:
        self.api_key = api_key if api_key is not None else eodhd_api_key_from_env()
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.default_exchange_suffix = default_exchange_suffix
        self.timeout = timeout

    def _session(self) -> Any:
        if self.session is not None:
            return self.session
        import requests

        return requests

    def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        if not self.api_key:
            raise RuntimeError("EODHD_API_KEY is not set")
        response = self._session().get(
            f"{self.base_url}/{path.lstrip('/')}",
            params={**params, "api_token": self.api_key, "fmt": "json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def fetch_daily_prices(self, tickers: Iterable[str], *, start: str, download_timestamp: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        all_rows: list[pd.DataFrame] = []
        failures: list[dict[str, object]] = []
        for raw_ticker in tickers:
            ticker = str(raw_ticker).upper().strip()
            if not ticker:
                continue
            provider_symbol = to_eodhd_symbol(ticker, default_exchange_suffix=self.default_exchange_suffix)
            try:
                payload = self._get_json(f"eod/{provider_symbol}", {"from": start})
                if isinstance(payload, dict) and (payload.get("code") or payload.get("message")):
                    failures.append({"ticker": ticker, "start": start, "reason": scrub_eodhd_secret(payload)[:500]})
                    continue
                if not isinstance(payload, list) or not payload:
                    failures.append({"ticker": ticker, "start": start, "reason": "empty_download"})
                    continue
                normalized = normalize_eodhd_eod_rows(payload, ticker, download_timestamp)
                if normalized.empty:
                    failures.append({"ticker": ticker, "start": start, "reason": "empty_normalized_download"})
                else:
                    all_rows.append(normalized)
            except Exception as exc:
                failures.append({"ticker": ticker, "start": start, "reason": scrub_eodhd_secret(exc)[:500]})

        prices = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame(columns=PRICE_COLUMNS)
        return prices, pd.DataFrame(failures, columns=["ticker", "start", "reason"])

    def fetch_intraday_bars(
        self,
        ticker: str,
        *,
        start: str,
        end: str,
        interval: str = "5m",
        download_timestamp: str,
    ) -> tuple[pd.DataFrame, dict[str, object] | None]:
        clean_ticker = str(ticker).upper().strip()
        provider_symbol = to_eodhd_symbol(clean_ticker, default_exchange_suffix=self.default_exchange_suffix)
        try:
            payload = self._get_json(
                f"intraday/{provider_symbol}",
                {
                    "interval": interval,
                    "from": _unix_seconds(start),
                    "to": _unix_seconds(end),
                },
            )
            if isinstance(payload, dict) and (payload.get("code") or payload.get("message")):
                return pd.DataFrame(columns=INTRADAY_COLUMNS), {"symbol": clean_ticker, "start": start, "end": end, "reason": scrub_eodhd_secret(payload)[:500]}
            if not isinstance(payload, list) or not payload:
                return pd.DataFrame(columns=INTRADAY_COLUMNS), {"symbol": clean_ticker, "start": start, "end": end, "reason": "empty_download"}
            normalized = normalize_eodhd_intraday_rows(payload, clean_ticker, download_timestamp)
            if normalized.empty:
                return normalized, {"symbol": clean_ticker, "start": start, "end": end, "reason": "empty_normalized_download"}
            return normalized, None
        except Exception as exc:
            return pd.DataFrame(columns=INTRADAY_COLUMNS), {"symbol": clean_ticker, "start": start, "end": end, "reason": scrub_eodhd_secret(exc)[:500]}

    def fetch_fundamentals(self, ticker: str, *, company: str = "", exchange: str = "") -> dict[str, object]:
        clean_ticker = str(ticker).upper().strip()
        provider_symbol = to_eodhd_symbol(clean_ticker, default_exchange_suffix=self.default_exchange_suffix)
        try:
            payload = self._get_json(f"fundamentals/{provider_symbol}", {})
            if not isinstance(payload, dict) or not payload:
                return empty_fundamentals_row(clean_ticker, "empty_metadata", company=company, exchange=exchange)
            general = payload.get("General") if isinstance(payload.get("General"), dict) else {}
            highlights = payload.get("Highlights") if isinstance(payload.get("Highlights"), dict) else {}
            valuation = payload.get("Valuation") if isinstance(payload.get("Valuation"), dict) else {}
            technicals = payload.get("Technicals") if isinstance(payload.get("Technicals"), dict) else {}

            row = {column: pd.NA for column in FUNDAMENTAL_COLUMNS}
            row.update(
                {
                    "ticker": clean_ticker,
                    "company": company or general.get("Name") or pd.NA,
                    "exchange": exchange or general.get("Exchange") or pd.NA,
                    "sector": general.get("Sector") or pd.NA,
                    "industry": general.get("Industry") or pd.NA,
                    "market_cap": highlights.get("MarketCapitalization") or pd.NA,
                    "beta": technicals.get("Beta") or highlights.get("Beta") or pd.NA,
                    "trailing_pe": highlights.get("PERatio") or pd.NA,
                    "forward_pe": highlights.get("ForwardPE") or pd.NA,
                    "price_to_book": valuation.get("PriceBookMRQ") or highlights.get("PriceBookMRQ") or pd.NA,
                    "dividend_yield": highlights.get("DividendYield") or pd.NA,
                    "average_volume": technicals.get("AvgVolume") or pd.NA,
                    "quote_type": general.get("Type") or general.get("AssetType") or pd.NA,
                    "currency": general.get("CurrencyCode") or general.get("CurrencyName") or pd.NA,
                    "country": general.get("CountryName") or general.get("CountryISO") or pd.NA,
                    "metadata_status": "ok",
                    "metadata_error": "",
                }
            )
            return row
        except Exception as exc:
            return empty_fundamentals_row(clean_ticker, "metadata_error", scrub_eodhd_secret(exc), company=company, exchange=exchange)
