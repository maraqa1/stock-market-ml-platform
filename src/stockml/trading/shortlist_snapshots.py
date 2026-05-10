from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, Engine

from stockml.db.connection import get_engine
from stockml.db.schema import pipeline_runs, shortlist_snapshots


def _text(value: Any) -> str:
    if value is None:
        return ""
    exc_info = (None, None, None)
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        if pd.isna(parsed):
            return default
        return parsed
    except Exception:
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _bias(row: dict[str, Any]) -> str:
    value = _text(row.get("bias") or row.get("trade_action") or row.get("side")).lower()
    if value in {"long", "buy"}:
        return "long"
    if value in {"short", "sell"}:
        return "short"
    return "neutral"


def _in_basket(row: dict[str, Any]) -> bool:
    status = _text(row.get("trade_quality_status")).lower()
    return _bool(row.get("in_basket") or row.get("order_eligible")) and status in {"approved", "reduced", "trimmed"}


def _normalize_rows(frame: pd.DataFrame, run_id: str) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    rows = []
    for index, row in enumerate(frame.fillna("").to_dict("records"), start=1):
        symbol = _text(row.get("symbol") or row.get("ticker")).upper()
        if not symbol:
            continue
        rank = int(_num(row.get("rank") or row.get("candidate_rank") or row.get("rank_overall"), index))
        rows.append(
            {
                "run_id": run_id,
                "rank": rank,
                "symbol": symbol,
                "bias": _bias(row),
                "score": _num(row.get("score") or row.get("risk_adjusted_score") or row.get("model_score"), 0.0),
                "expected_edge": _num(row.get("expected_edge") or row.get("expected_trade_return") or row.get("probability_edge"), 0.0),
                "sector": _text(row.get("sector")),
                "in_basket": _in_basket(row),
                "excluded_reason": _text(row.get("excluded_reason") or row.get("trade_quality_reason") or row.get("no_decision_reason")),
            }
        )
    return rows


def _connection(target: Engine | Connection | None = None) -> tuple[Connection | None, Any]:
    if target is None:
        engine = get_engine(required=False)
        if engine is None:
            return None, None
        context = engine.begin()
        return context.__enter__(), context
    if isinstance(target, Engine):
        context = target.begin()
        return context.__enter__(), context
    return target, None


def write_shortlist_snapshot(
    run_id: str,
    candidates: pd.DataFrame,
    *,
    target: Engine | Connection | None = None,
    ran_at: datetime | None = None,
    triggered_by: str = "candidate_generation",
) -> int:
    clean_run_id = _text(run_id)
    if not clean_run_id or candidates.empty:
        return 0
    rows = _normalize_rows(candidates, clean_run_id)
    if not rows:
        return 0
    conn, context = _connection(target)
    if conn is None:
        return 0
    exc_info = (None, None, None)
    try:
        run_exists = conn.execute(select(pipeline_runs.c.run_id).where(pipeline_runs.c.run_id == clean_run_id)).first()
        if run_exists:
            conn.execute(
                update(pipeline_runs)
                .where(pipeline_runs.c.run_id == clean_run_id)
                .values(current_stage="candidates", completed_at=ran_at or datetime.now(timezone.utc))
            )
        else:
            conn.execute(
                insert(pipeline_runs).values(
                    run_id=clean_run_id,
                    started_at=ran_at or datetime.now(timezone.utc),
                    completed_at=ran_at or datetime.now(timezone.utc),
                    status="success",
                    current_stage="candidates",
                    triggered_by=triggered_by,
                )
            )

        dialect = conn.dialect.name
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        elif dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        else:
            dialect_insert = insert

        statement = dialect_insert(shortlist_snapshots).values(rows)
        if hasattr(statement, "on_conflict_do_update"):
            update_columns = {
                column: getattr(statement.excluded, column)
                for column in ["rank", "bias", "score", "expected_edge", "sector", "in_basket", "excluded_reason"]
            }
            statement = statement.on_conflict_do_update(
                index_elements=[shortlist_snapshots.c.run_id, shortlist_snapshots.c.symbol],
                set_=update_columns,
            )
        conn.execute(statement)
        return len(rows)
    except Exception:
        exc_info = sys.exc_info()
        return 0
    finally:
        if context is not None:
            context.__exit__(*exc_info)


def write_shortlist_snapshot_from_csv(path: Path, *, target: Engine | Connection | None = None) -> int:
    if not path.exists():
        return 0
    frame = pd.read_csv(path, low_memory=False)
    ran_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return write_shortlist_snapshot(path.stem, frame, target=target, ran_at=ran_at, triggered_by="backfill")
