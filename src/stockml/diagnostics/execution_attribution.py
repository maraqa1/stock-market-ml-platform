from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.common.paths import TRADING_DIR
from stockml.diagnostics.common import latest_portal, missing_frame, numeric, safe_read_csv, write_report


def build_execution_attribution_report(stamp: str, *, result_file: Path | None = None, tracking_file: Path | None = None) -> object:
    result_path = result_file or latest_portal("08_alpaca_paper_order_results_*.csv")
    tracking_path = tracking_file or latest_portal("08_alpaca_paper_order_tracking_*.csv")
    results = safe_read_csv(result_path)
    tracking = safe_read_csv(tracking_path)
    missing = []
    if results.empty:
        missing.append("paper_order_results")
    if tracking.empty:
        missing.append("paper_order_tracking")
    output = TRADING_DIR / f"diagnostics_execution_attribution_{stamp}.csv"
    if missing:
        return write_report("execution_attribution", missing_frame("execution_attribution", missing), output, missing)
    frame = tracking.copy()
    frame["session"] = "regular"
    if "extended_hours" in frame.columns:
        frame.loc[frame["extended_hours"].astype(str).str.lower().isin({"true", "1", "yes"}), "session"] = "extended_hours"
    frame["filled_qty_num"] = numeric(frame, "filled_qty")
    frame["is_filled"] = frame["filled_qty_num"] > 0
    status = frame["alpaca_status"] if "alpaca_status" in frame.columns else pd.Series("", index=frame.index)
    frame["is_resting"] = status.astype(str).str.lower().isin({"new", "accepted", "pending_new"})
    rows = []
    for keys, group in frame.groupby(["session", "side"], dropna=False):
        session, side = keys
        rows.append(
            {
                "session": session,
                "side": side,
                "orders": len(group),
                "filled_orders": int(group["is_filled"].sum()),
                "resting_orders": int(group["is_resting"].sum()),
                "fill_rate": float(group["is_filled"].mean()) if len(group) else 0.0,
                "mean_limit_price": float(numeric(group, "limit_price", float("nan")).mean()),
                "mean_filled_avg_price": float(numeric(group, "filled_avg_price", float("nan")).mean()),
                "status_counts": group.get("alpaca_status", pd.Series("", index=group.index)).value_counts(dropna=False).to_dict(),
            }
        )
    return write_report("execution_attribution", pd.DataFrame(rows), output)
