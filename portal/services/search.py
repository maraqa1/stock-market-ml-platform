from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from portal.services.latest_file_reader import latest_file, safe_read_csv
from stockml.db.connection import get_engine


RUN_ID_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-[A-Z]$")
_SYMBOL_CACHE: set[str] | None = None


def _engine():
    return get_engine(required=False)


def _clean_query(query: str) -> str:
    return str(query or "").strip()


def _limit(limit: int) -> int:
    try:
        return max(1, min(int(limit), 20))
    except Exception:
        return 5


def _db_rows(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    engine = _engine()
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(text(sql), params).mappings().all()]
    except Exception:
        return []


def _db_positions(prefix: str, limit: int) -> list[dict[str, Any]]:
    rows = _db_rows(
        """
        select p.symbol, coalesce(s.name, p.symbol) as name, p.side,
               p.unrealized_plpc as pnl_pct, p.opened_at
        from positions p
        left join dim_security s on upper(s.symbol) = upper(p.symbol)
        where p.symbol ilike :prefix and lower(coalesce(p.status, '')) = 'open'
        order by p.opened_at desc nulls last, p.symbol
        limit :limit
        """,
        {"prefix": f"{prefix}%", "limit": limit},
    )
    return [
        {
            "symbol": str(row["symbol"]).upper(),
            "name": row.get("name") or str(row["symbol"]).upper(),
            "side": row.get("side") or "",
            "pnl_pct": row.get("pnl_pct"),
            "age_days": "",
            "url": f"/symbols/{str(row['symbol']).upper()}",
        }
        for row in rows
        if row.get("symbol")
    ]


def _db_signals(prefix: str, limit: int) -> list[dict[str, Any]]:
    rows = _db_rows(
        """
        select ss.symbol, coalesce(ds.name, ss.symbol) as name, ss.bias, ss.score
        from shortlist_snapshots ss
        left join dim_security ds on upper(ds.symbol) = upper(ss.symbol)
        where ss.symbol ilike :prefix
          and ss.run_id = (select max(run_id) from pipeline_runs)
        order by ss.rank asc
        limit :limit
        """,
        {"prefix": f"{prefix}%", "limit": limit},
    )
    return [
        {
            "symbol": str(row["symbol"]).upper(),
            "name": row.get("name") or str(row["symbol"]).upper(),
            "side": row.get("bias") or "",
            "score": row.get("score"),
            "url": f"/symbols/{str(row['symbol']).upper()}",
        }
        for row in rows
        if row.get("symbol")
    ]


def _db_reference(query: str, used: set[str], limit: int) -> list[dict[str, Any]]:
    rows = _db_rows(
        """
        select symbol, name
        from dim_security
        where symbol ilike :prefix or name ilike :name_query
        order by symbol
        limit :limit
        """,
        {"prefix": f"{query}%", "name_query": f"%{query}%", "limit": limit + len(used)},
    )
    items = []
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol or symbol in used:
            continue
        items.append({"symbol": symbol, "name": row.get("name") or symbol, "url": f"/symbols/{symbol}"})
        if len(items) >= limit:
            break
    return items


def _symbol_cache(root: Path | None = None) -> set[str]:
    global _SYMBOL_CACHE
    if _SYMBOL_CACHE is not None:
        return _SYMBOL_CACHE
    rows = _db_rows("select symbol from dim_security where is_active is distinct from false", {})
    symbols = {str(row.get("symbol") or "").upper() for row in rows if row.get("symbol")}
    if not symbols and root is not None:
        for key, pattern, column in [
            ("portal_outputs", "08_alpaca_paper_candidate_pool_*.csv", "symbol"),
            ("model_outputs", "advanced_model_signal_table_*.csv", "ticker"),
            ("raw", "01_us_equity_universe_*.csv", "symbol"),
        ]:
            path = latest_file(root, key, pattern)
            frame = safe_read_csv(path, nrows=5000)
            if column in frame.columns:
                symbols.update(str(value).upper() for value in frame[column].dropna())
    symbols.update({"AAPL", "AMZN", "GOOG", "GOOGL", "MSFT", "NVDA", "TSLA"})
    _SYMBOL_CACHE = {symbol for symbol in symbols if symbol}
    return _SYMBOL_CACHE


def _fallback_positions(root: Path | None, prefix: str, limit: int) -> list[dict[str, Any]]:
    if root is None:
        return []
    path = latest_file(root, "portal_outputs", "08_alpaca_paper_positions_*.csv")
    frame = safe_read_csv(path, nrows=1000)
    if frame.empty or "symbol" not in frame.columns:
        return []
    frame = frame[frame["symbol"].fillna("").astype(str).str.upper().str.startswith(prefix)].head(limit)
    rows = []
    for row in frame.fillna("").to_dict("records"):
        symbol = str(row.get("symbol") or "").upper()
        rows.append(
            {
                "symbol": symbol,
                "name": row.get("name") or symbol,
                "side": row.get("side") or "long",
                "pnl_pct": row.get("unrealized_plpc", ""),
                "age_days": row.get("age_days", ""),
                "url": f"/symbols/{symbol}",
            }
        )
    return rows


def _fallback_signals(root: Path | None, prefix: str, limit: int) -> list[dict[str, Any]]:
    if root is None:
        return []
    path = latest_file(root, "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
    frame = safe_read_csv(path, nrows=1000)
    if frame.empty or "symbol" not in frame.columns:
        return []
    frame = frame[frame["symbol"].fillna("").astype(str).str.upper().str.startswith(prefix)].head(limit)
    rows = []
    for row in frame.fillna("").to_dict("records"):
        symbol = str(row.get("symbol") or "").upper()
        rows.append(
            {
                "symbol": symbol,
                "name": row.get("company") or symbol,
                "side": str(row.get("trade_action") or row.get("side") or "").lower(),
                "score": row.get("risk_adjusted_score", ""),
                "url": f"/symbols/{symbol}",
            }
        )
    return rows


def _fallback_reference(root: Path | None, query: str, used: set[str], limit: int) -> list[dict[str, Any]]:
    prefix = query.upper()
    matches = [symbol for symbol in sorted(_symbol_cache(root)) if symbol.startswith(prefix) and symbol not in used][:limit]
    return [{"symbol": symbol, "name": symbol, "url": f"/symbols/{symbol}"} for symbol in matches]


def _run_items(query: str, limit: int) -> list[dict[str, Any]]:
    rows = _db_rows(
        """
        select run_id
        from pipeline_runs
        where run_id ilike :prefix
        order by started_at desc nulls last, run_id desc
        limit :limit
        """,
        {"prefix": f"{query}%", "limit": limit},
    )
    if RUN_ID_PATTERN.match(query) and not rows:
        rows = [{"run_id": query}]
    return [{"run_id": str(row["run_id"]), "url": f"/diagnostics#run-{row['run_id']}"} for row in rows if row.get("run_id")]


def search(query: str, limit: int = 5, root: Path | None = None, scope: str = "all") -> dict[str, list[dict[str, Any]]]:
    clean = _clean_query(query)
    cap = _limit(limit)
    if not clean:
        return {"groups": []}
    if RUN_ID_PATTERN.match(clean):
        runs = _run_items(clean, cap)
        return {"groups": [{"key": "runs", "label": "Pipeline runs", "items": runs}] if runs else []}

    prefix = clean.upper()
    groups: list[dict[str, Any]] = []
    used: set[str] = set()

    if scope != "symbol":
        positions = _db_positions(prefix, cap) or _fallback_positions(root, prefix, cap)
        used.update(item["symbol"] for item in positions)
        if positions:
            groups.append({"key": "positions", "label": "Open positions", "items": positions[:cap]})

        signals = _db_signals(prefix, cap) or _fallback_signals(root, prefix, cap)
        used.update(item["symbol"] for item in signals)
        if signals:
            groups.append({"key": "signals", "label": "Today's signals", "items": signals[:cap]})

    reference = _db_reference(clean, used, cap) or _fallback_reference(root, clean, used, cap)
    if reference:
        groups.append({"key": "reference", "label": "Reference", "items": reference[:cap]})

    if scope != "symbol":
        runs = _run_items(clean, cap)
        if runs:
            groups.append({"key": "runs", "label": "Pipeline runs", "items": runs[:cap]})

    return {"groups": groups}
