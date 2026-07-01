from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


DIAGNOSTIC_FILES = {
    "ledger": ("trade_ledger_*.csv", "Trade ledger"),
    "unmatched": ("unmatched_lifecycle_events_*.csv", "Unmatched lifecycle events"),
    "attribution": ("profitability_attribution_*.csv", "Profitability attribution"),
}


def trade_ledger_context(root: Path, *, preview_rows: int = 100) -> dict[str, Any]:
    paths = {key: _latest(root, pattern) for key, (pattern, _label) in DIAGNOSTIC_FILES.items()}
    frames = {key: _read_csv(path) for key, path in paths.items()}
    ledger = frames["ledger"]
    unmatched = frames["unmatched"]
    attribution = frames["attribution"]

    return {
        "files": [
            {
                "kind": key,
                "label": label,
                "path": str(paths[key]) if paths[key] else "",
                "filename": paths[key].name if paths[key] else "",
                "exists": bool(paths[key]),
                "rows": int(len(frames[key])),
                "csv_url": f"/reports/trade_ledger/{key}.csv" if paths[key] else "",
            }
            for key, (_pattern, label) in DIAGNOSTIC_FILES.items()
        ],
        "summary": _summary(ledger, unmatched, attribution),
        "ledger_rows": _records(ledger, _ledger_columns(), preview_rows),
        "attribution_rows": _records(attribution, _attribution_columns(), preview_rows),
        "unmatched_rows": _records(unmatched, _unmatched_columns(), preview_rows),
        "missing": all(path is None for path in paths.values()),
        "missing_message": "No trade-ledger diagnostics were found. Run scripts/build_trade_ledger.py and scripts/build_profitability_attribution.py.",
    }


def trade_ledger_csv(root: Path, kind: str) -> str | None:
    path = latest_trade_ledger_file(root, kind)
    if not path:
        return None
    return path.read_text(encoding="utf-8")


def latest_trade_ledger_file(root: Path, kind: str) -> Path | None:
    spec = DIAGNOSTIC_FILES.get(kind)
    if not spec:
        return None
    return _latest(root, spec[0])


def _diagnostics_dir(root: Path) -> Path:
    return root / "data" / "trading" / "diagnostics"


def _latest(root: Path, pattern: str) -> Path | None:
    directory = _diagnostics_dir(root)
    if not directory.exists():
        return None
    matches = [path for path in directory.glob(pattern) if path.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda path: (path.stat().st_mtime, path.name))


def _read_csv(path: Path | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _summary(ledger: pd.DataFrame, unmatched: pd.DataFrame, attribution: pd.DataFrame) -> dict[str, Any]:
    status_col = _first_existing(ledger, ["trade_status", "position_status"])
    status_counts = _counts(ledger, status_col) if status_col else {}
    pnl_col = _first_existing(attribution, ["total_pnl_usd", "total_pnl", "realized_pnl_usd", "realised_pnl"])
    pnl_source = attribution
    if pnl_col is None:
        pnl_col = _first_existing(ledger, ["realized_pnl_usd", "realised_pnl", "realized_pnl"])
        pnl_source = ledger
    return {
        "trade_count": int(len(ledger)),
        "open_trades": int(status_counts.get("open", 0)),
        "closed_trades": int(status_counts.get("closed", 0)),
        "unmatched_events": int(len(unmatched)),
        "attribution_rows": int(len(attribution)),
        "total_pnl_usd": _sum(pnl_source, pnl_col) if pnl_col else 0.0,
        "winner_count": _positive_count(ledger, _first_existing(ledger, ["realized_pnl_usd", "realised_pnl", "realized_pnl"]) or ""),
        "loser_count": _negative_count(ledger, _first_existing(ledger, ["realized_pnl_usd", "realised_pnl", "realized_pnl"]) or ""),
    }


def _first_existing(frame: pd.DataFrame, columns: list[str]) -> str | None:
    for column in columns:
        if column in frame.columns:
            return column
    return None


def _counts(frame: pd.DataFrame, column: str | None) -> dict[str, int]:
    if frame.empty or not column or column not in frame.columns:
        return {}
    counts = frame[column].fillna("").astype(str).str.lower().value_counts()
    return {str(key): int(value) for key, value in counts.items()}


def _sum(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())


def _positive_count(frame: pd.DataFrame, column: str) -> int:
    if frame.empty or column not in frame.columns:
        return 0
    return int((pd.to_numeric(frame[column], errors="coerce").fillna(0) > 0).sum())


def _negative_count(frame: pd.DataFrame, column: str) -> int:
    if frame.empty or column not in frame.columns:
        return 0
    return int((pd.to_numeric(frame[column], errors="coerce").fillna(0) < 0).sum())


def _records(frame: pd.DataFrame, preferred: list[str], limit: int) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    cols = [col for col in preferred if col in frame.columns]
    if not cols:
        cols = list(frame.columns[:12])
    sort_cols = [col for col in ["exit_time", "closed_at", "entry_time", "opened_at", "event_at", "symbol"] if col in frame.columns]
    source = frame.sort_values(sort_cols, ascending=False, kind="stable") if sort_cols else frame
    return source[cols].head(limit).fillna("").to_dict("records")


def _ledger_columns() -> list[str]:
    return [
        "trade_id",
        "symbol",
        "side",
        "position_status",
        "trade_status",
        "entry_time",
        "exit_time",
        "opened_at",
        "closed_at",
        "entry_price",
        "exit_price",
        "entry_quantity",
        "exit_quantity",
        "quantity",
        "realised_pnl",
        "realized_pnl_usd",
        "client_order_id",
        "entry_broker_order_id",
        "exit_broker_order_id",
        "broker_order_id",
    ]


def _attribution_columns() -> list[str]:
    return [
        "trade_id",
        "symbol",
        "side",
        "opened_at",
        "closed_at",
        "realized_pnl_usd",
        "realised_pnl",
        "total_pnl_usd",
        "total_pnl",
        "entry_slippage_bps",
        "exit_slippage_bps",
        "candidate_source",
        "session_mode",
    ]


def _unmatched_columns() -> list[str]:
    return [
        "event_at",
        "event_id",
        "symbol",
        "event_type",
        "client_order_id",
        "entry_broker_order_id",
        "exit_broker_order_id",
        "broker_order_id",
        "trade_id",
        "lineage_warning",
    ]
