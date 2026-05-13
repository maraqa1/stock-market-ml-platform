from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from portal.services.database_reader import panel_sample, panel_summary, sector_coverage, table_count
from portal.services.gold_service import FEATURE_GROUPS
from portal.services.latest_file_reader import count_rows, file_status, latest_file, project_root, safe_read_csv


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATASETS = [
    {"key": "raw_universe", "label": "Raw Universe", "kind": "universe", "file_key": "raw", "pattern": "01_us_equity_universe_*.csv", "db_table": "equity_universe"},
    {"key": "tradable_universe", "label": "Tradable Universe", "kind": "universe", "file_key": "interim", "pattern": "02_us_tradable_universe_*.csv"},
    {"key": "validated_universe", "label": "Price Validated Universe", "kind": "quality", "file_key": "interim", "pattern": "03_us_price_validated_universe_*.csv"},
    {"key": "metadata", "label": "Metadata Enrichment", "kind": "reference", "file_key": "interim", "pattern": "04_us_metadata_enriched_*.csv", "db_table": "metadata_enriched"},
    {"key": "feature_panel", "label": "Feature Panel", "kind": "panel", "file_key": "processed", "pattern": "05_us_feature_panel_*.csv", "panel_dataset": "feature_panel"},
    {"key": "sentiment_panel", "label": "Sentiment Panel", "kind": "panel", "file_key": "processed", "pattern": "05_news_sentiment_panel_*.csv", "db_table": "sentiment_panel"},
    {"key": "gold_dataset", "label": "Gold Dataset", "kind": "gold", "file_key": "gold", "pattern": "06_us_gold_ml_dataset_*.csv", "panel_dataset": "gold_dataset"},
    {"key": "model_predictions", "label": "Model Predictions", "kind": "model", "file_key": "model_outputs", "pattern": "advanced_model_latest_predictions_*.csv", "fallback": "model_predictions_latest.csv"},
    {"key": "signal_table", "label": "Signal Table", "kind": "model", "file_key": "model_outputs", "pattern": "advanced_model_signal_table_*.csv"},
    {"key": "candidate_pool", "label": "Paper Candidate Pool", "kind": "trading", "file_key": "portal_outputs", "pattern": "08_alpaca_paper_candidate_pool_*.csv", "db_table": "shortlist_snapshots"},
    {"key": "order_plan", "label": "Paper Order Plan", "kind": "trading", "file_key": "portal_outputs", "pattern": "08_alpaca_paper_order_plan_*.csv"},
    {"key": "positions", "label": "Paper Positions", "kind": "trading", "file_key": "portal_outputs", "pattern": "08_alpaca_paper_positions_*.csv"},
    {"key": "near_miss", "label": "Near Miss Analysis", "kind": "diagnostic", "file_key": "near_miss", "pattern": "near_miss_*.csv"},
]


def _should_use_database(root: Path) -> bool:
    try:
        return project_root(root).resolve() == PROJECT_ROOT.resolve()
    except Exception:
        return False


def _dataset_spec(key: str | None) -> dict[str, Any]:
    for spec in DATASETS:
        if spec["key"] == key:
            return spec
    return next(spec for spec in DATASETS if spec["key"] == "gold_dataset")


def _latest_dataset_file(root: Path, spec: dict[str, Any]) -> Path | None:
    path = latest_file(root, spec["file_key"], spec["pattern"])
    if path is None and spec.get("fallback"):
        candidate = project_root(root) / "data" / spec["file_key"] / spec["fallback"]
        if candidate.exists():
            return candidate
    return path


def _file_timestamp(path: Path | None) -> str:
    if not path:
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def _db_panel_summary(spec: dict[str, Any]) -> dict[str, Any]:
    dataset = spec.get("panel_dataset")
    if not dataset:
        return {}
    return panel_summary(dataset)


def _db_count(spec: dict[str, Any]) -> int | None:
    if spec.get("panel_dataset"):
        summary = _db_panel_summary(spec)
        if summary.get("row_count"):
            return int(summary["row_count"])
    if spec.get("db_table"):
        return table_count(spec["db_table"])
    return None


def _csv_ticker_count(frame: pd.DataFrame) -> int:
    for col in ["ticker", "symbol", "yahoo_ticker"]:
        if col in frame.columns:
            return int(frame[col].dropna().astype(str).str.upper().nunique())
    return 0


def _csv_date_range(frame: pd.DataFrame) -> tuple[str, str]:
    for col in ["date", "prediction_date", "run_timestamp"]:
        if col in frame.columns and not frame.empty:
            return str(frame[col].min()), str(frame[col].max())
    return "", ""


def _dataset_inventory_row(root: Path, spec: dict[str, Any], use_db: bool) -> dict[str, Any]:
    path = _latest_dataset_file(root, spec)
    frame = safe_read_csv(path, nrows=5000)
    db_summary = _db_panel_summary(spec) if use_db else {}
    db_count = _db_count(spec) if use_db else None
    row_count = int(db_count) if db_count else count_rows(path)
    ticker_count = int(db_summary.get("ticker_count") or 0) if db_summary else _csv_ticker_count(frame)
    csv_min, csv_max = _csv_date_range(frame)
    return {
        "key": spec["key"],
        "label": spec["label"],
        "kind": spec["kind"],
        "source": "PostgreSQL" if db_count else "CSV",
        "rows": row_count,
        "tickers": ticker_count,
        "date_min": str(db_summary.get("date_min") or csv_min or ""),
        "date_max": str(db_summary.get("date_max") or csv_max or ""),
        "freshness": _file_timestamp(path),
        "file_name": path.name if path else "Missing",
        "exists": bool(path),
    }


def _feature_group_coverage(sample: pd.DataFrame) -> list[dict[str, Any]]:
    coverage = []
    for group, cols in FEATURE_GROUPS.items():
        present = [col for col in cols if col in sample.columns]
        missing_ratio = sample[present].isna().mean().mean() if present and not sample.empty else 1
        coverage.append(
            {
                "group": group.replace("_", " ").title(),
                "present": len(present),
                "expected": len(cols),
                "missing_ratio": round(float(missing_ratio), 4),
            }
        )
    return coverage


def _sample_rows(root: Path, spec: dict[str, Any], use_db: bool) -> tuple[list[dict[str, Any]], list[str]]:
    frame = pd.DataFrame()
    if use_db and spec.get("panel_dataset"):
        frame = panel_sample(spec["panel_dataset"], limit=100)
    if frame.empty:
        frame = safe_read_csv(_latest_dataset_file(root, spec), nrows=100)
    if frame.empty:
        return [], []
    columns = [col for col in frame.columns[:14]]
    return frame.tail(50).to_dict("records"), columns


def _gold_sample(root: Path, use_db: bool) -> pd.DataFrame:
    spec = _dataset_spec("gold_dataset")
    frame = panel_sample("gold_dataset", limit=5000) if use_db else pd.DataFrame()
    if frame.empty:
        frame = safe_read_csv(_latest_dataset_file(root, spec), nrows=5000)
    return frame


def data_estate_context(root: Path | None = None, selected_dataset: str | None = None) -> dict[str, Any]:
    resolved_root = project_root(root)
    use_db = _should_use_database(resolved_root)
    selected = _dataset_spec(selected_dataset)
    inventory = [_dataset_inventory_row(resolved_root, spec, use_db) for spec in DATASETS]
    gold = next((row for row in inventory if row["key"] == "gold_dataset"), {})
    sample_rows, sample_columns = _sample_rows(resolved_root, selected, use_db)
    gold_frame = _gold_sample(resolved_root, use_db)
    files = [file_status(_latest_dataset_file(resolved_root, spec), spec["label"]) for spec in DATASETS]
    return {
        "datasets": inventory,
        "selected_dataset": selected["key"],
        "selected_label": selected["label"],
        "sample_rows": sample_rows,
        "sample_columns": sample_columns,
        "gold": gold,
        "gold_feature_coverage": _feature_group_coverage(gold_frame),
        "gold_sector_coverage": sector_coverage("gold_dataset") if use_db and gold.get("source") == "PostgreSQL" else (
            gold_frame["sector"].fillna("Unknown").value_counts().head(20).reset_index().to_dict("records") if "sector" in gold_frame.columns else []
        ),
        "files": files,
        "data_source": "PostgreSQL + CSV artifacts" if use_db else "CSV artifacts",
    }
