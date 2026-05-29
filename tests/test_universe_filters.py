from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.universe.clean_us_equity_universe import clean_universe_frame, tradable_only


def sample_universe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "yahoo_ticker": "AAPL",
                "company": "Apple Inc. - Common Stock",
                "security_name": "Apple Inc. - Common Stock",
                "listing_exchange": "NASDAQ",
                "test_issue": "N",
                "etf_flag": "N",
                "source": "nasdaqlisted",
            },
            {
                "symbol": "SPY",
                "yahoo_ticker": "SPY",
                "company": "SPDR S&P 500 ETF Trust",
                "security_name": "SPDR S&P 500 ETF Trust",
                "listing_exchange": "NYSEARCA",
                "test_issue": "N",
                "etf_flag": "Y",
                "source": "otherlisted",
            },
            {
                "symbol": "ABCW",
                "yahoo_ticker": "ABCW",
                "company": "Example Corp Warrants",
                "security_name": "Example Corp Warrants",
                "listing_exchange": "NASDAQ",
                "test_issue": "N",
                "etf_flag": "N",
                "source": "nasdaqlisted",
            },
            {
                "symbol": "TEST",
                "yahoo_ticker": "TEST",
                "company": "Tick Pilot Test Stock Common Stock",
                "security_name": "Tick Pilot Test Stock Common Stock",
                "listing_exchange": "NASDAQ",
                "test_issue": "Y",
                "etf_flag": "N",
                "source": "nasdaqlisted",
            },
            {
                "symbol": "BRK.B",
                "yahoo_ticker": "BRK-B",
                "company": "Berkshire Hathaway Inc. Class B Common Stock",
                "security_name": "Berkshire Hathaway Inc. Class B Common Stock",
                "listing_exchange": "NYSE",
                "test_issue": "N",
                "etf_flag": "N",
                "source": "otherlisted",
            },
            {
                "symbol": "BADF",
                "yahoo_ticker": "BADF",
                "company": "Bad Filing Corp Common Stock",
                "security_name": "Bad Filing Corp Common Stock",
                "listing_exchange": "NASDAQ",
                "test_issue": "N",
                "financial_status": "D",
                "etf_flag": "N",
                "source": "nasdaqlisted",
            },
            {
                "symbol": "LIQD",
                "yahoo_ticker": "LIQD",
                "company": "Example Liquidating Trust Common Stock",
                "security_name": "Example Liquidating Trust Common Stock",
                "listing_exchange": "NYSE",
                "test_issue": "N",
                "etf_flag": "N",
                "source": "otherlisted",
            },
            {
                "symbol": "SPACU",
                "yahoo_ticker": "SPACU",
                "company": "Example Acquisition Corp Unit",
                "security_name": "Example Acquisition Corp Unit",
                "listing_exchange": "NASDAQ",
                "test_issue": "N",
                "financial_status": "N",
                "etf_flag": "N",
                "source": "nasdaqlisted",
            },
        ]
    )


def test_clean_universe_keeps_common_stock_candidates():
    cleaned = clean_universe_frame(sample_universe())
    kept = set(cleaned.loc[cleaned["is_tradable_common_stock_candidate"], "symbol"])
    assert "AAPL" in kept
    assert "BRK.B" in kept


def test_clean_universe_excludes_non_common_instruments():
    cleaned = clean_universe_frame(sample_universe())
    excluded = dict(zip(cleaned["symbol"], cleaned["exclude_reason"]))
    assert excluded["SPY"] in {"exchange_not_allowed", "etf"}
    assert excluded["ABCW"] == "non_common_security_name"
    assert excluded["TEST"] == "test_issue"
    assert excluded["BADF"] == "financial_status_not_normal"
    assert excluded["LIQD"] == "non_common_security_name"
    assert excluded["SPACU"] == "non_common_security_name"


def test_yahoo_ticker_format_is_preserved_for_class_shares():
    tradable = tradable_only(sample_universe())
    row = tradable[tradable["symbol"] == "BRK.B"].iloc[0]
    assert row["yahoo_ticker"] == "BRK-B"
