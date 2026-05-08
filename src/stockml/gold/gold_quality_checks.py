from __future__ import annotations

import pandas as pd

from stockml.gold.target_engineering import leakage_columns


def build_gold_quality(gold: pd.DataFrame) -> pd.DataFrame:
    summary = {
        "row_count": len(gold),
        "ticker_count": gold["ticker"].nunique() if "ticker" in gold.columns else 0,
        "min_date": gold["date"].min() if "date" in gold.columns and not gold.empty else pd.NA,
        "max_date": gold["date"].max() if "date" in gold.columns and not gold.empty else pd.NA,
        "long_count": int((gold.get("target_trade_label_5d") == "Long").sum()) if "target_trade_label_5d" in gold.columns else 0,
        "short_count": int((gold.get("target_trade_label_5d") == "Short").sum()) if "target_trade_label_5d" in gold.columns else 0,
        "neutral_count": int((gold.get("target_trade_label_5d") == "Neutral").sum()) if "target_trade_label_5d" in gold.columns else 0,
        "leakage_column_count": len(leakage_columns(list(gold.columns))),
    }
    return pd.DataFrame([summary])


def build_data_dictionary(gold: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in gold.columns:
        if col.startswith("target_"):
            group = "target"
        elif col.startswith("sentiment") or col in {"article_count", "news_attention_score"}:
            group = "sentiment"
        elif col in {"sector", "industry", "company", "exchange", "country", "currency"}:
            group = "metadata"
        elif "rank" in col or "score" in col:
            group = "candidate_selection"
        else:
            group = "feature"
        rows.append({"column": col, "group": group, "dtype": str(gold[col].dtype)})
    return pd.DataFrame(rows)

