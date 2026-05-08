import pandas as pd

from stockml.metadata.yahoo_metadata import METADATA_COLUMNS, build_metadata_quality, empty_metadata_row


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

