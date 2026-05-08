from __future__ import annotations

from typing import Optional

import pandas as pd
from sqlalchemy import text

from stockml.db.connection import get_engine


def db_available() -> bool:
    engine = get_engine(required=False)
    if engine is None:
        return False
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False


def _engine():
    return get_engine(required=False)


def table_count(table: str) -> Optional[int]:
    engine = _engine()
    if engine is None:
        return None
    try:
        with engine.connect() as conn:
            return int(conn.execute(text(f"select count(*) from {table}")).scalar() or 0)
    except Exception:
        return None


def latest_model_artifact_source(artifact_type: str) -> str:
    engine = _engine()
    if engine is None:
        return ""
    try:
        with engine.connect() as conn:
            value = conn.execute(
                text(
                    """
                    select source_file
                    from model_artifacts
                    where artifact_type = :artifact_type
                    order by loaded_at desc, artifact_key desc
                    limit 1
                    """
                ),
                {"artifact_type": artifact_type},
            ).scalar()
        return str(value or "")
    except Exception:
        return ""


def model_artifacts(artifact_type: str, limit: int = 5000) -> pd.DataFrame:
    source_file = latest_model_artifact_source(artifact_type)
    if not source_file:
        return pd.DataFrame()
    engine = _engine()
    if engine is None:
        return pd.DataFrame()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    select payload
                    from model_artifacts
                    where artifact_type = :artifact_type
                      and source_file = :source_file
                    order by artifact_key
                    limit :limit
                    """
                ),
                {"artifact_type": artifact_type, "source_file": source_file, "limit": limit},
            ).mappings().all()
        return pd.DataFrame([dict(row["payload"] or {}) for row in rows])
    except Exception:
        return pd.DataFrame()


def panel_summary(dataset: str) -> dict:
    engine = _engine()
    if engine is None:
        return {}
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    select count(*) as row_count,
                           count(distinct ticker) as ticker_count,
                           min(date) as date_min,
                           max(date) as date_max
                    from panel_rows
                    where dataset = :dataset
                    """
                ),
                {"dataset": dataset},
            ).mappings().one()
        return dict(row)
    except Exception:
        return {}


def panel_sample(dataset: str, limit: int = 500) -> pd.DataFrame:
    engine = _engine()
    if engine is None:
        return pd.DataFrame()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    select date, ticker, payload
                    from panel_rows
                    where dataset = :dataset
                    order by date desc, ticker
                    limit :limit
                    """
                ),
                {"dataset": dataset, "limit": limit},
            ).mappings().all()
        records = []
        for row in rows:
            payload = dict(row["payload"] or {})
            payload.setdefault("date", row["date"])
            payload.setdefault("ticker", row["ticker"])
            records.append(payload)
        return pd.DataFrame(records)
    except Exception:
        return pd.DataFrame()


def sector_coverage(dataset: str, limit: int = 20) -> list[dict]:
    engine = _engine()
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    select coalesce(payload->>'sector', 'Unknown') as sector,
                           count(*) as count
                    from panel_rows
                    where dataset = :dataset
                    group by coalesce(payload->>'sector', 'Unknown')
                    order by count desc
                    limit :limit
                    """
                ),
                {"dataset": dataset, "limit": limit},
            ).mappings().all()
        return [dict(row) for row in rows]
    except Exception:
        return []


def latest_gold_for_ticker(ticker: str) -> dict:
    engine = _engine()
    if engine is None:
        return {}
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    select payload
                    from panel_rows
                    where dataset = 'gold_dataset'
                      and ticker = :ticker
                    order by date desc
                    limit 1
                    """
                ),
                {"ticker": ticker.upper()},
            ).mappings().first()
        return dict(row["payload"] or {}) if row else {}
    except Exception:
        return {}


def price_history_for_ticker(ticker: str, limit: int = 50) -> list[dict]:
    engine = _engine()
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    select date, ticker, open, high, low, close, adj_close, volume
                    from price_history
                    where ticker = :ticker
                    order by date desc
                    limit :limit
                    """
                ),
                {"ticker": ticker.upper(), "limit": limit},
            ).mappings().all()
        return [dict(row) for row in rows]
    except Exception:
        return []
