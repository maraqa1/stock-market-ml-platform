from __future__ import annotations

from pathlib import Path
from typing import Optional

from portal.services.latest_file_reader import file_status, latest_file, safe_read_csv


def _exchange_col(df):
    for col in ["listing_exchange", "exchange", "Exchange"]:
        if col in df.columns:
            return col
    return None


def universe_context(root: Optional[Path] = None) -> dict:
    raw_file = latest_file(root, "raw", "01_us_equity_universe_*.csv")
    tradable_file = latest_file(root, "interim", "02_us_tradable_universe_*.csv")
    summary_file = latest_file(root, "interim", "02_us_universe_summary_*.csv")
    raw = safe_read_csv(raw_file)
    tradable = safe_read_csv(tradable_file)
    summary = safe_read_csv(summary_file)

    raw_exchange = raw[_exchange_col(raw)].value_counts().reset_index().to_dict("records") if not raw.empty and _exchange_col(raw) else []
    tradable_exchange = tradable[_exchange_col(tradable)].value_counts().reset_index().to_dict("records") if not tradable.empty and _exchange_col(tradable) else []
    excluded = []
    if "exclude_reason" in raw.columns:
        excluded = raw["exclude_reason"].fillna("included").replace("", "included").value_counts().reset_index().to_dict("records")
    elif not summary.empty:
        excluded = summary.head(20).to_dict("records")

    return {
        "raw_count": len(raw),
        "tradable_count": len(tradable),
        "raw_by_exchange": raw_exchange,
        "tradable_by_exchange": tradable_exchange,
        "excluded_summary": excluded,
        "sample_tickers": tradable.head(50).to_dict("records") if not tradable.empty else raw.head(50).to_dict("records"),
        "files": [file_status(raw_file, "Raw universe"), file_status(tradable_file, "Tradable universe"), file_status(summary_file, "Universe summary")],
    }

