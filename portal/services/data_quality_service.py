from __future__ import annotations

from pathlib import Path
from typing import Optional

from portal.services.latest_file_reader import file_status, latest_file, safe_read_csv


def data_quality_context(root: Optional[Path] = None) -> dict:
    price_file = latest_file(root, "interim", "03_us_price_history_quality_*.csv")
    validated_file = latest_file(root, "interim", "03_us_price_validated_universe_*.csv")
    failure_file = latest_file(root, "interim", "03_us_price_download_failures_*.csv")
    metadata_file = latest_file(root, "interim", "04_us_metadata_quality_*.csv")
    sentiment_file = latest_file(root, "interim", "05_news_sentiment_quality_*.csv")
    price = safe_read_csv(price_file)
    validated = safe_read_csv(validated_file)
    metadata = safe_read_csv(metadata_file)
    sentiment = safe_read_csv(sentiment_file)

    status_counts = price["price_quality_status"].fillna("unknown").value_counts().reset_index().to_dict("records") if "price_quality_status" in price.columns else []
    liquidity_counts = price["passes_liquidity_filter"].fillna(False).value_counts().reset_index().to_dict("records") if "passes_liquidity_filter" in price.columns else []
    metadata_counts = metadata["metadata_status"].fillna("unknown").value_counts().reset_index().to_dict("records") if "metadata_status" in metadata.columns else []
    sentiment_counts = sentiment["sentiment_status"].fillna("unknown").value_counts().reset_index().to_dict("records") if "sentiment_status" in sentiment.columns else []

    return {
        "price_status_counts": status_counts,
        "validated_count": len(validated),
        "rejected_count": max(0, len(price) - len(validated)),
        "missing_close_total": int(price["missing_close_count"].fillna(0).sum()) if "missing_close_count" in price.columns else 0,
        "missing_volume_total": int(price["missing_volume_count"].fillna(0).sum()) if "missing_volume_count" in price.columns else 0,
        "min_price_date": price["min_date"].min() if "min_date" in price.columns and not price.empty else "",
        "max_price_date": price["max_date"].max() if "max_date" in price.columns and not price.empty else "",
        "liquidity_counts": liquidity_counts,
        "metadata_counts": metadata_counts,
        "sentiment_counts": sentiment_counts,
        "failure_sample": safe_read_csv(failure_file, nrows=25).to_dict("records"),
        "files": [
            file_status(price_file, "Price quality"),
            file_status(validated_file, "Validated universe"),
            file_status(failure_file, "Price failures"),
            file_status(metadata_file, "Metadata quality"),
            file_status(sentiment_file, "Sentiment quality"),
        ],
    }

