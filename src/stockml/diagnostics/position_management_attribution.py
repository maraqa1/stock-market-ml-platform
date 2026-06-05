from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import select

from stockml.common.paths import TRADING_DIR
from stockml.db.connection import get_engine
from stockml.db.schema import position_events
from stockml.diagnostics.common import latest_trading, missing_frame, safe_read_csv, write_report


EXIT_REASON_MAP = {
    "stop_loss": "stop_loss",
    "hard_stop_loss": "stop_loss",
    "take_profit": "take_profit",
    "trailing_profit_giveback": "trailing_giveback",
    "monitor_close": "defensive_close",
    "operator_close": "manual_close",
    "stale_signal": "stale_signal",
    "unknown_signal": "unknown_signal",
    "defensive_close": "defensive_close",
}


def _events_from_db() -> pd.DataFrame:
    try:
        engine = get_engine(required=False)
        if engine is None:
            return pd.DataFrame()
        with engine.connect() as conn:
            rows = conn.execute(select(position_events)).mappings().all()
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def _reason(row: pd.Series) -> str:
    text = "|".join(str(row.get(column) or "") for column in ["event_type", "source", "details"]).lower()
    for needle, reason in EXIT_REASON_MAP.items():
        if needle in text:
            return reason
    return "other"


def build_position_management_report(stamp: str, *, event_file: Path | None = None) -> object:
    path = event_file or latest_trading("*position*event*.csv")
    events = safe_read_csv(path)
    if events.empty:
        events = _events_from_db()
    output = TRADING_DIR / f"diagnostics_position_management_{stamp}.csv"
    if events.empty:
        return write_report("position_management", missing_frame("position_management", ["position_events"]), output, ["position_events"])
    events["exit_reason"] = events.apply(_reason, axis=1)
    rows = []
    for reason, group in events.groupby("exit_reason", dropna=False):
        rows.append(
            {
                "exit_reason": reason,
                "event_count": len(group),
                "position_count": group.get("position_id", pd.Series("", index=group.index)).nunique(),
                "sources": group.get("source", pd.Series("", index=group.index)).value_counts(dropna=False).to_dict(),
                "note": "P&L impact requires linked fill/close records with realized P&L." if "realized_pnl" not in group.columns else "",
            }
        )
    return write_report("position_management", pd.DataFrame(rows), output)

