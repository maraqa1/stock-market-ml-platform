from __future__ import annotations

from datetime import date, datetime
import math
import os
from pathlib import Path
from typing import Dict, Optional, Set

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

CSV_CHUNK_SIZE = int(os.environ.get("STOCKML_DB_CSV_CHUNK_SIZE", "25000"))
DB_BATCH_SIZE = int(os.environ.get("STOCKML_DB_BATCH_SIZE", "1000"))


def init_database(database_url: Optional[str] = None) -> None:
    engine = get_engine(database_url)
    create_all(engine)


def load_latest_outputs(database_url: Optional[str] = None, skip: Optional[Set[str]] = None) -> Dict[str, int]:
    engine = get_engine(database_url)
    create_all(engine)
    skip = skip or set()
    loaded = {}
    if "equity_universe" not in skip:
        with engine.begin() as conn:
            loaded["equity_universe"] = _load_universe(conn, latest_file(INTERIM_DIR, "02_us_tradable_universe_*.csv"))
    if "price_history" not in skip:
        loaded["price_history"] = _load_price_history_streaming(engine, RAW_DIR / "03_us_price_history_store.csv")
    if "metadata_enriched" not in skip:
        with engine.begin() as conn:
            loaded["metadata_enriched"] = _load_metadata(conn, latest_file(INTERIM_DIR, "04_us_metadata_enriched_*.csv"))
    if "feature_panel" not in skip:
        loaded["feature_panel"] = _load_panel_streaming(
            engine, "feature_panel", latest_file(PROCESSED_DIR, "05_us_feature_panel_*.csv")
        )
    if "sentiment_panel" not in skip:
        with engine.begin() as conn:
            loaded["sentiment_panel"] = _load_sentiment(conn, latest_file(PROCESSED_DIR, "05_news_sentiment_panel_*.csv"))
    if "gold_dataset" not in skip:
        loaded["gold_dataset"] = _load_panel_streaming(
            engine, "gold_dataset", latest_file(GOLD_DIR, "06_us_gold_ml_dataset_*.csv")
        )
    if "model_outputs" not in skip:
        with engine.begin() as conn:
            loaded.update(_load_model_outputs(conn))
    return loaded


def _read(path: Optional[Path]) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


def _read_chunks(path: Optional[Path], chunksize: int = CSV_CHUNK_SIZE):
    if path is None or not path.exists():
        return
    yield from pd.read_csv(path, low_memory=False, chunksize=chunksize)


def _batches(rows: list[dict], size: int = DB_BATCH_SIZE):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _clean_payload(row: dict) -> dict:
    cleaned = {}
    for key, value in row.items():
        scalar = _db_value(value)
        if scalar is None:
            cleaned[key] = None
        elif isinstance(value, (pd.Timestamp, datetime, date)):
            cleaned[key] = value.isoformat()
        elif hasattr(value, "item"):
            item = scalar
            if isinstance(item, (datetime, date)):
                cleaned[key] = item.isoformat()
            else:
                cleaned[key] = item
        else:
            cleaned[key] = scalar
    return cleaned


def _db_value(value):
    if value is None:
        return None
    if is_scalar(value) and pd.isna(value):
        return None
    if hasattr(value, "item"):
        item = value.item()
        if item is None or (is_scalar(item) and pd.isna(item)):
            return None
        if isinstance(item, float) and not math.isfinite(item):
            return None
        return item
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _db_float(value) -> Optional[float]:
    cleaned = _db_value(value)
    if cleaned is None:
        return None
    return float(cleaned)


def _db_int(value) -> Optional[int]:
    cleaned = _db_value(value)
    if cleaned is None:
        return None
    return int(cleaned)


def _db_text(value) -> Optional[str]:
    cleaned = _db_value(value)
    if cleaned is None:
        return None
    return str(cleaned)


def _db_bool(value) -> Optional[bool]:
    cleaned = _db_value(value)
    if cleaned is None:
        return None
    if isinstance(cleaned, str):
        lowered = cleaned.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
        return None
    return bool(cleaned)


def _upsert_rows(conn, table, rows: list[dict], conflict_cols: list[str]) -> int:
    if not rows:
        return 0
    count = 0
    for batch in _batches(rows):
        _upsert_batch(conn, table, batch, conflict_cols)
        count += len(batch)
    return count


def _upsert_batch(conn, table, rows: list[dict], conflict_cols: list[str]) -> None:
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
                "company": _db_text(row.get("company")),
                "listing_exchange": _db_text(row.get("listing_exchange")),
                "is_tradable_common_stock_candidate": _db_bool(
                    row.get("is_tradable_common_stock_candidate", True)
                ),
                "exclude_reason": _db_text(row.get("exclude_reason")),
                "payload": payload,
                "source_file": str(path),
            }
        )
    count = _upsert_rows(conn, equity_universe, rows, ["yahoo_ticker"])
    _record_run(conn, "db_load_equity_universe", path, count)
    return count


def _load_price_history(conn, path: Optional[Path]) -> int:
    if path is None or not path.exists():
        _record_run(conn, "db_load_price_history", path, 0, status="missing")
        return 0
    count = 0
    for frame in _read_chunks(path):
        if frame.empty:
            continue
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        rows = []
        for row in frame.dropna(subset=["date", "ticker"]).to_dict("records"):
            payload = _clean_payload(row)
            rows.append(
                {
                    "date": row["date"],
                    "ticker": str(row["ticker"]).upper(),
                    "open": _db_float(row.get("open")),
                    "high": _db_float(row.get("high")),
                    "low": _db_float(row.get("low")),
                    "close": _db_float(row.get("close")),
                    "adj_close": _db_float(row.get("adj_close")),
                    "volume": _db_int(row.get("volume")),
                    "source": _db_text(row.get("source")),
                    "payload": payload,
                    "source_file": str(path),
                }
            )
        count += _upsert_rows(conn, price_history, rows, ["date", "ticker"])
    if count == 0:
        _record_run(conn, "db_load_price_history", path, 0, status="empty")
        return 0
    _record_run(conn, "db_load_price_history", path, count)
    return count


def _price_history_rows(frame: pd.DataFrame, path: Path) -> list[dict]:
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    rows = []
    for row in frame.dropna(subset=["date", "ticker"]).to_dict("records"):
        payload = _clean_payload(row)
        rows.append(
            {
                "date": row["date"],
                "ticker": str(row["ticker"]).upper(),
                "open": _db_float(row.get("open")),
                "high": _db_float(row.get("high")),
                "low": _db_float(row.get("low")),
                "close": _db_float(row.get("close")),
                "adj_close": _db_float(row.get("adj_close")),
                "volume": _db_int(row.get("volume")),
                "source": _db_text(row.get("source")),
                "payload": payload,
                "source_file": str(path),
            }
        )
    return rows


def _load_price_history_streaming(engine, path: Optional[Path]) -> int:
    if path is None or not path.exists():
        with engine.begin() as conn:
            _record_run(conn, "db_load_price_history", path, 0, status="missing")
        return 0
    count = 0
    for chunk_number, frame in enumerate(_read_chunks(path), start=1):
        if frame.empty:
            continue
        rows = _price_history_rows(frame, path)
        with engine.begin() as conn:
            count += _upsert_rows(conn, price_history, rows, ["date", "ticker"])
        print(f"db_load_price_history chunk={chunk_number} total_rows={count}", flush=True)
    with engine.begin() as conn:
        if count == 0:
            _record_run(conn, "db_load_price_history", path, 0, status="empty")
            return 0
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
                    "company": _db_text(row.get("company")),
                    "exchange": _db_text(row.get("exchange")),
                    "sector": _db_text(row.get("sector")),
                    "industry": _db_text(row.get("industry")),
                    "market_cap": _db_float(row.get("market_cap")),
                    "metadata_status": _db_text(row.get("metadata_status")),
                    "payload": payload,
                    "source_file": str(path),
                }
        )
    count = _upsert_rows(conn, metadata_enriched, rows, ["ticker"])
    _record_run(conn, "db_load_metadata", path, count)
    return count


def _load_panel(conn, dataset: str, path: Optional[Path]) -> int:
    if path is None or not path.exists():
        _record_run(conn, f"db_load_{dataset}", path, 0, status="missing")
        return 0
    count = 0
    for frame in _read_chunks(path):
        if frame.empty:
            continue
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
        count += _upsert_rows(conn, panel_rows, rows, ["dataset", "date", "ticker"])
    if count == 0:
        _record_run(conn, f"db_load_{dataset}", path, 0, status="empty")
        return 0
    _record_run(conn, f"db_load_{dataset}", path, count)
    return count


def _panel_rows(dataset: str, frame: pd.DataFrame, path: Path) -> list[dict]:
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
    return rows


def _load_panel_streaming(engine, dataset: str, path: Optional[Path]) -> int:
    if path is None or not path.exists():
        with engine.begin() as conn:
            _record_run(conn, f"db_load_{dataset}", path, 0, status="missing")
        return 0
    count = 0
    for chunk_number, frame in enumerate(_read_chunks(path), start=1):
        if frame.empty:
            continue
        rows = _panel_rows(dataset, frame, path)
        with engine.begin() as conn:
            count += _upsert_rows(conn, panel_rows, rows, ["dataset", "date", "ticker"])
        print(f"db_load_{dataset} chunk={chunk_number} total_rows={count}", flush=True)
    with engine.begin() as conn:
        if count == 0:
            _record_run(conn, f"db_load_{dataset}", path, 0, status="empty")
            return 0
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
                    "article_count": _db_int(row.get("article_count")),
                    "sentiment_score_mean": _db_float(row.get("sentiment_score_mean")),
                    "sentiment_source": _db_text(row.get("sentiment_source")),
                    "sentiment_status": _db_text(row.get("sentiment_status")),
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
                    "ticker": str(row.get("ticker", "")).upper() if _db_value(row.get("ticker")) else None,
                    "payload": _clean_payload(row),
                    "source_file": str(path),
                }
            )
        count = _upsert_rows(conn, model_artifacts, rows, ["artifact_type", "artifact_key"])
        _record_run(conn, f"db_load_model_{artifact_type}", path, count)
        counts[f"model_{artifact_type}"] = count
    return counts
