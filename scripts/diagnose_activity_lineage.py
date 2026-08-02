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
    "strategy_version",
    "cycle_id",
    "candidate_id",
    "scan_candidate_id",
    "parent_candidate_id",
    "event_key",
    "client_order_id",
    "broker_order_id",
    "position_id",
    "trade_id",
    "exit_decision_id",
    "order_intent",
    "session_mode",
    "event_session_mode",
    "planned_execution_session_mode",
    "actual_submission_session_mode",
    "lineage_warning",
]

SCAN_EVENTS = {"candidate_scanned"}
SUBMIT_EVENTS = {"submitted", "order_submitted", "candidate_submitted"}
FILL_EVENTS = {"filled", "close_filled"}
MONITOR_EVENTS = {"monitor_safe", "monitor_watch", "monitor_rotate", "monitor_close"}
EXIT_EVENTS = {"monitor_close", "operator_close", "close_submitted", "close_filled"}
CLOSE_EVENTS = {"operator_close", "close_submitted", "close_filled", "monitor_close"}


def _present(value: Any) -> bool:
    return value not in (None, "") and str(value).strip().lower() not in {"nan", "none", "null", "<na>"}


def _links(series: pd.Series) -> set[str]:
    return {str(value) for value in series.dropna().tolist() if _present(value)}


def _broken_chain_counts(frame: pd.DataFrame, group: pd.DataFrame) -> dict[str, int]:
    empty = pd.Series("", index=group.index)
    scanned_ids = _links(frame.loc[frame["event_type"].isin(SCAN_EVENTS), "candidate_id"]) if "candidate_id" in frame else set()
    scanned_ids |= _links(frame.loc[frame["event_type"].isin(SCAN_EVENTS), "parent_candidate_id"]) if "parent_candidate_id" in frame else set()
    submitted_ids = _links(frame.loc[frame["event_type"].isin(SUBMIT_EVENTS), "candidate_id"]) if "candidate_id" in frame else set()
    submitted_ids |= _links(frame.loc[frame["event_type"].isin(SUBMIT_EVENTS), "parent_candidate_id"]) if "parent_candidate_id" in frame else set()

    candidate_ids = group.get("candidate_id", empty).astype(str)
    selected_mask = group["event_type"].eq("selected")
    submit_mask = group["event_type"].isin(SUBMIT_EVENTS)
    fill_mask = group["event_type"].isin(FILL_EVENTS)
    monitor_mask = group["event_type"].isin(MONITOR_EVENTS)
    exit_mask = group["event_type"].isin(EXIT_EVENTS)
    close_mask = group["event_type"].isin(CLOSE_EVENTS)

    details_summary = group.get("details_summary", empty).astype(str).str.lower()
    session_mode = group.get("session_mode", empty).astype(str)
    event_session_mode = group.get("event_session_mode", empty).astype(str)
    actual_session_mode = group.get("actual_submission_session_mode", empty).astype(str)

    duplicate_submissions = 0
    cycle_cap_violations = 0
    if {"cycle_id", "symbol", "event_type"}.issubset(frame.columns):
        submissions = frame[frame["event_type"].isin(SUBMIT_EVENTS)].copy()
        if not submissions.empty:
            grouped = submissions.groupby(["cycle_id", "symbol"], dropna=False).size()
            duplicate_submissions = int((grouped > 1).sum())
            cycle_counts = submissions.groupby("cycle_id", dropna=False).size()
            cycle_cap_violations = int((cycle_counts > 1).sum())

    submitted_marked_open = submit_mask & (
        group.get("position_id", empty).apply(_present)
        | group.get("trade_id", empty).apply(_present)
        | details_summary.str.contains("opened", na=False)
    )
    session_mismatch = (
        group.get("event_session_mode", empty).apply(_present)
        & group.get("actual_submission_session_mode", empty).apply(_present)
        & event_session_mode.ne(actual_session_mode)
    ) | (
        group.get("session_mode", empty).apply(_present)
        & group.get("event_session_mode", empty).apply(_present)
        & group["event_type"].isin({"selected", "candidate_scanned", "candidate_blocked"})
        & session_mode.ne(event_session_mode)
    )

    return {
        "selected_without_scan_link": int((selected_mask & ~candidate_ids.isin(scanned_ids)).sum()),
        "selected_without_submit_link": int((selected_mask & ~candidate_ids.isin(submitted_ids)).sum()),
        "submit_without_client_order_id": int((submit_mask & ~group.get("client_order_id", empty).apply(_present)).sum()),
        "submit_without_broker_order_id": int((submit_mask & ~group.get("broker_order_id", empty).apply(_present)).sum()),
        "submitted_marked_open_before_fill": int(submitted_marked_open.sum()),
        "fill_without_position_id": int((fill_mask & ~group.get("position_id", empty).apply(_present)).sum()),
        "fill_without_trade_id": int((fill_mask & ~group.get("trade_id", empty).apply(_present)).sum()),
        "monitor_without_trade_id": int((monitor_mask & ~group.get("trade_id", empty).apply(_present)).sum()),
        "exit_without_exit_decision_id": int((exit_mask & ~group.get("exit_decision_id", empty).apply(_present)).sum()),
        "close_without_original_trade_id": int((close_mask & ~group.get("trade_id", empty).apply(_present)).sum()),
        "session_mode_mismatch": int(session_mismatch.sum()),
        "duplicate_submission_same_symbol": duplicate_submissions if group is frame else 0,
        "cycle_submission_cap_violation": cycle_cap_violations if group is frame else 0,
    }


def _zero_metrics() -> dict[str, int]:
    return {
        "selected_without_scan_link": 0,
        "selected_without_submit_link": 0,
        "submit_without_client_order_id": 0,
        "submit_without_broker_order_id": 0,
        "submitted_marked_open_before_fill": 0,
        "fill_without_position_id": 0,
        "fill_without_trade_id": 0,
        "monitor_without_trade_id": 0,
        "exit_without_exit_decision_id": 0,
        "close_without_original_trade_id": 0,
        "session_mode_mismatch": 0,
        "duplicate_submission_same_symbol": 0,
        "cycle_submission_cap_violation": 0,
    }


def build_lineage_coverage(request, *, target: Engine | None = None) -> pd.DataFrame:
    rows = list(iter_activity_journal_rows(request, target=target))
    base = {f"{field}_coverage": 0.0 for field in COVERAGE_FIELDS}
    if not rows:
        return pd.DataFrame([{"event_type": "ALL", "total_rows": 0, **base, "rows_with_lineage_warning": 0, **_zero_metrics()}])
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
