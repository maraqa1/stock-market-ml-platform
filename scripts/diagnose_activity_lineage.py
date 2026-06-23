from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.engine import Engine

from stockml.common.paths import timestamp
from stockml.trading.activity_journal_export import iter_activity_journal_rows, request_for_date, request_for_range, parse_utc_datetime

COVERAGE_FIELDS = [
    "cycle_id",
    "candidate_id",
    "event_key",
    "client_order_id",
    "broker_order_id",
    "position_id",
    "trade_id",
    "exit_decision_id",
    "order_intent",
    "session_mode",
    "lineage_warning",
]

SUBMIT_EVENTS = {"submitted", "order_submitted", "candidate_submitted"}
FILL_EVENTS = {"filled", "close_filled"}
MONITOR_EVENTS = {"monitor_safe", "monitor_watch", "monitor_rotate", "monitor_close"}
EXIT_EVENTS = {"monitor_close", "operator_close", "close_submitted", "close_filled"}
PNL_EVENTS = {"pnl_recorded", "monitor_safe"}


def _present(value: Any) -> bool:
    return value not in (None, "") and str(value).strip().lower() not in {"nan", "none", "null", "<na>"}


def _has_link(value: Any, linked: set[str]) -> bool:
    return _present(value) and str(value) in linked


def _broken_chain_counts(frame: pd.DataFrame, group: pd.DataFrame) -> dict[str, int]:
    submitted_candidates = set(frame.loc[frame["event_type"].isin(SUBMIT_EVENTS), "candidate_id"].dropna().astype(str)) if "candidate_id" in frame else set()
    filled_clients = set(frame.loc[frame["event_type"].isin(FILL_EVENTS), "client_order_id"].dropna().astype(str)) if "client_order_id" in frame else set()
    filled_brokers = set(frame.loc[frame["event_type"].isin(FILL_EVENTS), "broker_order_id"].dropna().astype(str)) if "broker_order_id" in frame else set()

    selected_without_submit_link = group["event_type"].eq("selected") & ~group.get("candidate_id", pd.Series("", index=group.index)).apply(lambda value: _has_link(value, submitted_candidates))
    submit_mask = group["event_type"].isin(SUBMIT_EVENTS)
    submitted_without_fill_link = submit_mask & ~(
        group.get("client_order_id", pd.Series("", index=group.index)).apply(lambda value: _has_link(value, filled_clients))
        | group.get("broker_order_id", pd.Series("", index=group.index)).apply(lambda value: _has_link(value, filled_brokers))
    )
    fill_mask = group["event_type"].isin(FILL_EVENTS)
    monitor_mask = group["event_type"].isin(MONITOR_EVENTS)
    exit_mask = group["event_type"].isin(EXIT_EVENTS)
    pnl_mask = group["event_type"].isin(PNL_EVENTS) | group.get("source", pd.Series("", index=group.index)).astype(str).str.contains("pnl", case=False, na=False)

    return {
        "selected_without_submit_link": int(selected_without_submit_link.sum()),
        "submitted_without_fill_link": int(submitted_without_fill_link.sum()),
        "fill_without_position": int((fill_mask & ~group.get("position_id", pd.Series("", index=group.index)).apply(_present)).sum()),
        "fill_without_trade_id": int((fill_mask & ~group.get("trade_id", pd.Series("", index=group.index)).apply(_present)).sum()),
        "monitor_without_trade_id": int((monitor_mask & ~group.get("trade_id", pd.Series("", index=group.index)).apply(_present)).sum()),
        "exit_without_trade_id": int((exit_mask & ~group.get("trade_id", pd.Series("", index=group.index)).apply(_present)).sum()),
        "close_without_exit_decision": int((exit_mask & ~group.get("exit_decision_id", pd.Series("", index=group.index)).apply(_present)).sum()),
        "pnl_without_trade_id": int((pnl_mask & ~group.get("trade_id", pd.Series("", index=group.index)).apply(_present)).sum()),
    }


def build_lineage_coverage(request, *, target: Engine | None = None) -> pd.DataFrame:
    rows = list(iter_activity_journal_rows(request, target=target))
    base = {f"{field}_coverage": 0.0 for field in COVERAGE_FIELDS}
    broken_zero = {
        "selected_without_submit_link": 0,
        "submitted_without_fill_link": 0,
        "fill_without_position": 0,
        "fill_without_trade_id": 0,
        "monitor_without_trade_id": 0,
        "exit_without_trade_id": 0,
        "close_without_exit_decision": 0,
        "pnl_without_trade_id": 0,
    }
    if not rows:
        return pd.DataFrame([{ "event_type": "ALL", "total_rows": 0, **base, "rows_with_lineage_warning": 0, **broken_zero }])
    frame = pd.DataFrame(rows)
    out = []
    for event_type, group in frame.groupby("event_type", dropna=False):
        item = {"event_type": event_type or "", "total_rows": len(group)}
        for field in COVERAGE_FIELDS:
            item[f"{field}_coverage"] = round(float(group[field].apply(_present).mean()), 6) if field in group else 0.0
        item["rows_with_lineage_warning"] = int(group.get("lineage_warning", pd.Series("", index=group.index)).apply(_present).sum())
        item.update(_broken_chain_counts(frame, group))
        out.append(item)
    all_item = {"event_type": "ALL", "total_rows": len(frame)}
    for field in COVERAGE_FIELDS:
        all_item[f"{field}_coverage"] = round(float(frame[field].apply(_present).mean()), 6) if field in frame else 0.0
    all_item["rows_with_lineage_warning"] = int(frame.get("lineage_warning", pd.Series("", index=frame.index)).apply(_present).sum())
    all_item.update(_broken_chain_counts(frame, frame))
    return pd.concat([pd.DataFrame([all_item]), pd.DataFrame(out).sort_values("event_type")], ignore_index=True)


def write_lineage_coverage(request, output_dir: Path = Path("data/trading/diagnostics"), *, target: Engine | None = None) -> dict[str, Path | int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    frame = build_lineage_coverage(request, target=target)
    csv_path = output_dir / f"activity_lineage_coverage_{stamp}.csv"
    md_path = output_dir / f"activity_lineage_coverage_{stamp}.md"
    frame.to_csv(csv_path, index=False)
    lines = ["# Activity Lineage Coverage", "", f"Rows by event type: {int(frame.loc[frame['event_type'].ne('ALL'), 'total_rows'].sum())}", "", "```csv", frame.to_csv(index=False).strip(), "```"]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"csv_path": csv_path, "markdown_path": md_path, "rows": int(frame.loc[frame["event_type"].ne("ALL"), "total_rows"].sum())}


def _args():
    parser = argparse.ArgumentParser(description="Report activity journal lineage coverage by event type.")
    parser.add_argument("--date")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--output", default="data/trading/diagnostics")
    return parser.parse_args()


def main() -> int:
    args = _args()
    if args.date:
        request = request_for_date(date.fromisoformat(args.date))
    elif args.start and args.end:
        request = request_for_range(parse_utc_datetime(args.start), parse_utc_datetime(args.end))
    else:
        raise SystemExit("Provide --date or both --start and --end")
    result = write_lineage_coverage(request, Path(args.output))
    print("activity_lineage_coverage_status: ok")
    print(f"rows: {result['rows']}")
    print(f"csv_path: {Path(result['csv_path']).resolve()}")
    print(f"markdown_path: {Path(result['markdown_path']).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
