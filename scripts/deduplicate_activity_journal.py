from __future__ import annotations

import argparse
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine

from stockml.common.paths import timestamp
from stockml.db.connection import get_engine
from stockml.db.schema import position_events
from stockml.trading.activity_journal_export import iter_activity_journal_rows, parse_utc_datetime, request_for_date, request_for_range

DUPLICATE_KEY_FIELDS = ["broker_order_id", "event_type", "filled_qty", "filled_avg_price", "status"]


def _details_value(row: dict[str, Any], field: str) -> str:
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    if field == "broker_order_id":
        value = row.get("broker_order_id") or details.get("broker_order_id") or details.get("order_id")
    else:
        value = row.get(field) or details.get(field)
    return str(value or "").strip()


def duplicate_fill_report_rows(request, *, target: Engine | None = None) -> list[dict[str, Any]]:
    engine = target or get_engine(required=True)
    rows = []
    with engine.connect() as conn:
        db_rows = conn.execute(
            select(position_events)
            .where(position_events.c.event_at >= request.start, position_events.c.event_at < request.end, position_events.c.event_type == "filled")
            .order_by(position_events.c.event_at.asc(), position_events.c.id.asc())
        ).mappings().all()
    seen: dict[tuple[str, str, str, str, str], int] = {}
    for raw_row in db_rows:
        row = dict(raw_row)
        key = tuple(_details_value(row, field) for field in DUPLICATE_KEY_FIELDS)
        if not key[0]:
            continue
        first_id = seen.setdefault(key, int(row["id"]))
        is_duplicate = first_id != int(row["id"])
        rows.append({
            "event_id": int(row["id"]),
            "keep_event_id": first_id,
            "is_duplicate": is_duplicate,
            "broker_order_id": key[0],
            "event_type": key[1],
            "filled_qty": key[2],
            "filled_avg_price": key[3],
            "status": key[4],
        })
    return rows


def write_duplicate_fill_report(request, output_dir: Path = Path("data/trading/diagnostics"), *, target: Engine | None = None, apply: bool = False) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    rows = duplicate_fill_report_rows(request, target=target)
    frame = pd.DataFrame(rows, columns=["event_id", "keep_event_id", "is_duplicate", "broker_order_id", "event_type", "filled_qty", "filled_avg_price", "status"])
    path = output_dir / f"activity_journal_duplicate_fills_{stamp}.csv"
    frame.to_csv(path, index=False)
    removed = 0
    backup_path = None
    if apply and not frame.empty:
        engine = target or get_engine(required=True)
        duplicate_ids = [int(value) for value in frame.loc[frame["is_duplicate"], "event_id"].tolist()]
        backup_path = output_dir / f"activity_journal_duplicate_fills_backup_{stamp}.json"
        with engine.begin() as conn:
            backup = [dict(row) for row in conn.execute(select(position_events).where(position_events.c.id.in_(duplicate_ids))).mappings().all()] if duplicate_ids else []
            backup_path.write_text(json.dumps(backup, default=str, indent=2), encoding="utf-8")
            if duplicate_ids:
                result = conn.execute(delete(position_events).where(position_events.c.id.in_(duplicate_ids)))
                removed = int(result.rowcount or 0)
    return {"report_path": path, "duplicate_rows": int(frame["is_duplicate"].sum()) if not frame.empty else 0, "removed_rows": removed, "backup_path": backup_path}


def _args():
    parser = argparse.ArgumentParser(description="Report or remove duplicate historical filled activity events.")
    parser.add_argument("--date")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--output", default="data/trading/diagnostics")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _args()
    if args.date:
        request = request_for_date(date.fromisoformat(args.date))
    elif args.start and args.end:
        request = request_for_range(parse_utc_datetime(args.start), parse_utc_datetime(args.end))
    else:
        raise SystemExit("Provide --date or both --start and --end")
    result = write_duplicate_fill_report(request, Path(args.output), apply=args.apply)
    print("activity_journal_duplicate_fill_status: ok")
    print(f"duplicate_rows: {result['duplicate_rows']}")
    print(f"removed_rows: {result['removed_rows']}")
    print(f"report_path: {Path(result['report_path']).resolve()}")
    if result.get("backup_path"):
        print(f"backup_path: {Path(result['backup_path']).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
