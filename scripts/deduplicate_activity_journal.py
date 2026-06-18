#!/opt/jupyter-env/bin/python3
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd
from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.common.paths import TRADING_DIR, ensure_data_dirs
from stockml.db.connection import get_engine
from stockml.db.schema import position_events


def _clean(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _signature(details: object) -> tuple[str, str, str, str] | None:
    if not isinstance(details, dict):
        return None
    order_id = _clean(details.get("broker_order_id") or details.get("order_id"))
    status = _clean(details.get("status") or details.get("alpaca_status")).lower()
    qty = _clean(details.get("filled_qty"))
    price = _clean(details.get("filled_avg_price"))
    if not order_id or not qty or not price:
        return None
    return order_id, status, qty, price


def find_duplicate_fills() -> tuple[pd.DataFrame, list[int]]:
    engine = get_engine(required=True)
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                position_events.c.id,
                position_events.c.position_id,
                position_events.c.event_at,
                position_events.c.details,
            ).where(position_events.c.event_type == "filled", position_events.c.source == "alpaca_tracking")
        ).mappings().all()
    groups: dict[tuple[str, str, str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        sig = _signature(row["details"])
        if sig is None:
            continue
        groups[(row["position_id"], *sig)].append(dict(row))

    report_rows: list[dict] = []
    duplicate_ids: list[int] = []
    for key, items in groups.items():
        if len(items) <= 1:
            continue
        items.sort(key=lambda item: (item["event_at"], item["id"]))
        keep = items[0]
        for duplicate in items[1:]:
            duplicate_ids.append(int(duplicate["id"]))
            report_rows.append(
                {
                    "duplicate_event_id": duplicate["id"],
                    "kept_event_id": keep["id"],
                    "position_id": key[0],
                    "broker_order_id": key[1],
                    "status": key[2],
                    "filled_qty": key[3],
                    "filled_avg_price": key[4],
                    "kept_event_at": keep["event_at"],
                    "duplicate_event_at": duplicate["event_at"],
                }
            )
    return pd.DataFrame(report_rows), duplicate_ids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="delete duplicate fill rows after writing the report")
    args = parser.parse_args()
    ensure_data_dirs()
    out_dir = TRADING_DIR / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report, duplicate_ids = find_duplicate_fills()
    path = out_dir / f"activity_journal_duplicate_fills_{stamp}.csv"
    report.to_csv(path, index=False)
    deleted = 0
    if args.apply and duplicate_ids:
        engine = get_engine(required=True)
        with engine.begin() as conn:
            result = conn.execute(delete(position_events).where(position_events.c.id.in_(duplicate_ids)))
            deleted = int(result.rowcount or 0)
    print(f"duplicate_fill_rows: {len(report)}")
    print(f"duplicates_deleted: {deleted}")
    print(f"duplicate_report_path: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
