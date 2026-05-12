from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine

from stockml.common.paths import PROJECT_ROOT
from stockml.db.connection import get_engine
from stockml.db.schema import pipeline_runs, shortlist_snapshots


PositionLoader = Callable[[], list[dict[str, Any]]]


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _symbols_from_positions(rows: list[dict[str, Any]] | None) -> set[str]:
    return {_symbol(row.get("symbol") or row.get("ticker")) for row in rows or [] if _symbol(row.get("symbol") or row.get("ticker"))}


def _latest_file(root: Path, area: str, pattern: str) -> Path | None:
    base = root / "data" / area
    if not base.exists():
        return None
    matches = sorted(base.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _safe_read_csv(path: Path | None, *, nrows: int | None = None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, nrows=nrows)
    except Exception:
        return pd.DataFrame()


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


def _latest_shortlist_rows_from_db(engine: Engine, selected: date) -> list[dict[str, Any]]:
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
            return []
        rows = conn.execute(
            select(
                shortlist_snapshots.c.symbol,
                shortlist_snapshots.c.bias,
                shortlist_snapshots.c.score,
                shortlist_snapshots.c.rank,
                shortlist_snapshots.c.sector,
            )
            .where(shortlist_snapshots.c.run_id == run)
            .order_by(shortlist_snapshots.c.rank.asc())
        ).mappings()
    return [
        {
            "symbol": _symbol(row["symbol"]),
            "bias": str(row["bias"] or "").strip().lower() or "neutral",
            "score": row["score"],
            "rank": row["rank"],
            "sector": row["sector"],
            "source": "shortlist_snapshots",
        }
        for row in rows
        if _symbol(row["symbol"])
    ]


def _latest_shortlist_symbols_from_artifacts(root: Path) -> set[str]:
    path = _latest_file(root, "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
    frame = _safe_read_csv(path, nrows=1000)
    if frame.empty:
        return set()
    column = "symbol" if "symbol" in frame.columns else "ticker" if "ticker" in frame.columns else ""
    if not column:
        return set()
    return {_symbol(value) for value in frame[column].dropna() if _symbol(value)}


def _latest_shortlist_rows_from_artifacts(root: Path) -> list[dict[str, Any]]:
    path = _latest_file(root, "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
    frame = _safe_read_csv(path, nrows=1000)
    if frame.empty:
        return []
    symbol_col = "symbol" if "symbol" in frame.columns else "ticker" if "ticker" in frame.columns else ""
    if not symbol_col:
        return []
    rows: list[dict[str, Any]] = []
    for idx, row in frame.iterrows():
        symbol = _symbol(row.get(symbol_col))
        if not symbol:
            continue
        bias = str(row.get("bias") or row.get("side") or row.get("trade_action") or "neutral").strip().lower()
        if bias in {"buy", "long"}:
            bias = "long"
        elif bias in {"sell", "short"}:
            bias = "short"
        elif bias not in {"long", "short", "neutral"}:
            bias = "neutral"
        rows.append(
            {
                "symbol": symbol,
                "bias": bias,
                "score": row.get("score") if "score" in frame.columns else row.get("model_score") if "model_score" in frame.columns else None,
                "rank": row.get("rank") if "rank" in frame.columns else idx + 1,
                "sector": row.get("sector") if "sector" in frame.columns else None,
                "source": "candidate_artifact",
            }
        )
    return rows


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


def scope_rows_for_today(
    selected: date | None = None,
    *,
    root: Path | None = None,
    positions_loader: PositionLoader | None = None,
    engine: Engine | None = None,
) -> list[dict[str, Any]]:
    day = selected or date.today()
    base = Path(root) if root is not None else PROJECT_ROOT
    db = engine or (get_engine(required=False) if base.resolve() == PROJECT_ROOT.resolve() else None)
    rows: list[dict[str, Any]] = []
    if db is not None:
        try:
            rows = _latest_shortlist_rows_from_db(db, day)
        except Exception:
            rows = []
    if not rows:
        rows = _latest_shortlist_rows_from_artifacts(base)

    by_symbol = {row["symbol"]: dict(row) for row in rows if _symbol(row.get("symbol"))}
    held_symbols = _symbols_from_positions(positions_loader()) if positions_loader is not None else set()
    for symbol in held_symbols:
        by_symbol.setdefault(
            symbol,
            {
                "symbol": symbol,
                "bias": "neutral",
                "score": None,
                "rank": None,
                "sector": None,
                "source": "open_position",
            },
        )
    for symbol, row in by_symbol.items():
        row["is_held"] = symbol in held_symbols
    return [by_symbol[symbol] for symbol in sorted(by_symbol)]
