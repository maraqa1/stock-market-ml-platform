import pandas as pd

from stockml.metadata import build_metadata_enriched
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


def test_metadata_build_reuses_last_healthy_snapshot_when_fresh_caps_are_bad(tmp_path, monkeypatch):
    interim = tmp_path / "data" / "interim"
    interim.mkdir(parents=True)
    universe = interim / "03_us_price_validated_universe_20260527_000000.csv"
    pd.DataFrame(
        [
            {"yahoo_ticker": "AAA", "company": "A", "listing_exchange": "NYSE"},
            {"yahoo_ticker": "BBB", "company": "B", "listing_exchange": "NYSE"},
        ]
    ).to_csv(universe, index=False)

    healthy = pd.DataFrame(
        [
            {**empty_metadata_row("AAA", "ok", company="A", exchange="NYSE"), "market_cap": 1_000_000_000},
            {**empty_metadata_row("BBB", "ok", company="B", exchange="NYSE"), "market_cap": 2_000_000_000},
        ],
        columns=METADATA_COLUMNS,
    )
    healthy.to_csv(interim / "04_us_metadata_enriched_20260526_000000.csv", index=False)

    bad = pd.DataFrame(
        [
            empty_metadata_row("AAA", "metadata_error", "fundamentals not subscribed"),
            empty_metadata_row("BBB", "metadata_error", "fundamentals not subscribed"),
        ],
        columns=METADATA_COLUMNS,
    )

    monkeypatch.setattr(build_metadata_enriched, "INTERIM_DIR", interim)
    monkeypatch.setattr(build_metadata_enriched, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(build_metadata_enriched, "timestamp", lambda: "20260527_010000")
    monkeypatch.setattr(build_metadata_enriched, "fetch_metadata_for_universe", lambda *args, **kwargs: bad)

    paths = build_metadata_enriched.build_metadata_enriched(sleep_seconds=0)
    out = pd.read_csv(paths["metadata_enriched"], low_memory=False)
    quality = pd.read_csv(paths["metadata_quality"], low_memory=False)

    assert paths["metadata_enriched"].name == "04_us_metadata_enriched_20260527_010000.csv"
    assert pd.to_numeric(out["market_cap"], errors="coerce").notna().mean() == 1.0
    assert set(out["ticker"]) == {"AAA", "BBB"}
    assert set(quality["metadata_build_source"]) == {"reused_last_good"}


def test_metadata_build_fails_closed_when_no_healthy_snapshot_exists(tmp_path, monkeypatch):
    interim = tmp_path / "data" / "interim"
    interim.mkdir(parents=True)
    pd.DataFrame([{"yahoo_ticker": "AAA"}]).to_csv(
        interim / "03_us_price_validated_universe_20260527_000000.csv",
        index=False,
    )
    bad = pd.DataFrame([empty_metadata_row("AAA", "metadata_error", "fundamentals not subscribed")], columns=METADATA_COLUMNS)

    monkeypatch.setattr(build_metadata_enriched, "INTERIM_DIR", interim)
    monkeypatch.setattr(build_metadata_enriched, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(build_metadata_enriched, "timestamp", lambda: "20260527_010000")
    monkeypatch.setattr(build_metadata_enriched, "fetch_metadata_for_universe", lambda *args, **kwargs: bad)

    try:
        build_metadata_enriched.build_metadata_enriched(sleep_seconds=0)
    except RuntimeError as exc:
        assert "no previous healthy metadata snapshot found" in str(exc)
    else:
        raise AssertionError("metadata build should fail closed without a healthy fallback")
    assert not list(interim.glob("04_us_metadata_enriched_20260527_010000.csv"))
