from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import and_, asc, or_, select
from sqlalchemy.engine import Engine

from stockml.db.connection import get_engine
from stockml.db.schema import position_events


LINEAGE_EXPORT_COLUMNS = ["pipeline_run_id", "cycle_id", "signal_id", "candidate_id", "event_key", "client_order_id", "broker_order_id", "position_id", "trade_id", "exit_decision_id", "order_intent", "strategy_mode", "session_mode", "candidate_source", "model_version", "lineage_warning"]
EXPORT_COLUMNS = ["id", "event_at", "symbol", "event_type", "source", "details_summary", *LINEAGE_EXPORT_COLUMNS]
DEFAULT_BATCH_SIZE = 500


@dataclass(frozen=True)
class ActivityJournalExportRequest:
    start: datetime
    end: datetime
    sources: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()
    symbol: str = ""
    batch_size: int = DEFAULT_BATCH_SIZE


@dataclass(frozen=True)
class ActivityJournalExportResult:
    csv_path: Path
    metadata_path: Path
    metadata: dict[str, Any]


def utc_day_window(value: date) -> tuple[datetime, datetime]:
    start = datetime.combine(value, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def request_for_date(
    value: date,
    *,
    sources: Iterable[str] = (),
    event_types: Iterable[str] = (),
    symbol: str = "",
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> ActivityJournalExportRequest:
    start, end = utc_day_window(value)
    return ActivityJournalExportRequest(
        start=start,
        end=end,
        sources=tuple(source for source in sources if source),
        event_types=tuple(event_type for event_type in event_types if event_type),
        symbol=symbol.strip().upper(),
        batch_size=batch_size,
    )


def request_for_range(
    start: datetime,
    end: datetime,
    *,
    sources: Iterable[str] = (),
    event_types: Iterable[str] = (),
    symbol: str = "",
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> ActivityJournalExportRequest:
    if end <= start:
        raise ValueError("export end must be after start")
    return ActivityJournalExportRequest(
        start=start.astimezone(timezone.utc),
        end=end.astimezone(timezone.utc),
        sources=tuple(source for source in sources if source),
        event_types=tuple(event_type for event_type in event_types if event_type),
        symbol=symbol.strip().upper(),
        batch_size=batch_size,
    )


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
    if event_type == "filled":
        return f"{data.get('qty', 'unknown')} @ ${data.get('avg_price') or data.get('filled_avg_price', 'unknown')} - {data.get('order_id', '')}".strip()
    if event_type == "monitor_close":
        return str(data.get("reason") or "monitor close")
    if event_type == "broker_rejected":
        return f"broker - {data.get('reason') or data.get('message') or 'rejected'}"
    if event_type == "guardrail_blocked":
        return f"guardrail - {data.get('rule') or data.get('reason') or 'blocked'}"
    if event_type == "selected":
        return f"basket {data.get('basket_pos', '?')}/{data.get('basket_size', '?')} from {data.get('run_id', '')}".strip()
    if event_type == "ranked":
        score = data.get("score")
        score_text = f"{float(score):.2f}" if isinstance(score, (int, float)) else str(score or "n/a")
        return f"rank {data.get('rank', '?')} of {data.get('rank_of', 50)} - score {score_text}"
    return str(data.get("reason") or data.get("message") or data.get("decision") or event_type.replace("_", " "))


def _stable_candidate_id(cycle_id: Any, symbol: Any, candidate_source: Any) -> str:
    parts = [str(cycle_id or "").strip(), str(symbol or "").strip().upper(), str(candidate_source or "paper_order_plan").strip()]
    if any(not part for part in parts):
        return ""
    return "cand-" + hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _lineage_value(row: dict[str, Any], field: str) -> Any:
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    value = row.get(field)
    if value in (None, ""):
        value = details.get(field)
    if field == "broker_order_id" and value in (None, ""):
        value = details.get("order_id")
    if field == "position_id":
        value = details.get("position_id") or value or row.get("position_id")
    return "" if value is None else value


def _event_record(row: dict[str, Any]) -> dict[str, Any]:
    event_type = str(row.get("event_type") or "")
    event_at = row.get("event_at")
    record = {
        "id": int(row.get("id") or 0),
        "event_at": event_at.isoformat() if isinstance(event_at, datetime) else str(event_at or ""),
        "symbol": _symbol_from_event(row),
        "event_type": event_type,
        "source": str(row.get("source") or ""),
        "details_summary": _details_summary(event_type, row.get("details") or {}),
    }
    for field in LINEAGE_EXPORT_COLUMNS:
        record[field] = _lineage_value(row, field)
    if not record.get("candidate_id") and record.get("cycle_id") and record.get("symbol") and record.get("event_type") in {"selected", "candidate_scanned", "candidate_blocked", "candidate_submitted", "candidate_skipped_duplicate", "candidate_skipped_anti_churn", "candidate_skipped_meta_label", "candidate_skipped_not_overnight_tradable"}:
        record["candidate_id"] = _stable_candidate_id(record.get("cycle_id"), record.get("symbol"), record.get("candidate_source") or record.get("source"))
    if not record.get("session_mode") and record.get("event_type") in {"selected", "candidate_scanned", "candidate_blocked", "candidate_submitted", "candidate_skipped_duplicate", "candidate_skipped_anti_churn", "candidate_skipped_meta_label", "candidate_skipped_not_overnight_tradable"}:
        record["session_mode"] = "regular_session"
    if not record.get("order_intent") and record.get("event_type") in {"selected", "candidate_submitted"}:
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        side = str(details.get("side") or "").lower()
        record["order_intent"] = "open_long" if side == "buy" else "open_short" if side == "sell" else ""
    return record


def _base_conditions(request: ActivityJournalExportRequest) -> list[Any]:
    conditions: list[Any] = [
        position_events.c.event_at >= request.start,
        position_events.c.event_at < request.end,
    ]
    if request.sources:
        conditions.append(position_events.c.source.in_(request.sources))
    if request.event_types:
        conditions.append(position_events.c.event_type.in_(request.event_types))
    if request.symbol:
        conditions.append(position_events.c.position_id.like(f"%:{request.symbol}"))
    return conditions


def iter_activity_journal_rows(request: ActivityJournalExportRequest, *, target: Engine | None = None) -> Iterable[dict[str, Any]]:
    engine = target or get_engine(required=True)
    if engine is None:
        raise RuntimeError("activity journal export requires a database engine")
    batch_size = max(1, int(request.batch_size or DEFAULT_BATCH_SIZE))
    last_event_at: datetime | None = None
    last_event_id: int | None = None
    with engine.connect() as conn:
        while True:
            conditions = _base_conditions(request)
            if last_event_at is not None and last_event_id is not None:
                conditions.append(
                    or_(
                        position_events.c.event_at > last_event_at,
                        and_(position_events.c.event_at == last_event_at, position_events.c.id > last_event_id),
                    )
                )
            rows = (
                conn.execute(
                    select(position_events)
                    .where(*conditions)
                    .order_by(asc(position_events.c.event_at), asc(position_events.c.id))
                    .limit(batch_size)
                )
                .mappings()
                .all()
            )
            if not rows:
                break
            for row in rows:
                raw = dict(row)
                last_event_at = raw["event_at"]
                last_event_id = int(raw["id"])
                yield _event_record(raw)


def export_activity_journal(
    request: ActivityJournalExportRequest,
    output_dir: Path,
    *,
    target: Engine | None = None,
    now: datetime | None = None,
) -> ActivityJournalExportResult:
    started = now or datetime.now(timezone.utc)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"activity_journal_{stamp}.csv"
    metadata_path = output_dir / f"activity_journal_{stamp}.metadata.json"

    total_rows = 0
    first_event_id: int | None = None
    last_event_id: int | None = None
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        for row in iter_activity_journal_rows(request, target=target):
            if first_event_id is None:
                first_event_id = int(row["id"])
            last_event_id = int(row["id"])
            total_rows += 1
            writer.writerow({column: row.get(column, "") for column in EXPORT_COLUMNS})

    completed = datetime.now(timezone.utc)
    metadata: dict[str, Any] = {
        "export_started_at": started.isoformat(),
        "export_completed_at": completed.isoformat(),
        "requested_start": request.start.isoformat(),
        "requested_end": request.end.isoformat(),
        "total_rows": total_rows,
        "first_event_id": first_event_id,
        "last_event_id": last_event_id,
        "was_truncated": False,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    if metadata["was_truncated"]:
        raise RuntimeError("activity journal full export was truncated")
    return ActivityJournalExportResult(csv_path=csv_path, metadata_path=metadata_path, metadata=metadata)
