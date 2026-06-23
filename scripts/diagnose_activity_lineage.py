from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.engine import Engine

from stockml.common.paths import timestamp
from stockml.trading.activity_journal_export import LINEAGE_EXPORT_COLUMNS, iter_activity_journal_rows, request_for_date, request_for_range, parse_utc_datetime

COVERAGE_FIELDS = ["cycle_id", "candidate_id", "event_key", "client_order_id", "broker_order_id", "position_id", "trade_id", "order_intent", "session_mode"]


def _present(value: Any) -> bool:
    return value not in (None, "") and str(value).strip().lower() not in {"nan", "none", "null", "<na>"}


def build_lineage_coverage(request, *, target: Engine | None = None) -> pd.DataFrame:
    rows = list(iter_activity_journal_rows(request, target=target))
    if not rows:
        return pd.DataFrame([{"event_type": "ALL", "total_rows": 0, **{f"{field}_coverage": 0.0 for field in COVERAGE_FIELDS}, "rows_with_lineage_warning": 0}])
    frame = pd.DataFrame(rows)
    out = []
    for event_type, group in frame.groupby("event_type", dropna=False):
        item = {"event_type": event_type or "", "total_rows": len(group)}
        for field in COVERAGE_FIELDS:
            item[f"{field}_coverage"] = round(float(group[field].apply(_present).mean()), 6) if field in group else 0.0
        item["rows_with_lineage_warning"] = int(group.get("lineage_warning", pd.Series("", index=group.index)).apply(_present).sum())
        out.append(item)
    return pd.DataFrame(out).sort_values("event_type").reset_index(drop=True)


def write_lineage_coverage(request, output_dir: Path = Path("data/trading/diagnostics"), *, target: Engine | None = None) -> dict[str, Path | int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    frame = build_lineage_coverage(request, target=target)
    csv_path = output_dir / f"activity_lineage_coverage_{stamp}.csv"
    md_path = output_dir / f"activity_lineage_coverage_{stamp}.md"
    frame.to_csv(csv_path, index=False)
    lines = ["# Activity Lineage Coverage", "", f"Rows by event type: {int(frame['total_rows'].sum())}", "", "```csv", frame.to_csv(index=False).strip(), "```"]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"csv_path": csv_path, "markdown_path": md_path, "rows": int(frame["total_rows"].sum())}


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
