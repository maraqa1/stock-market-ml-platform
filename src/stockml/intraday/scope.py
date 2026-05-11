from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine

from portal.services.latest_file_reader import latest_file, safe_read_csv
from stockml.common.paths import PROJECT_ROOT
from stockml.db.connection import get_engine
from stockml.db.schema import pipeline_runs, shortlist_snapshots


PositionLoader = Callable[[], list[dict[str, Any]]]


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _symbols_from_positions(rows: list[dict[str, Any]] | None) -> set[str]:
    return {_symbol(row.get("symbol") or row.get("ticker")) for row in rows or [] if _symbol(row.get("symbol") or row.get("ticker"))}


def _latest_shortlist_symbols_from_db(engine: Engine, selected: date) -> set[str]:
    start = datetime.combine(selected, time.min, tzinfo=timezone.utc)
    end = datetime.combine(selected, time.max, tzinfo=timezone.utc)
    with engine.connect() as conn:
        run = conn.execute(
            select(pipeline_runs.c.run_id)
            .where(pipeline_runs.c.started_at >= start)
            .where(pipeline_runs.c.started_at <= end)
            .order_by(pipeline_runs.c.started_at.desc(), pipeline_runs.c.run_id.desc())
            .limit(1)
        ).scalar()
        if not run:
            return set()
        rows = conn.execute(select(shortlist_snapshots.c.symbol).where(shortlist_snapshots.c.run_id == run)).all()
    return {_symbol(row[0]) for row in rows if _symbol(row[0])}


def _latest_shortlist_symbols_from_artifacts(root: Path) -> set[str]:
    path = latest_file(root, "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
    frame = safe_read_csv(path, nrows=1000)
    if frame.empty:
        return set()
    column = "symbol" if "symbol" in frame.columns else "ticker" if "ticker" in frame.columns else ""
    if not column:
        return set()
    return {_symbol(value) for value in frame[column].dropna() if _symbol(value)}


def scope_for_today(
    selected: date | None = None,
    *,
    root: Path | None = None,
    positions_loader: PositionLoader | None = None,
    engine: Engine | None = None,
) -> list[str]:
    day = selected or date.today()
    base = Path(root) if root is not None else PROJECT_ROOT
    symbols: set[str] = set()
    db = engine or (get_engine(required=False) if base.resolve() == PROJECT_ROOT.resolve() else None)
    if db is not None:
        try:
            symbols.update(_latest_shortlist_symbols_from_db(db, day))
        except Exception:
            pass
    if not symbols:
        symbols.update(_latest_shortlist_symbols_from_artifacts(base))
    if positions_loader is not None:
        symbols.update(_symbols_from_positions(positions_loader()))
    return sorted(symbols)
