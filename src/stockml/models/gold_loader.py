from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from stockml.common.paths import GOLD_DIR, latest_file
from stockml.gold.target_engineering import TARGET_COLUMNS, leakage_columns

IDENTITY_COLUMNS = {
    "date",
    "ticker",
    "company",
    "exchange",
    "sector",
    "industry",
    "country",
    "currency",
    "sentiment_status",
    "sentiment_source",
    "risk_on_risk_off_flag",
}

REQUIRED_GOLD_COLUMNS = {"date", "ticker", "target_return_5d", "target_trade_label_5d"}


def latest_gold_file(gold_dir: Path = GOLD_DIR) -> Path:
    path = latest_file(gold_dir, "06_us_gold_ml_dataset_*.csv")
    if path is None:
        raise FileNotFoundError("No Gold dataset found. Run scripts/run_gold_pipeline.py first.")
    return path


def load_gold_dataset(
    path: Optional[Path] = None,
    limit_tickers: Optional[int] = None,
    shard_count: int = 1,
    shard_index: int = 0,
) -> pd.DataFrame:
    gold_path = path or latest_gold_file()
    header = pd.read_csv(gold_path, nrows=0)
    missing = REQUIRED_GOLD_COLUMNS - set(header.columns)
    if missing:
        raise ValueError(f"Gold dataset missing required columns: {sorted(missing)}")
    if shard_count > 1:
        if shard_index < 0 or shard_index >= shard_count:
            raise ValueError(f"shard_index must be between 0 and {shard_count - 1}")
        tickers = pd.read_csv(gold_path, usecols=["ticker"], low_memory=False)["ticker"].astype(str).str.upper().str.strip()
        unique_tickers = tickers.drop_duplicates().tolist()
        shard_tickers = set(unique_tickers[shard_index::shard_count])
        if limit_tickers:
            shard_tickers = set(list(shard_tickers)[:limit_tickers])
        frames = []
        for chunk in pd.read_csv(gold_path, chunksize=250_000, low_memory=False):
            chunk["ticker"] = chunk["ticker"].astype(str).str.upper().str.strip()
            chunk = chunk[chunk["ticker"].isin(shard_tickers)]
            if not chunk.empty:
                frames.append(chunk)
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=header.columns)
    else:
        frame = pd.read_csv(gold_path, low_memory=False)
    missing = REQUIRED_GOLD_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"Gold dataset missing required columns: {sorted(missing)}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    frame = frame.dropna(subset=["date", "ticker"]).sort_values(["date", "ticker"]).reset_index(drop=True)
    if limit_tickers:
        tickers = frame["ticker"].drop_duplicates().head(limit_tickers).tolist()
        frame = frame[frame["ticker"].isin(tickers)].copy()
    return frame


def safe_feature_columns(columns: Iterable[str]) -> list[str]:
    blocked = set(leakage_columns(list(columns))) | IDENTITY_COLUMNS | set(TARGET_COLUMNS)
    candidates = [col for col in columns if col not in blocked]
    return candidates


def build_model_matrix(gold: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    trainable = gold.dropna(subset=["target_return_5d"]).copy().reset_index(drop=True)
    y = pd.to_numeric(trainable["target_top_quintile_5d"], errors="coerce").fillna(False).astype(int)
    feature_cols = []
    for col in safe_feature_columns(trainable.columns):
        numeric = pd.to_numeric(trainable[col], errors="coerce")
        if numeric.notna().any() and numeric.nunique(dropna=True) > 1:
            trainable[col] = numeric
            feature_cols.append(col)
    if not feature_cols:
        raise ValueError("Gold dataset has no usable numeric feature columns after leakage filtering.")
    x = trainable[feature_cols].replace([np.inf, -np.inf], np.nan).fillna(0)
    return x, y, feature_cols


def build_prediction_matrix(gold: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    out = pd.DataFrame(index=gold.index)
    for col in feature_cols:
        out[col] = pd.to_numeric(gold[col], errors="coerce") if col in gold.columns else 0
    return out.replace([np.inf, -np.inf], np.nan).fillna(0)
