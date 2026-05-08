from __future__ import annotations

from pathlib import Path
from typing import Optional

from portal.services.database_reader import panel_sample, panel_summary, sector_coverage
from portal.services.latest_file_reader import count_rows, file_status, latest_file, safe_read_csv


FEATURE_GROUPS = {
    "price": ["open", "high", "low", "close", "adj_close", "volume"],
    "technical": ["sma_20", "sma_50", "rsi_14", "macd"],
    "liquidity": ["dollar_volume", "avg_dollar_volume_20d", "liquidity_score"],
    "volatility": ["volatility_20d", "volatility_60d", "risk_score"],
    "sector_relative": ["sector_return_5d", "relative_return_vs_sector_5d", "sector_relative_strength_score"],
    "market_context": ["market_return_5d", "market_volatility_20d", "market_regime_score"],
    "sentiment": ["article_count", "sentiment_score_mean", "sentiment_status"],
    "candidate_selection": ["selection_score", "candidate_rank_overall", "candidate_rank_by_sector"],
    "targets": ["target_return_5d", "target_trade_label_5d"],
}


def gold_context(root: Optional[Path] = None) -> dict:
    gold_file = latest_file(root, "gold", "06_us_gold_ml_dataset_*.csv")
    quality_file = latest_file(root, "interim", "06_us_gold_quality_*.csv")
    dictionary_file = latest_file(root, "interim", "06_us_gold_data_dictionary_*.csv")
    summary = panel_summary("gold_dataset")
    db_sample = panel_sample("gold_dataset", limit=5000)
    using_db = bool(summary.get("row_count")) and not db_sample.empty
    sample = db_sample if using_db else safe_read_csv(gold_file, nrows=5000)
    row_count = int(summary.get("row_count") or 0) if using_db else count_rows(gold_file)
    ticker_count = int(summary.get("ticker_count") or 0) if using_db else (sample["ticker"].nunique() if "ticker" in sample.columns else 0)
    if not using_db and row_count > len(sample) and ticker_count:
        ticker_count = f"{ticker_count}+"

    coverage = []
    for group, cols in FEATURE_GROUPS.items():
        present = [col for col in cols if col in sample.columns]
        missing_ratio = sample[present].isna().mean().mean() if present and not sample.empty else 1
        coverage.append({"group": group.replace("_", " ").title(), "present": len(present), "expected": len(cols), "missing_ratio": round(float(missing_ratio), 4)})

    sentiment_warning = ""
    if "sentiment_status" not in sample.columns or sample.empty:
        sentiment_warning = "Sentiment data is unavailable."
    elif "article_count" in sample.columns and sample["article_count"].fillna(0).sum() == 0:
        sentiment_warning = "Sentiment coverage is currently low or unavailable."

    return {
        "gold_file": file_status(gold_file, "Gold dataset"),
        "quality_file": file_status(quality_file, "Gold quality"),
        "dictionary_file": file_status(dictionary_file, "Gold dictionary"),
        "row_count": row_count,
        "ticker_count": ticker_count,
        "date_min": summary.get("date_min", "") if using_db else (sample["date"].min() if "date" in sample.columns and not sample.empty else ""),
        "date_max": summary.get("date_max", "") if using_db else (sample["date"].max() if "date" in sample.columns and not sample.empty else ""),
        "sector_coverage": sector_coverage("gold_dataset") if using_db else (sample["sector"].fillna("Unknown").value_counts().head(20).reset_index().to_dict("records") if "sector" in sample.columns else []),
        "feature_group_coverage": coverage,
        "sentiment_warning": sentiment_warning,
        "sample_rows": sample.tail(50).to_dict("records"),
        "files": [file_status(gold_file, "Gold dataset"), file_status(quality_file, "Gold quality"), file_status(dictionary_file, "Gold dictionary")],
        "data_source": "PostgreSQL" if using_db else "CSV",
    }
