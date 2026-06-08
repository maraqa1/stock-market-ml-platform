from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from stockml.reports.closed_trades_attribution import (
    ATTRIBUTION_COLUMNS,
    attribution_summary,
    csv_text,
    latest_attribution_file,
    load_attribution,
    section_rows,
)


def closed_trades_context(root: Path | None = None, *, days: int = 30, stream: str | None = None) -> dict[str, Any]:
    frame, source = _load_frame(root)
    if not frame.empty:
        frame = _filter(frame, days=days, stream=stream)
    summary = attribution_summary(frame)
    return {
        "source": source,
        "days": days,
        "stream": stream or "all",
        "summary": summary,
        "verdict": summary["verdict"],
        "rows": _records(frame),
        "by_reason": section_rows(frame, "close_reason"),
        "by_stream": section_rows(frame, "strategy_stream"),
        "by_direction": section_rows(frame, "direction"),
        "csv_url": f"/reports/closed_trades.csv?days={days}" + (f"&stream={stream}" if stream else ""),
        "missing": frame.empty,
        "missing_message": "No closed-trades attribution rows were found. Run the attribution builder after positions close." if frame.empty else "",
    }


def closed_trades_csv(root: Path | None = None, *, days: int = 30, stream: str | None = None) -> str:
    frame, _source = _load_frame(root)
    if not frame.empty:
        frame = _filter(frame, days=days, stream=stream)
    if frame.empty:
        frame = pd.DataFrame(columns=ATTRIBUTION_COLUMNS)
    return csv_text(frame)


def _load_frame(root: Path | None) -> tuple[pd.DataFrame, str]:
    path = latest_attribution_file(root)
    if path:
        try:
            return pd.read_csv(path, low_memory=False), str(path)
        except Exception:
            pass
    frame = load_attribution()
    return frame, "database:closed_trades_attribution" if not frame.empty else ""


def _filter(frame: pd.DataFrame, *, days: int, stream: str | None) -> pd.DataFrame:
    out = frame.copy()
    if "closed_at" in out.columns:
        closed = pd.to_datetime(out["closed_at"], utc=True, errors="coerce")
        if closed.notna().any():
            cutoff = closed.max() - pd.Timedelta(days=max(int(days or 30), 1))
            out = out[closed >= cutoff]
    if stream and stream != "all" and "strategy_stream" in out.columns:
        out = out[out["strategy_stream"].astype(str).eq(stream)]
    return out


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    display_cols = [
        "symbol",
        "strategy_stream",
        "direction",
        "closed_at",
        "close_reason",
        "realized_net_bps",
        "realized_pnl_usd",
        "max_favourable_bps",
        "max_adverse_bps",
        "signal_to_entry_bps",
        "exit_slippage_bps",
    ]
    cols = [col for col in display_cols if col in frame.columns]
    return frame[cols].sort_values("closed_at", ascending=False, kind="stable").head(200).fillna("").to_dict("records")
