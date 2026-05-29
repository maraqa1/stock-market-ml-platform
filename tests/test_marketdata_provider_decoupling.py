from pathlib import Path

import pandas as pd

from stockml.marketdata.providers.base import MarketDataProvider
from stockml.marketdata.providers.eodhd import EodhdProvider, normalize_eodhd_eod_rows, normalize_eodhd_intraday_rows, scrub_eodhd_secret, to_eodhd_symbol
from stockml.marketdata.providers.factory import provider_from_name
from stockml.marketdata.providers.yahoo_legacy import empty_fundamentals_row, normalize_yfinance_download
from stockml.marketdata.schemas import FUNDAMENTAL_COLUMNS, PRICE_COLUMNS
from stockml.metadata.yahoo_metadata import METADATA_COLUMNS, empty_metadata_row


def test_marketdata_contracts_define_current_price_and_fundamental_schemas():
    assert PRICE_COLUMNS == ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume", "source", "download_timestamp"]
    assert METADATA_COLUMNS == FUNDAMENTAL_COLUMNS


def test_yahoo_legacy_normalizes_prices_to_canonical_schema():
    raw = pd.DataFrame(
        {
            "Date": ["2026-05-14"],
            "Open": [10],
            "High": [11],
            "Low": [9],
            "Close": [10.5],
            "Adj Close": [10.25],
            "Volume": [1000],
        }
    ).set_index("Date")

    out = normalize_yfinance_download(raw, ["AAA"], "2026-05-15T00:00:00")

    assert list(out.columns) == PRICE_COLUMNS
    assert out.loc[0, "ticker"] == "AAA"
    assert out.loc[0, "source"] == "yahoo_legacy"
    assert out.loc[0, "adj_close"] == 10.25


def test_eodhd_normalizes_prices_to_canonical_schema():
    raw = [
        {
            "date": "2026-05-14",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10.5,
            "adjusted_close": 10.25,
            "volume": 1000,
        }
    ]

    out = normalize_eodhd_eod_rows(raw, "aaa", "2026-05-15T00:00:00")

    assert list(out.columns) == PRICE_COLUMNS
    assert out.loc[0, "ticker"] == "AAA"
    assert out.loc[0, "source"] == "eodhd"
    assert out.loc[0, "adj_close"] == 10.25


def test_eodhd_provider_fetches_price_rows_with_us_suffix():
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"date": "2026-05-14", "open": 10, "high": 11, "low": 9, "close": 10.5, "adjusted_close": 10.25, "volume": 1000}]

    class Session:
        def get(self, url, params, timeout):
            calls.append((url, params, timeout))
            return Response()

    provider = EodhdProvider(api_key="key", session=Session())
    prices, failures = provider.fetch_daily_prices(["aaa"], start="2026-05-01", download_timestamp="stamp")

    assert failures.empty
    assert prices.loc[0, "ticker"] == "AAA"
    assert calls[0][0].endswith("/eod/AAA.US")
    assert calls[0][1]["from"] == "2026-05-01"
    assert calls[0][1]["api_token"] == "key"


def test_eodhd_normalizes_intraday_rows_to_spec72_schema():
    raw = [{"datetime": "2026-05-29 14:30:00", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000}]

    out = normalize_eodhd_intraday_rows(raw, "aaa", "stamp")

    assert out.loc[0, "symbol"] == "AAA"
    assert str(out.loc[0, "timestamp"]) == "2026-05-29 14:30:00+00:00"
    assert out.loc[0, "vwap"] == 10.5


def test_eodhd_normalizes_intraday_unix_seconds():
    raw = [{"timestamp": 1772461800, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000}]

    out = normalize_eodhd_intraday_rows(raw, "aaa", "stamp")

    assert out.loc[0, "timestamp"].year == 2026
    assert out.loc[0, "timestamp"].month == 3


def test_eodhd_provider_fetches_intraday_bars_with_unix_window():
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"datetime": "2026-05-29 14:30:00", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000}]

    class Session:
        def get(self, url, params, timeout):
            calls.append((url, params, timeout))
            return Response()

    provider = EodhdProvider(api_key="key", session=Session())
    bars, failure = provider.fetch_intraday_bars("aaa", start="2026-05-29T14:00:00Z", end="2026-05-29T15:00:00Z", download_timestamp="stamp")

    assert failure is None
    assert bars.loc[0, "symbol"] == "AAA"
    assert calls[0][0].endswith("/intraday/AAA.US")
    assert calls[0][1]["interval"] == "5m"
    assert isinstance(calls[0][1]["from"], int)
    assert isinstance(calls[0][1]["to"], int)


def test_eodhd_provider_maps_fundamentals_to_canonical_schema():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "General": {"Name": "AAA Inc", "Exchange": "NASDAQ", "Sector": "Technology", "Industry": "Software", "CurrencyCode": "USD", "CountryName": "USA", "Type": "Common Stock"},
                "Highlights": {"MarketCapitalization": 12345, "PERatio": 12.3, "ForwardPE": 10.1, "DividendYield": 0.01, "Beta": 1.2},
                "Valuation": {"PriceBookMRQ": 2.3},
                "Technicals": {"AvgVolume": 456789},
            }

    class Session:
        def get(self, url, params, timeout):
            return Response()

    row = EodhdProvider(api_key="key", session=Session()).fetch_fundamentals("aaa")

    assert list(row.keys()) == FUNDAMENTAL_COLUMNS
    assert row["ticker"] == "AAA"
    assert row["company"] == "AAA Inc"
    assert row["market_cap"] == 12345
    assert row["metadata_status"] == "ok"


def test_provider_factory_selects_eodhd_and_yahoo_aliases():
    assert isinstance(provider_from_name("eodhd", api_key="key"), EodhdProvider)
    assert provider_from_name("yahoo").provider_name == "yahoo_legacy"
    assert to_eodhd_symbol("SEDG") == "SEDG.US"


def test_eodhd_error_text_redacts_api_token():
    text = scrub_eodhd_secret("401 for https://eodhd.com/api/eod/A.US?from=2018-01-01&api_token=secret-key&fmt=json")

    assert "secret-key" not in text
    assert "api_token=<redacted>" in text


def test_eodhd_fundamentals_errors_redact_api_token():
    class Response:
        def raise_for_status(self):
            raise RuntimeError("403 for https://eodhd.com/api/fundamentals/AAA.US?api_token=secret-key&fmt=json")

    class Session:
        def get(self, url, params, timeout):
            return Response()

    row = EodhdProvider(api_key="key", session=Session()).fetch_fundamentals("aaa")

    assert row["metadata_status"] == "metadata_error"
    assert "secret-key" not in row["metadata_error"]
    assert "api_token=<redacted>" in row["metadata_error"]


def test_legacy_metadata_wrapper_preserves_schema():
    row = empty_metadata_row("aapl", "metadata_error", "rate limited")
    provider_row = empty_fundamentals_row("aapl", "metadata_error", "rate limited")

    assert list(row.keys()) == FUNDAMENTAL_COLUMNS
    assert row == provider_row
    assert row["ticker"] == "AAPL"


def test_marketdata_provider_interface_requires_price_and_fundamental_methods():
    assert issubclass(MarketDataProvider, object)
    assert {"fetch_daily_prices", "fetch_fundamentals"}.issubset(MarketDataProvider.__abstractmethods__)


def test_portal_has_no_vendor_provider_imports():
    portal_root = Path("portal")
    vendor_tokens = ["yfinance", "alpha_vantage", "YahooLegacyProvider", "yf."]
    offenders = []
    for path in portal_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in vendor_tokens:
            if token in text:
                offenders.append(f"{path}:{token}")

    assert offenders == []


def test_non_provider_code_does_not_import_vendor_sdks():
    allowed_parts = {
        ("src", "stockml", "marketdata", "providers"),
        ("src", "stockml", "sentiment"),
        ("src", "stockml", "metadata", "yahoo_metadata.py"),
        ("tests",),
    }
    vendor_tokens = ["import yfinance", "https://eodhd.com"]
    offenders = []
    for root in [Path("src"), Path("portal"), Path("scripts")]:
        for path in root.rglob("*.py"):
            parts = path.parts
            if any(parts[:len(allowed)] == allowed for allowed in allowed_parts):
                continue
            text = path.read_text(encoding="utf-8")
            for token in vendor_tokens:
                if token in text:
                    offenders.append(f"{path}:{token}")

    assert offenders == []
