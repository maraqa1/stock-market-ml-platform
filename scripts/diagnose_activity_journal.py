from __future__ import annotations

import argparse
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select

from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.db.connection import get_engine
from stockml.db.schema import position_events


def _details_value(details, key: str):
    return details.get(key, "") if isinstance(details, dict) else ""


def _load_from_db() -> pd.DataFrame:
    engine = get_engine(required=False)
    if engine is None:
        return pd.DataFrame()
    with engine.connect() as conn:
        rows = conn.execute(select(position_events)).mappings().all()
    records = []
    for row in rows:
        item = dict(row)
        details = item.get("details") or {}
        records.append(
            {
                "event_at": item.get("event_at"),
                "position_id": item.get("position_id"),
                "event_type": item.get("event_type"),
                "source": item.get("source"),
                "symbol": _details_value(details, "symbol") or str(item.get("position_id") or "").split(":")[-1],
                "event_key": _details_value(details, "event_key"),
                "broker_order_id": _details_value(details, "broker_order_id") or _details_value(details, "order_id"),
                "filled_qty": _details_value(details, "filled_qty"),
                "filled_avg_price": _details_value(details, "filled_avg_price"),
                "cycle_id": _details_value(details, "cycle_id"),
                "candidate_source": _details_value(details, "candidate_source"),
                "action": _details_value(details, "action") or _details_value(details, "decision"),
                "reason": _details_value(details, "reason") or _details_value(details, "block_reason"),
                "message": _details_value(details, "message") or _details_value(details, "final_outcome"),
                "details": details,
            }
        )
    return pd.DataFrame(records)


def _load(path: Path | None) -> pd.DataFrame:
    if path is not None:
        return pd.read_csv(path, low_memory=False)
    return _load_from_db()


def _col(frame: pd.DataFrame, name: str) -> pd.Series:
    return frame[name] if name in frame.columns else pd.Series([""] * len(frame), index=frame.index)


def diagnose(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        metrics = {"total_rows": 0, "warning": "activity_journal_empty"}
    else:
        event_type = _col(frame, "event_type").astype(str)
        source = _col(frame, "source").astype(str)
        symbol = _col(frame, "symbol").astype(str).str.upper()
        event_key = _col(frame, "event_key").astype(str)
        filled = frame[event_type.eq("filled")].copy()
        if "broker_order_id" not in filled.columns:
            filled["broker_order_id"] = _col(filled, "order_id")
        selected = frame[event_type.eq("selected")].copy()
        selected_key_cols = [c for c in ["cycle_id", "symbol", "candidate_source", "action"] if c in selected.columns]
        monitor = frame[event_type.eq("monitor_rotate")].copy()
        manual = frame[source.str.contains("manual", case=False, na=False) | event_type.isin(["operator_keep", "operator_close"])]
        manual_messages = _col(manual, "message").astype(str)
        contradictory = 0
        if not manual.empty:
            grouped = manual.assign(__symbol=symbol.reindex(manual.index).fillna(""), __message=manual_messages).groupby("__symbol")
            contradictory = int(sum(group["__message"].nunique() > 1 for _, group in grouped))
        metrics = {
            "total_rows": int(len(frame)),
            "duplicate_filled_events": int(filled.duplicated("event_key").sum()) if "event_key" in filled.columns and filled["event_key"].astype(str).ne("").any() else int(filled.duplicated([c for c in ["broker_order_id", "event_type", "filled_qty", "filled_avg_price"] if c in filled.columns]).sum()) if not filled.empty else 0,
            "unique_broker_fills": int(filled.get("broker_order_id", pd.Series(dtype=str)).dropna().astype(str).replace("", pd.NA).dropna().nunique()) if not filled.empty else 0,
            "duplicate_selected_events": int(selected.duplicated(selected_key_cols).sum()) if selected_key_cols else 0,
            "repeated_monitor_actions": int(monitor.duplicated([c for c in ["symbol", "event_type", "reason"] if c in monitor.columns]).sum()) if not monitor.empty else 0,
            "contradictory_manual_actions": contradictory,
            "anti_churn_blocked_count": int(event_type.eq("anti_churn_blocked").sum()),
            "24x5_submitted_count": int(event_type.eq("candidate_submitted").sum()),
            "24x5_blocked_count": int(event_type.isin(["candidate_blocked", "candidate_skipped_anti_churn", "candidate_skipped_meta_label", "candidate_skipped_not_overnight_tradable"]).sum()),
            "symbols_with_repeated_replace_logs": int(monitor.get("symbol", pd.Series(dtype=str)).dropna().astype(str).str.upper().value_counts().gt(1).sum()) if not monitor.empty and "symbol" in monitor.columns else 0,
        }
    return pd.DataFrame([{"metric": key, "value": value} for key, value in metrics.items()])


def write_reports(metrics: pd.DataFrame, *, root: Path = PROJECT_ROOT, stamp: str | None = None) -> tuple[Path, Path]:
    out_dir = root / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    csv_path = out_dir / f"activity_journal_quality_{run_stamp}.csv"
    md_path = out_dir / f"activity_journal_quality_{run_stamp}.md"
    metrics.to_csv(csv_path, index=False)
    lines = ["# Activity Journal Quality", "", f"Generated: {datetime.now(timezone.utc).isoformat()}", "", "| metric | value |", "| --- | --- |"]
    for row in metrics.to_dict("records"):
        lines.append(f"| {row['metric']} | {row['value']} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--activity-file", type=Path)
    args = parser.parse_args()
    metrics = diagnose(_load(args.activity_file))
    csv_path, md_path = write_reports(metrics)
    print(f"activity_journal_quality_csv: {csv_path}")
    print(f"activity_journal_quality_md: {md_path}")
    for row in metrics.to_dict("records"):
        print(f"{row['metric']}: {row['value']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
