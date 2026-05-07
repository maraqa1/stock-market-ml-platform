from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.prices.download_price_history import determine_download_plan


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
