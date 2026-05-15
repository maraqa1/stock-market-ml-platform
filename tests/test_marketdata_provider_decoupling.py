from pathlib import Path

import pandas as pd

from stockml.marketdata.providers.base import MarketDataProvider
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

