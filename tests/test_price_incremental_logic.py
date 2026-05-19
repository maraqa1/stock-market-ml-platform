from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.prices.download_price_history import determine_download_plan
from stockml.prices.download_price_history import read_tradable_universe
from stockml.prices.validate_price_history import build_price_quality_report


def test_first_run_downloads_all_tickers():
    plan, full = determine_download_plan(["AAPL", "MSFT"], pd.DataFrame(), "2018-01-01")
    assert full is True
    assert plan == {"AAPL": "2018-01-01", "MSFT": "2018-01-01"}


def test_delta_run_downloads_existing_from_latest_with_overlap_and_new_ticker_full():
    store = pd.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "MSFT"],
            "date": pd.to_datetime(["2024-01-02", "2024-01-10", "2024-01-05"]),
        }
    )

    plan, full = determine_download_plan(["AAPL", "MSFT", "NVDA"], store, "2018-01-01")

    assert full is False
    assert plan["NVDA"] == "2018-01-01"
    assert plan["AAPL"] <= "2024-01-11"
    assert plan["MSFT"] <= "2024-01-06"


def test_provider_scoped_plan_requires_one_full_transfer_per_provider():
    store = pd.DataFrame(
        {
            "ticker": ["AAPL", "MSFT"],
            "date": pd.to_datetime(["2026-05-18", "2026-05-18"]),
            "source": ["yahoo_legacy", "yahoo_legacy"],
        }
    )

    plan, full = determine_download_plan(["AAPL", "MSFT"], store, "2018-01-01", provider_name="eodhd")

    assert full is True
    assert plan == {"AAPL": "2018-01-01", "MSFT": "2018-01-01"}


def test_provider_scoped_plan_uses_delta_after_provider_bootstrap():
    store = pd.DataFrame(
        {
            "ticker": ["AAPL", "MSFT", "AAPL"],
            "date": pd.to_datetime(["2026-05-18", "2026-05-18", "2024-01-02"]),
            "source": ["eodhd", "eodhd", "yahoo_legacy"],
        }
    )

    plan, full = determine_download_plan(["AAPL", "MSFT"], store, "2018-01-01", provider_name="eodhd")

    assert full is False
    assert plan["AAPL"] >= "2026-05-14"
    assert plan["MSFT"] >= "2026-05-14"


def test_force_full_ignores_existing_store():
    store = pd.DataFrame(
        {
            "ticker": ["AAPL"],
            "date": pd.to_datetime(["2024-01-10"]),
        }
    )

    plan, full = determine_download_plan(["AAPL"], store, "2018-01-01", force_full=True)

    assert full is True
    assert plan["AAPL"] == "2018-01-01"


def test_read_tradable_universe_filters_exchange(tmp_path, monkeypatch):
    interim = tmp_path / "data" / "interim"
    interim.mkdir(parents=True)
    path = interim / "02_us_tradable_universe_20240101_000000.csv"
    pd.DataFrame(
        {
            "yahoo_ticker": ["AAA", "BBB", "CCC"],
            "listing_exchange": ["NASDAQ", "NYSE", "NASDAQ"],
        }
    ).to_csv(path, index=False)

    monkeypatch.setattr("stockml.prices.download_price_history.INTERIM_DIR", interim)
    out = read_tradable_universe(limit=1, exchange="NASDAQ")
    assert out["yahoo_ticker"].tolist() == ["AAA"]
    assert set(out["listing_exchange"]) == {"NASDAQ"}


def test_read_tradable_universe_filters_multiple_exchanges(tmp_path, monkeypatch):
    interim = tmp_path / "data" / "interim"
    interim.mkdir(parents=True)
    path = interim / "02_us_tradable_universe_20240101_000000.csv"
    pd.DataFrame(
        {
            "yahoo_ticker": ["AAA", "BBB", "CCC"],
            "listing_exchange": ["NASDAQ", "NYSE", "NYSEAMERICAN"],
        }
    ).to_csv(path, index=False)

    monkeypatch.setattr("stockml.prices.download_price_history.INTERIM_DIR", interim)
    out = read_tradable_universe(exchange=["NASDAQ", "NYSE"])
    assert out["yahoo_ticker"].tolist() == ["AAA", "BBB"]
    assert set(out["listing_exchange"]) == {"NASDAQ", "NYSE"}


def test_price_quality_report_filters_requested_provider(tmp_path, monkeypatch):
    raw = tmp_path / "data" / "raw"
    interim = tmp_path / "data" / "interim"
    raw.mkdir(parents=True)
    interim.mkdir(parents=True)
    store = raw / "03_us_price_history_store.csv"
    universe = interim / "02_us_tradable_universe_20240101_000000.csv"

    dates = pd.date_range("2024-01-01", periods=260, freq="D")
    rows = []
    for source, ticker in [("eodhd", "AAA"), ("yahoo_legacy", "BBB")]:
        rows.extend(
            {
                "date": date,
                "ticker": ticker,
                "open": 10.0,
                "high": 10.5,
                "low": 9.5,
                "close": 10.0,
                "adj_close": 10.0,
                "volume": 1_000_000,
                "source": source,
            }
            for date in dates
        )
    pd.DataFrame(rows).to_csv(store, index=False)
    pd.DataFrame({"yahoo_ticker": ["AAA", "BBB"]}).to_csv(universe, index=False)

    monkeypatch.setattr("stockml.prices.validate_price_history.STORE_FILE", store)
    monkeypatch.setattr("stockml.prices.validate_price_history.INTERIM_DIR", interim)
    monkeypatch.setattr("stockml.prices.validate_price_history.ensure_data_dirs", lambda: None)
    monkeypatch.setattr("stockml.prices.validate_price_history.timestamp", lambda: "20240101_000000")
    monkeypatch.setattr("stockml.prices.validate_price_history.latest_tradable_universe_file", lambda: universe)

    paths = build_price_quality_report(provider_name="eodhd")
    validated = pd.read_csv(paths["validated_universe"])
    assert validated["yahoo_ticker"].tolist() == ["AAA"]
