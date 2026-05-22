import pandas as pd

from stockml.features.build_feature_panel import FEATURE_PANEL_COLUMNS, build_feature_panel_from_frames


def test_feature_engineering_builds_required_columns():
    dates = pd.date_range("2024-01-01", periods=80, freq="B")
    prices = pd.DataFrame(
        [
            {
                "date": date,
                "ticker": ticker,
                "open": base + i,
                "high": base + i + 1,
                "low": base + i - 1,
                "close": base + i,
                "adj_close": base + i,
                "volume": 1_000_000 + i * 1000,
            }
            for ticker, base in [("AAA", 10), ("BBB", 20)]
            for i, date in enumerate(dates)
        ]
    )
    universe = pd.DataFrame({"yahoo_ticker": ["AAA", "BBB"], "company": ["AAA Inc", "BBB Inc"], "listing_exchange": ["NASDAQ", "NYSE"]})
    metadata = pd.DataFrame({"ticker": ["AAA", "BBB"], "sector": ["Tech", "Health"], "industry": ["Software", "Care"]})
    panel = build_feature_panel_from_frames(prices, universe, metadata)
    assert set(FEATURE_PANEL_COLUMNS).issubset(panel.columns)
    assert panel["dollar_volume"].notna().any()
    assert panel["relative_return_vs_sector_5d"].notna().any()


def test_feature_engineering_keeps_price_valid_symbols_without_metadata():
    dates = pd.date_range("2024-01-01", periods=80, freq="B")
    prices = pd.DataFrame(
        [
            {
                "date": date,
                "ticker": ticker,
                "open": base + i,
                "high": base + i + 1,
                "low": base + i - 1,
                "close": base + i,
                "adj_close": base + i,
                "volume": 1_000_000 + i * 1000,
            }
            for ticker, base in [("AAA", 10), ("BB", 20)]
            for i, date in enumerate(dates)
        ]
    )
    universe = pd.DataFrame(
        {
            "yahoo_ticker": ["AAA", "BB"],
            "company": ["AAA Inc", "BlackBerry Ltd"],
            "listing_exchange": ["NASDAQ", "NYSE"],
        }
    )
    metadata = pd.DataFrame(
        {
            "ticker": ["AAA"],
            "sector": ["Tech"],
            "industry": ["Software"],
            "market_cap": [1_000_000_000],
            "beta": [1.2],
            "country": ["US"],
            "currency": ["USD"],
        }
    )

    panel = build_feature_panel_from_frames(prices, universe, metadata)
    latest = panel.sort_values("date").groupby("ticker").tail(1).set_index("ticker")

    assert "BB" in latest.index
    assert latest.loc["BB", "sector"] == "Unknown"
    assert latest.loc["BB", "industry"] == "Unknown"
    assert latest.loc["BB", "company"] == "BlackBerry Ltd"
    assert latest.loc["BB", "exchange"] == "NYSE"
    assert pd.isna(latest.loc["BB", "market_cap"])
    assert latest.loc["AAA", "market_cap"] == 1_000_000_000
