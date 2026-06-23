from __future__ import annotations

import base64
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.engine import Engine

from portal.services.latest_file_reader import latest_file, safe_read_csv
from stockml.db.connection import get_engine
from stockml.db.schema import POSITION_EVENT_TYPES, position_events


DEFAULT_LIMIT = 200
MAX_LIMIT = 500
CSV_LIMIT = 50_000


@dataclass
class JournalFilters:
    from_date: date
    to_date: date
    event_types: list[str]
    sources: list[str]
    symbol: str
    sort: str = "event_at"
    direction: str = "desc"


def _engine() -> Engine | None:
    return get_engine(required=False)


def _utc_date(value: str | None, default: date) -> date:
    if not value:
        return default
    try:
        return datetime.fromisoformat(value).date()
    except Exception:
        return default


def filters_from_args(args: Any) -> JournalFilters:
    today = datetime.now(timezone.utc).date()
    from_date = _utc_date(args.get("from"), today - timedelta(days=30))
    to_date = _utc_date(args.get("to"), today)
    event_types = [str(value) for value in args.getlist("event_type") if value]
    sources = [str(value) for value in args.getlist("source") if value]
    symbol = str(args.get("symbol", "") or "").strip().upper()
    sort = str(args.get("sort", "event_at") or "event_at")
    direction = "asc" if str(args.get("dir", "desc")).lower() == "asc" else "desc"
    return JournalFilters(from_date, to_date, event_types, sources, symbol, sort, direction)


def _limit(value: Any, default: int = DEFAULT_LIMIT, max_limit: int = MAX_LIMIT) -> int:
    try:
        return max(1, min(int(value), max_limit))
    except Exception:
        return default


def encode_cursor(event_at: Any, event_id: Any) -> str:
    stamp = event_at.isoformat() if isinstance(event_at, datetime) else str(event_at)
    raw = f"{stamp}|{event_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str | None) -> tuple[datetime, int] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        event_at, event_id = raw.rsplit("|", 1)
        return datetime.fromisoformat(event_at.replace("Z", "+00:00")), int(event_id)
    except Exception:
        return None


def _symbol_from_event(row: dict[str, Any]) -> str:
    details = row.get("details") or {}
    if isinstance(details, dict):
        symbol = details.get("symbol") or details.get("ticker")
        if symbol:
            return str(symbol).upper()
    position_id = str(row.get("position_id") or "")
    if ":" in position_id:
        return position_id.rsplit(":", 1)[-1].upper()
    return position_id.upper()


def _details_summary(event_type: str, details: Any) -> str:
    data = details if isinstance(details, dict) else {}
    if data.get("details_summary"):
        return str(data.get("details_summary"))
    if event_type == "filled":
        order_id = data.get("broker_order_id") or data.get("order_id", "")
        return f"{data.get('side', '')} {data.get('filled_qty') or data.get('qty', 'unknown')} {data.get('symbol', '')} filled @ {data.get('filled_avg_price') or data.get('avg_price', 'unknown')} · {order_id}".strip()
    if event_type.startswith("candidate_"):
        return f"{event_type.replace('_', ' ')} · {data.get('symbol', '')} · {data.get('block_reason') or data.get('verdict') or ''}".strip()
    if event_type == "anti_churn_blocked":
        return f"anti churn · {data.get('reason') or 'blocked'} · {data.get('attempted_action') or ''}".strip()
    if event_type == "monitor_close":
        return str(data.get("reason") or "monitor close")
    if event_type == "broker_rejected":
        return f"broker · {data.get('reason') or data.get('message') or 'rejected'}"
    if event_type == "guardrail_blocked":
        return f"guardrail · {data.get('rule') or data.get('reason') or 'blocked'}"
    if event_type == "selected":
        return f"basket {data.get('basket_pos', '?')}/{data.get('basket_size', '?')} from {data.get('run_id', '')}".strip()
    if event_type == "ranked":
        score = data.get("score")
        score_text = f"{float(score):.2f}" if isinstance(score, (int, float)) else str(score or "n/a")
        return f"rank {data.get('rank', '?')} of {data.get('rank_of', 50)} · score {score_text}"
    return str(data.get("reason") or data.get("message") or data.get("decision") or event_type.replace("_", " "))


def _event_record(row: dict[str, Any]) -> dict[str, Any]:
    symbol = _symbol_from_event(row)
    event_type = str(row.get("event_type") or "")
    details = row.get("details") or {}
    return {
        "id": int(row.get("id") or 0),
        "event_at": row.get("event_at").isoformat() if isinstance(row.get("event_at"), datetime) else str(row.get("event_at") or ""),
        "symbol": symbol,
        "name": symbol,
        "event_type": event_type,
        "source": str(row.get("source") or ""),
        "details_summary": _details_summary(event_type, details),
        "details": details,
        "position_id": row.get("position_id") or "",
    }


def _window(filters: JournalFilters) -> tuple[datetime, datetime]:
    start = datetime.combine(filters.from_date, time.min, tzinfo=timezone.utc)
    end = datetime.combine(filters.to_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start, end


def _conditions(filters: JournalFilters, cursor: str | None = None) -> list[Any]:
    start, end = _window(filters)
    conditions = [position_events.c.event_at >= start, position_events.c.event_at < end]
    if filters.event_types:
        conditions.append(position_events.c.event_type.in_(filters.event_types))
    if filters.sources:
        conditions.append(position_events.c.source.in_(filters.sources))
    if filters.symbol:
        conditions.append(position_events.c.position_id.ilike(f"%:{filters.symbol}"))
    decoded = decode_cursor(cursor)
    if decoded:
        cursor_time, cursor_id = decoded
        conditions.append(
            or_(
                position_events.c.event_at < cursor_time,
                and_(position_events.c.event_at == cursor_time, position_events.c.id < cursor_id),
            )
        )
    return conditions


def _artifact_events(root: Path, filters: JournalFilters) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    sources = [
        ("paper_trade_journal", "paper_trade_journal_*.csv", "trade_journal", "submitted"),
        ("paper_pnl", "paper_pnl_*.csv", "pnl", "monitor_safe"),
        ("agent_decisions", "position_decisions_*.csv", "monitor", "monitor_watch"),
        ("operator_actions", "operator_position_actions_*.csv", "operator", "operator_keep"),
        ("portal_outputs", "08_alpaca_paper_order_results_*.csv", "broker", "submitted"),
        ("portal_outputs", "08_alpaca_paper_order_plan_*.csv", "guardrail", "selected"),
    ]
    event_id = 1
    for key, pattern, source, default_type in sources:
        path = latest_file(root, key, pattern)
        frame = safe_read_csv(path, nrows=1000)
        if frame.empty:
            continue
        event_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) if path and path.exists() else datetime.now(timezone.utc)
        for row in frame.fillna("").to_dict("records"):
            symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
            if filters.symbol and symbol != filters.symbol:
                continue
            event_type = str(row.get("event_type") or row.get("lifecycle_state") or row.get("decision") or default_type).lower()
            if event_type not in set(POSITION_EVENT_TYPES):
                if source == "monitor" and event_type in {"watch", "close", "rotate"}:
                    event_type = f"monitor_{event_type}"
                elif source == "operator" and event_type in {"keep", "close", "override"}:
                    event_type = f"operator_{event_type}"
                elif source == "broker" and str(row.get("status") or "").lower() in {"rejected", "error"}:
                    event_type = "broker_rejected"
                else:
                    event_type = default_type
            if filters.event_types and event_type not in filters.event_types:
                continue
            if filters.sources and source not in filters.sources:
                continue
            if not (datetime.combine(filters.from_date, time.min, tzinfo=timezone.utc) <= event_at < datetime.combine(filters.to_date + timedelta(days=1), time.min, tzinfo=timezone.utc)):
                continue
            details = {key: value for key, value in row.items() if value != ""}
            event = _event_record(
                {
                    "id": event_id,
                    "position_id": f"paper:{symbol}" if symbol else "",
                    "event_at": event_at,
                    "event_type": event_type,
                    "source": source,
                    "details": details,
                }
            )
            events.append(event)
            event_id += 1
    events.sort(key=lambda item: (item["event_at"], item["id"]), reverse=True)
    return events


def _artifact_query(root: Path | None, filters: JournalFilters, cursor: str | None, limit: int) -> dict[str, Any]:
    if root is None:
        return {"events": [], "next_cursor": None, "total_in_range": 0}
    events = _artifact_events(root, filters)
    decoded = decode_cursor(cursor)
    if decoded:
        cursor_time, cursor_id = decoded
        events = [
            event
            for event in events
            if datetime.fromisoformat(event["event_at"].replace("Z", "+00:00")) < cursor_time or (datetime.fromisoformat(event["event_at"].replace("Z", "+00:00")) == cursor_time and event["id"] < cursor_id)
        ]
    cap = _limit(limit)
    page = events[:cap]
    next_cursor = encode_cursor(page[-1]["event_at"], page[-1]["id"]) if len(events) > cap and page else None
    return {"events": page, "next_cursor": next_cursor, "total_in_range": len(events)}


def query(filters: JournalFilters, cursor: str | None = None, limit: int = DEFAULT_LIMIT, *, target: Engine | None = None, root: Path | None = None) -> dict[str, Any]:
    engine = target or _engine()
    cap = _limit(limit)
    if engine is None:
        return _artifact_query(root, filters, cursor, cap)
    try:
        conditions = _conditions(filters, cursor)
        base = select(position_events).where(*conditions)
        order = position_events.c.event_at.asc() if filters.direction == "asc" else position_events.c.event_at.desc()
        id_order = position_events.c.id.asc() if filters.direction == "asc" else position_events.c.id.desc()
        with engine.connect() as conn:
            all_rows = conn.execute(select(position_events.c.id).where(*_conditions(filters))).all()
            rows = conn.execute(base.order_by(order, id_order).limit(cap + 1)).mappings().all()
        page_rows = [dict(row) for row in rows[:cap]]
        events = [_event_record(row) for row in page_rows]
        if not events and root is not None:
            fallback = _artifact_query(root, filters, cursor, cap)
            if fallback["events"]:
                fallback["source"] = "csv_artifacts"
                return fallback
        next_cursor = None
        if len(rows) > cap and page_rows:
            last = page_rows[-1]
            next_cursor = encode_cursor(last["event_at"], last["id"])
        return {"events": events, "next_cursor": next_cursor, "total_in_range": len(all_rows)}
    except Exception:
        return _artifact_query(root, filters, cursor, cap)


def iter_csv(filters: JournalFilters, *, target: Engine | None = None, root: Path | None = None) -> Iterable[str]:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["id", "event_at", "symbol", "event_type", "source", "details_summary", "position_id"])
    writer.writeheader()
    yield output.getvalue()
    output.seek(0)
    output.truncate(0)
    cursor = None
    while True:
        payload = query(filters, cursor=cursor, limit=MAX_LIMIT, target=target, root=root)
        for event in payload["events"]:
            writer.writerow({key: event.get(key, "") for key in writer.fieldnames})
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)
        cursor = payload.get("next_cursor")
        if not cursor:
            break
