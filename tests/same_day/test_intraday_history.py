from __future__ import annotations

import pandas as pd

from stockml.intraday.history import determine_intraday_download_plan
from stockml.intraday.history import download_intraday_history
from stockml.intraday.history import normalize_intraday_bars
from stockml.intraday.history import save_intraday_store


def test_normalizes_intraday_bars_schema():
    raw = pd.DataFrame(
        [
            {
                "ticker": "aaa",
                "datetime": "2026-05-29T14:30:00Z",
                "open": "10",
                "high": "11",
                "low": "9",
                "close": "10.5",
                "volume": "1000",
            }
        ]
    )

    out = normalize_intraday_bars(raw)

    assert out.loc[0, "symbol"] == "AAA"
    assert str(out.loc[0, "timestamp"]) == "2026-05-29 14:30:00+00:00"
    assert out.loc[0, "vwap"] == 10.5


def test_cache_deduplicates_symbol_timestamp(tmp_path):
    store = tmp_path / "bars.csv"
    frame = pd.DataFrame(
        [
            {"symbol": "AAA", "timestamp": "2026-05-29T14:30:00Z", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100, "vwap": 10},
            {"symbol": "AAA", "timestamp": "2026-05-29T14:30:00Z", "open": 12, "high": 13, "low": 11, "close": 12, "volume": 200, "vwap": 12},
        ]
    )

    save_intraday_store(frame, store)
    saved = pd.read_csv(store)

    assert len(saved) == 1
    assert saved.loc[0, "close"] == 12


def test_delta_plan_starts_after_latest_cached_bar():
    store = pd.DataFrame(
        [
            {"symbol": "AAA", "timestamp": "2026-05-29T14:30:00Z", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
        ]
    )

    plan, full_mode = determine_intraday_download_plan(
        ["AAA", "BBB"],
        store,
        start_date="2026-05-01",
        end_date="2026-05-29",
    )

    assert full_mode is False
    assert plan["AAA"][0] == pd.Timestamp("2026-05-29T14:35:00Z")
    assert plan["BBB"][0] == pd.Timestamp("2026-05-01T00:00:00Z")


def test_download_records_provider_failures(tmp_path, monkeypatch):
    class Provider:
        provider_name = "fake"

        def fetch_intraday_bars(self, symbol, start, end, interval, download_timestamp):
            return pd.DataFrame(), {"symbol": symbol, "start": start, "end": end, "reason": "provider_error"}

    monkeypatch.setattr("stockml.intraday.history._provider_from_name", lambda provider_name: Provider())

    paths = download_intraday_history(
        start_date="2026-05-29",
        end_date="2026-05-29",
        provider_name="fake",
        symbols=["AAA"],
        sleep_seconds=0,
        store_file=tmp_path / "store.csv",
        output_dir=tmp_path,
    )

    failures = pd.read_csv(paths["failures_file"])
    assert failures.loc[0, "symbol"] == "AAA"
    assert failures.loc[0, "reason"] == "provider_error"
