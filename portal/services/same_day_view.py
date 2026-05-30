from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import insert, select

from stockml.db.connection import get_engine
from stockml.db.schema import same_day_candidates, same_day_missed_opportunities


def _record(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _rows(statement) -> list[dict[str, Any]]:
    engine = get_engine(required=False)
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            return [_record(dict(row)) for row in conn.execute(statement).mappings().all()]
    except Exception:
        return []


def same_day_panel_context(root: Path | None = None, *, limit: int = 20) -> dict[str, Any]:
    rows = _rows(
        same_day_candidates.select()
        .where(same_day_candidates.c.arbitration_outcome == "emit")
        .order_by(same_day_candidates.c.continuation_probability.desc(), same_day_candidates.c.generated_at.desc())
        .limit(limit)
    )
    return {
        "source": "database" if rows else "empty",
        "rows": rows,
        "counts": {"emit": len(rows)},
    }


def record_same_day_operator_decision(candidate_id: int, decision: str, *, operator_id: str = "operator@stockml") -> dict[str, Any]:
    engine = get_engine(required=False)
    if engine is None:
        return {"status": "rejected", "message": "database_unavailable"}
    with engine.begin() as conn:
        row = conn.execute(select(same_day_candidates).where(same_day_candidates.c.id == int(candidate_id))).mappings().first()
        if row is None:
            return {"status": "rejected", "message": "candidate_not_found"}
        outcome = "confirmed" if decision == "confirm" else "operator_skipped"
        conn.execute(
            same_day_candidates.update()
            .where(same_day_candidates.c.id == int(candidate_id))
            .values(arbitration_outcome=outcome)
        )
    return {
        "status": "recorded",
        "message": outcome,
        "candidate_id": int(candidate_id),
        "symbol": str(row.get("symbol") or "").upper(),
        "strategy_stream": "same_day_momentum",
        "must_flatten_at_eod": True,
        "operator_id": operator_id,
    }


def missed_opportunities_context(report_date: str | date) -> dict[str, Any]:
    session_date = date.fromisoformat(str(report_date)) if not isinstance(report_date, date) else report_date
    rows = _rows(
        same_day_missed_opportunities.select()
        .where(same_day_missed_opportunities.c.session_date == session_date)
        .order_by(same_day_missed_opportunities.c.intraday_move_pct.desc())
    )
    return {
        "session_date": session_date.isoformat(),
        "rows": rows,
        "counts": {"total": len(rows)},
    }


def write_missed_opportunity_rows(rows: list[dict[str, Any]]) -> int:
    engine = get_engine(required=False)
    if engine is None or not rows:
        return 0
    with engine.begin() as conn:
        conn.execute(insert(same_day_missed_opportunities), rows)
    return len(rows)
