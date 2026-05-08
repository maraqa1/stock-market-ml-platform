from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
from pandas.api.types import is_scalar
from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from stockml.common.paths import GOLD_DIR, INTERIM_DIR, MODEL_OUTPUTS_DIR, PROCESSED_DIR, RAW_DIR, latest_file
from stockml.db.connection import get_engine
from stockml.db.schema import (
    create_all,
    equity_universe,
    ingestion_runs,
    metadata_enriched,
    model_artifacts,
    panel_rows,
    price_history,
    sentiment_panel,
)


def init_database(database_url: Optional[str] = None) -> None:
    engine = get_engine(database_url)
    create_all(engine)


def load_latest_outputs(database_url: Optional[str] = None) -> Dict[str, int]:
    engine = get_engine(database_url)
    create_all(engine)
    loaded = {}
    with engine.begin() as conn:
        loaded["equity_universe"] = _load_universe(conn, latest_file(INTERIM_DIR, "02_us_tradable_universe_*.csv"))
        loaded["price_history"] = _load_price_history(conn, RAW_DIR / "03_us_price_history_store.csv")
        loaded["metadata_enriched"] = _load_metadata(conn, latest_file(INTERIM_DIR, "04_us_metadata_enriched_*.csv"))
        loaded["feature_panel"] = _load_panel(conn, "feature_panel", latest_file(PROCESSED_DIR, "05_us_feature_panel_*.csv"))
        loaded["sentiment_panel"] = _load_sentiment(conn, latest_file(PROCESSED_DIR, "05_news_sentiment_panel_*.csv"))
        loaded["gold_dataset"] = _load_panel(conn, "gold_dataset", latest_file(GOLD_DIR, "06_us_gold_ml_dataset_*.csv"))
        loaded.update(_load_model_outputs(conn))
    return loaded


def _read(path: Optional[Path]) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


def _clean_payload(row: dict) -> dict:
    cleaned = {}
    for key, value in row.items():
        if value is None:
            cleaned[key] = None
        elif is_scalar(value) and pd.isna(value):
            cleaned[key] = None
        elif isinstance(value, (pd.Timestamp, datetime, date)):
            cleaned[key] = value.isoformat()
        elif hasattr(value, "item"):
            item = value.item()
            if item is None or (is_scalar(item) and pd.isna(item)):
                cleaned[key] = None
            elif isinstance(item, (datetime, date)):
                cleaned[key] = item.isoformat()
            else:
                cleaned[key] = item
        else:
            cleaned[key] = value
    return cleaned


def _upsert_rows(conn, table, rows: list[dict], conflict_cols: list[str]) -> int:
    if not rows:
        return 0
    dialect = conn.engine.dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(table).values(rows)
        update_cols = {
            col.name: getattr(stmt.excluded, col.name)
            for col in table.columns
            if col.name not in set(conflict_cols) | {"loaded_at", "id"}
        }
        conn.execute(stmt.on_conflict_do_update(index_elements=conflict_cols, set_=update_cols))
    else:
        for row in rows:
            where = [getattr(table.c, col) == row[col] for col in conflict_cols]
            conn.execute(delete(table).where(*where))
        conn.execute(insert(table), rows)
    return len(rows)


def _record_run(conn, name: str, source_file: Optional[Path], row_count: int, status: str = "ok", message: str = "") -> None:
    conn.execute(
        insert(ingestion_runs).values(
            pipeline_name=name,
            profile=None,
            status=status,
            source_file=str(source_file) if source_file else "",
            row_count=row_count,
            message=message,
        )
    )


def _load_universe(conn, path: Optional[Path]) -> int:
    frame = _read(path)
    if frame.empty:
        _record_run(conn, "db_load_equity_universe", path, 0, status="missing")
        return 0
    if "yahoo_ticker" not in frame.columns:
        _record_run(conn, "db_load_equity_universe", path, 0, status="error", message="missing yahoo_ticker")
        return 0
    rows = []
    for row in frame.to_dict("records"):
        payload = _clean_payload(row)
        rows.append(
            {
                "symbol": str(row.get("symbol", row.get("yahoo_ticker", ""))).upper(),
                "yahoo_ticker": str(row.get("yahoo_ticker", "")).upper(),
                "company": row.get("company"),
                "listing_exchange": row.get("listing_exchange"),
                "is_tradable_common_stock_candidate": bool(row.get("is_tradable_common_stock_candidate", True)),
                "exclude_reason": row.get("exclude_reason"),
                "payload": payload,
                "source_file": str(path),
            }
        )
    count = _upsert_rows(conn, equity_universe, rows, ["yahoo_ticker"])
    _record_run(conn, "db_load_equity_universe", path, count)
    return count


def _load_price_history(conn, path: Optional[Path]) -> int:
    frame = _read(path)
    if frame.empty:
        _record_run(conn, "db_load_price_history", path, 0, status="missing")
        return 0
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    rows = []
    for row in frame.dropna(subset=["date", "ticker"]).to_dict("records"):
        payload = _clean_payload(row)
        rows.append(
            {
                "date": row["date"],
                "ticker": str(row["ticker"]).upper(),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "adj_close": row.get("adj_close"),
                "volume": row.get("volume"),
                "source": row.get("source"),
                "payload": payload,
                "source_file": str(path),
            }
        )
    count = _upsert_rows(conn, price_history, rows, ["date", "ticker"])
    _record_run(conn, "db_load_price_history", path, count)
    return count


def _load_metadata(conn, path: Optional[Path]) -> int:
    frame = _read(path)
    if frame.empty:
        _record_run(conn, "db_load_metadata", path, 0, status="missing")
        return 0
    rows = []
    for row in frame.dropna(subset=["ticker"]).to_dict("records"):
        payload = _clean_payload(row)
        rows.append(
            {
                "ticker": str(row["ticker"]).upper(),
                "company": row.get("company"),
                "exchange": row.get("exchange"),
                "sector": row.get("sector"),
                "industry": row.get("industry"),
                "market_cap": row.get("market_cap"),
                "metadata_status": row.get("metadata_status"),
                "payload": payload,
                "source_file": str(path),
            }
        )
    count = _upsert_rows(conn, metadata_enriched, rows, ["ticker"])
    _record_run(conn, "db_load_metadata", path, count)
    return count


def _load_panel(conn, dataset: str, path: Optional[Path]) -> int:
    frame = _read(path)
    if frame.empty:
        _record_run(conn, f"db_load_{dataset}", path, 0, status="missing")
        return 0
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    rows = []
    for row in frame.dropna(subset=["date", "ticker"]).to_dict("records"):
        rows.append(
            {
                "dataset": dataset,
                "date": row["date"],
                "ticker": str(row["ticker"]).upper(),
                "payload": _clean_payload(row),
                "source_file": str(path),
            }
        )
    count = _upsert_rows(conn, panel_rows, rows, ["dataset", "date", "ticker"])
    _record_run(conn, f"db_load_{dataset}", path, count)
    return count


def _load_sentiment(conn, path: Optional[Path]) -> int:
    frame = _read(path)
    if frame.empty:
        _record_run(conn, "db_load_sentiment", path, 0, status="missing")
        return 0
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    rows = []
    for row in frame.dropna(subset=["date", "ticker"]).to_dict("records"):
        rows.append(
            {
                "date": row["date"],
                "ticker": str(row["ticker"]).upper(),
                "article_count": row.get("article_count"),
                "sentiment_score_mean": row.get("sentiment_score_mean"),
                "sentiment_source": row.get("sentiment_source"),
                "sentiment_status": row.get("sentiment_status"),
                "payload": _clean_payload(row),
                "source_file": str(path),
            }
        )
    count = _upsert_rows(conn, sentiment_panel, rows, ["date", "ticker"])
    _record_run(conn, "db_load_sentiment", path, count)
    return count


def _load_model_outputs(conn) -> Dict[str, int]:
    patterns = {
        "latest_predictions": "advanced_model_latest_predictions_*.csv",
        "signal_table": "advanced_model_signal_table_*.csv",
        "top_long_signals": "advanced_model_top_long_signals_*.csv",
        "top_short_signals": "advanced_model_top_short_signals_*.csv",
        "validation_leaderboard": "advanced_model_validation_leaderboard_*.csv",
        "confidence_bucket_performance": "advanced_model_confidence_bucket_performance_*.csv",
        "feature_importance": "advanced_model_feature_importance_*.csv",
        "model_status": "advanced_model_model_status_*.csv",
        "data_dictionary": "advanced_model_data_dictionary_*.csv",
    }
    counts = {}
    for artifact_type, pattern in patterns.items():
        path = latest_file(MODEL_OUTPUTS_DIR, pattern)
        frame = _read(path)
        if frame.empty:
            _record_run(conn, f"db_load_model_{artifact_type}", path, 0, status="missing")
            counts[f"model_{artifact_type}"] = 0
            continue
        rows = []
        for idx, row in enumerate(frame.to_dict("records")):
            date_value = pd.to_datetime(row.get("date"), errors="coerce")
            rows.append(
                {
                    "artifact_type": artifact_type,
                    "artifact_key": f"{Path(path).name}:{idx}" if path else f"{artifact_type}:{idx}",
                    "date": date_value.date() if pd.notna(date_value) else None,
                    "ticker": str(row.get("ticker", "")).upper() if row.get("ticker") else None,
                    "payload": _clean_payload(row),
                    "source_file": str(path),
                }
            )
        count = _upsert_rows(conn, model_artifacts, rows, ["artifact_type", "artifact_key"])
        _record_run(conn, f"db_load_model_{artifact_type}", path, count)
        counts[f"model_{artifact_type}"] = count
    return counts
