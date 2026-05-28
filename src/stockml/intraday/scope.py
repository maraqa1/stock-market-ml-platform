from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from stockml.common.paths import PROJECT_ROOT
from stockml.db.connection import get_engine
from stockml.db.schema import pipeline_runs, shortlist_snapshots


PositionLoader = Callable[[], list[dict[str, Any]]]
MIN_SCOPE_SHORTLIST_ROWS = 25
MANUAL_MOVERS_PATTERN = "intraday_movers_*.csv"


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


def _latest_broad_file(root: Path, area: str, pattern: str, *, min_rows: int = MIN_SCOPE_SHORTLIST_ROWS) -> Path | None:
    base = root / "data" / area
    if not base.exists():
        return None
    matches = sorted(base.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    fallback = matches[0] if matches else None
    for path in matches:
        frame = _safe_read_csv(path, nrows=min_rows)
        if len(frame) >= min_rows:
            return path
    return fallback


def _safe_read_csv(path: Path | None, *, nrows: int | None = None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, nrows=nrows)
    except Exception:
        return pd.DataFrame()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace("%", "").replace(",", ""))
    except Exception:
        return default


def _manual_mover_score(row: pd.Series) -> float:
    raw = row.get("score")
    if raw not in {None, ""} and pd.notna(raw):
        return max(0.0, min(1.0, _safe_float(raw)))
    move_pct = abs(_safe_float(row.get("move_pct", row.get("% chg", row.get("pct_chg", row.get("change_pct", 0))))))
    dollar_traded = _safe_float(row.get("dollar_traded", row.get("$ traded", row.get("notional", 0))))
    score = min(1.0, move_pct / 40.0)
    if dollar_traded >= 100_000_000:
        score += 0.10
    elif dollar_traded >= 10_000_000:
        score += 0.05
    return max(0.35, min(1.0, score))


def _latest_manual_mover_rows_from_artifacts(root: Path) -> list[dict[str, Any]]:
    path = _latest_file(root, "trading/manual_movers", MANUAL_MOVERS_PATTERN)
    frame = _safe_read_csv(path, nrows=500)
    if frame.empty:
        return []
    symbol_col = next((column for column in ["symbol", "ticker", "SYMBOL"] if column in frame.columns), "")
    if not symbol_col:
        return []
    rows: list[dict[str, Any]] = []
    for idx, row in frame.iterrows():
        symbol = _symbol(row.get(symbol_col))
        if not symbol:
            continue
        side = str(row.get("side") or "").strip().lower()
        move_pct = _safe_float(row.get("move_pct", row.get("% chg", row.get("pct_chg", row.get("change_pct", 0)))))
        if side in {"buy", "long"}:
            bias = "long"
        elif side in {"sell", "short"}:
            bias = "short"
        else:
            bias = "long" if move_pct >= 0 else "short"
        rows.append(
            {
                "symbol": symbol,
                "bias": bias,
                "score": _manual_mover_score(row),
                "rank": idx + 1,
                "sector": row.get("sector") if "sector" in frame.columns else None,
                "source": "manual_intraday_movers",
                "manual_move_pct": move_pct,
                "manual_last_price": row.get("last", row.get("current_price", "")),
                "manual_dollar_traded": row.get("dollar_traded", row.get("$ traded", "")),
            }
        )
    return rows


def _latest_broad_shortlist_run_from_db(engine: Engine, selected: date, *, min_rows: int = MIN_SCOPE_SHORTLIST_ROWS) -> str | None:
    start = datetime.combine(selected, time.min, tzinfo=timezone.utc)
    end = datetime.combine(selected, time.max, tzinfo=timezone.utc)
    with engine.connect() as conn:
        run_ids = conn.execute(
            select(pipeline_runs.c.run_id)
            .where(pipeline_runs.c.started_at >= start)
            .where(pipeline_runs.c.started_at <= end)
            .order_by(pipeline_runs.c.started_at.desc(), pipeline_runs.c.run_id.desc())
        ).scalars().all()
        fallback = run_ids[0] if run_ids else None
        for run_id in run_ids:
            row_count = conn.execute(
                select(func.count()).select_from(shortlist_snapshots).where(shortlist_snapshots.c.run_id == run_id)
            ).scalar()
            if int(row_count or 0) >= min_rows:
                return str(run_id)
    return str(fallback) if fallback else None


def _latest_shortlist_symbols_from_db(engine: Engine, selected: date) -> set[str]:
    # Intraday evaluation needs the latest broad nightly universe, not a later
    # one-row operational artifact produced after the basket has been acted on.
    run = _latest_broad_shortlist_run_from_db(engine, selected)
    if not run:
        return set()
    with engine.connect() as conn:
        rows = conn.execute(select(shortlist_snapshots.c.symbol).where(shortlist_snapshots.c.run_id == run)).all()
    return {_symbol(row[0]) for row in rows if _symbol(row[0])}


def _latest_shortlist_rows_from_db(engine: Engine, selected: date) -> list[dict[str, Any]]:
    # Intraday evaluation needs the latest broad nightly universe, not a later
    # one-row operational artifact produced after the basket has been acted on.
    run = _latest_broad_shortlist_run_from_db(engine, selected)
    if not run:
        return []
    with engine.connect() as conn:
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
    path = _latest_broad_file(root, "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
    frame = _safe_read_csv(path, nrows=1000)
    if frame.empty:
        return set()
    column = "symbol" if "symbol" in frame.columns else "ticker" if "ticker" in frame.columns else ""
    if not column:
        return set()
    return {_symbol(value) for value in frame[column].dropna() if _symbol(value)}


def _latest_shortlist_rows_from_artifacts(root: Path) -> list[dict[str, Any]]:
    path = _latest_broad_file(root, "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
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
    for row in _latest_manual_mover_rows_from_artifacts(base):
        symbol = _symbol(row.get("symbol"))
        if not symbol:
            continue
        existing = by_symbol.get(symbol, {})
        by_symbol[symbol] = {**existing, **row}
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
