from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from stockml.intraday.features import Bar, Quote
from stockml.intraday.logging import intraday_log
from stockml.trading.config import AlpacaConfig, alpaca_config


ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"
MARKET_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MarketCalendar:
    open_at: datetime | None
    close_at: datetime | None
    is_open: bool


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _parse_calendar_dt(selected: date, value: Any) -> datetime | None:
    """Parse Alpaca calendar times.

    The calendar endpoint commonly returns market-local strings such as
    "09:30" and "16:00". Treat those as America/New_York times for the
    selected session date, then convert to UTC for all worker comparisons.
    """
    if not value:
        return None
    text = str(value).strip()
    if "T" in text or "+" in text or text.endswith("Z"):
        parsed = _parse_dt(text)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=MARKET_TZ).astimezone(timezone.utc)
        return parsed.astimezone(timezone.utc)
    try:
        parsed_time = datetime.strptime(text, "%H:%M").time()
    except ValueError:
        try:
            parsed_time = datetime.strptime(text, "%H:%M:%S").time()
        except ValueError:
            return None
    return datetime.combine(selected, parsed_time, tzinfo=MARKET_TZ).astimezone(timezone.utc)


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


class IndependentReferenceProvider:
    def fetch_quote(self, symbol: str) -> Quote | None:
        return None

    def fetch_bars(self, symbol: str, timeframe: str = "5Min", limit: int = 12) -> list[Bar] | None:
        return None


class IntradayProvider:
    """Read-only Alpaca market-data provider.

    This provider deliberately contains no order endpoints. It is safe to
    instantiate before the worker exists; sustained polling is introduced later.
    """

    def __init__(
        self,
        config: AlpacaConfig | None = None,
        *,
        data_base_url: str = ALPACA_DATA_BASE_URL,
        session: Any = requests,
        timeout_seconds: int = 20,
        logger: Callable[[str, dict[str, Any]], Any] = intraday_log,
    ) -> None:
        self.config = config or alpaca_config()
        self.data_base_url = data_base_url.rstrip("/")
        self.session = session
        self.timeout_seconds = timeout_seconds
        self.logger = logger

    def _headers(self) -> dict[str, str]:
        if not self.config.api_key or not self.config.secret_key:
            raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY are required for Alpaca data API calls.")
        return {
            "APCA-API-KEY-ID": self.config.api_key,
            "APCA-API-SECRET-KEY": self.config.secret_key,
            "Content-Type": "application/json",
        }

    def _get(self, url: str, *, params: dict[str, Any] | None = None, symbol: str = "", endpoint: str = "") -> dict[str, Any]:
        started = time.perf_counter()
        status = "error"
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = self.session.get(url, headers=self._headers(), params=params or {}, timeout=self.timeout_seconds)
                status = str(getattr(response, "status_code", ""))
                if response.status_code >= 400:
                    response.raise_for_status()
                payload = response.json()
                self._log_call(symbol, endpoint, status, started)
                return payload
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.2)
        self._log_call(symbol, endpoint, status, started, error=str(last_error or "unknown_error"))
        raise last_error or RuntimeError("alpaca_data_request_failed")

    def _log_call(self, symbol: str, endpoint: str, status: str, started: float, error: str = "") -> None:
        try:
            self.logger(
                "api_call",
                {
                    "provider": "alpaca",
                    "endpoint": endpoint,
                    "symbol": symbol,
                    "fetched_at": _now().isoformat(timespec="seconds"),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "status": status,
                    "error": error,
                },
            )
        except Exception:
            pass

    def fetch_quote(self, symbol: str) -> Quote:
        clean = symbol.upper()
        payload = self._get(f"{self.data_base_url}/v2/stocks/{clean}/quotes/latest", symbol=clean, endpoint="latest_quote")
        raw = payload.get("quote", payload)
        return Quote(
            symbol=clean,
            bid=_float(raw.get("bp") or raw.get("bid_price")),
            ask=_float(raw.get("ap") or raw.get("ask_price")),
            bid_size=_float(raw.get("bs") or raw.get("bid_size")),
            ask_size=_float(raw.get("as") or raw.get("ask_size")),
            last_price=_float(raw.get("last_price") or raw.get("p")),
            last_size=_float(raw.get("last_size") or raw.get("s")),
            quote_ts=_parse_dt(raw.get("t") or raw.get("timestamp")),
            fetched_at=_now(),
            source="alpaca",
        )

    def fetch_bars(self, symbol: str, timeframe: str = "5Min", limit: int = 12) -> list[Bar]:
        clean = symbol.upper()
        payload = self._get(
            f"{self.data_base_url}/v2/stocks/{clean}/bars",
            params={"timeframe": timeframe, "limit": limit, "sort": "desc"},
            symbol=clean,
            endpoint="bars",
        )
        bars = payload.get("bars", [])
        normalized = [
            Bar(
                open=_float(row.get("o") or row.get("open")),
                high=_float(row.get("h") or row.get("high")),
                low=_float(row.get("l") or row.get("low")),
                close=_float(row.get("c") or row.get("close")),
                volume=_float(row.get("v") or row.get("volume")),
                vwap=_float(row.get("vw") or row.get("vwap")),
                timestamp=_parse_dt(row.get("t") or row.get("timestamp")),
            )
            for row in bars
        ]
        return list(reversed(normalized))

    def fetch_market_calendar(self, selected: date) -> MarketCalendar:
        payload = self._get(
            f"{self.config.base_url}/v2/calendar",
            params={"start": selected.isoformat(), "end": selected.isoformat()},
            endpoint="calendar",
        )
        row = payload[0] if isinstance(payload, list) and payload else {}
        open_at = _parse_calendar_dt(selected, row.get("open"))
        close_at = _parse_calendar_dt(selected, row.get("close"))
        return MarketCalendar(open_at=open_at, close_at=close_at, is_open=bool(row and open_at and close_at))
