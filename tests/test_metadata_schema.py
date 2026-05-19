import pandas as pd

from stockml.metadata import yahoo_metadata
from stockml.metadata.yahoo_metadata import METADATA_COLUMNS, build_metadata_quality, empty_metadata_row, fetch_metadata_for_universe


def test_metadata_schema_contains_required_columns():
    row = empty_metadata_row("aapl", "metadata_error", "rate limited")
    frame = pd.DataFrame([row], columns=METADATA_COLUMNS)
    assert list(frame.columns) == METADATA_COLUMNS
    assert frame.loc[0, "ticker"] == "AAPL"
    assert frame.loc[0, "metadata_status"] == "metadata_error"


def test_metadata_quality_reports_missing_ratio():
    frame = pd.DataFrame([empty_metadata_row("MSFT", "empty_metadata")], columns=METADATA_COLUMNS)
    quality = build_metadata_quality(frame)
    assert {"ticker", "metadata_status", "metadata_missing_ratio", "has_sector", "has_market_cap"}.issubset(quality.columns)


def test_metadata_fetch_uses_fallback_when_primary_market_cap_missing(monkeypatch):
    class PrimaryProvider:
        provider_name = "eodhd"

        def fetch_fundamentals(self, ticker, *, company="", exchange=""):
            row = empty_metadata_row(ticker, "empty_metadata", company=company, exchange=exchange)
            row["sector"] = "Primary Sector"
            return row

    class FallbackProvider:
        provider_name = "yahoo_legacy"

        def fetch_fundamentals(self, ticker, *, company="", exchange=""):
            row = empty_metadata_row(ticker, "ok", company=company, exchange=exchange)
            row["market_cap"] = 1_000_000_000
            row["sector"] = "Fallback Sector"
            return row

    def fake_provider_from_name(name):
        return FallbackProvider() if name == "yahoo_legacy" else PrimaryProvider()

    monkeypatch.setattr(yahoo_metadata, "provider_from_name", fake_provider_from_name)
    universe = pd.DataFrame([{"yahoo_ticker": "BFLY", "company": "Butterfly", "listing_exchange": "NYSE"}])

    out = fetch_metadata_for_universe(universe, provider_name="eodhd", fallback_provider_name="yahoo_legacy", sleep_seconds=0)

    assert out.loc[0, "ticker"] == "BFLY"
    assert out.loc[0, "market_cap"] == 1_000_000_000
    assert out.loc[0, "sector"] == "Fallback Sector"
