from __future__ import annotations

import math
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterator

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection, Engine

from stockml.db.connection import get_engine
from stockml.db.schema import POSITION_EVENT_TYPES, position_events

LINEAGE_EVENT_COLUMNS = (
    "pipeline_run_id",
    "cycle_id",
    "signal_id",
    "candidate_id",
    "event_key",
    "client_order_id",
    "broker_order_id",
    "trade_id",
    "exit_decision_id",
    "order_intent",
    "strategy_mode",
    "session_mode",
    "candidate_source",
    "model_version",
    "lineage_warning",
)


def position_id_for_symbol(symbol: str) -> str:
    return f"paper:{str(symbol or '').strip().upper()}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def _connection(target: Engine | Connection | None = None, *, required: bool = True) -> Iterator[Connection | None]:
    if target is None:
        engine = get_engine(required=required)
        if engine is None:
            yield None
            return
        with engine.begin() as conn:
            yield conn
        return

    if isinstance(target, Engine):
        with target.begin() as conn:
            yield conn
        return

    yield target


def _json_ready(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_ready(item())
        except Exception:
            pass
    text = str(value)
    return "" if text.lower() in {"nan", "nat", "none", "<na>"} else text


def record_event(
    position_id: str,
    event_type: str,
    source: str,
    details: dict[str, Any] | None = None,
    *,
    target: Engine | Connection | None = None,
    event_at: datetime | None = None,
) -> int | None:
    if event_type not in POSITION_EVENT_TYPES:
        allowed = ", ".join(POSITION_EVENT_TYPES)
        raise ValueError(f"Unknown position event type '{event_type}'. Expected one of: {allowed}")
    clean_position_id = str(position_id or "").strip()
    if not clean_position_id:
        raise ValueError("position_id is required")
    clean_source = str(source or "").strip()
    if not clean_source:
        raise ValueError("source is required")

    with _connection(target, required=True) as conn:
        if conn is None:
            raise RuntimeError("Database engine is unavailable")
        payload = _json_ready(details or {})
        values = {
            "position_id": clean_position_id,
            "event_at": event_at or _utc_now(),
            "event_type": event_type,
            "source": clean_source,
            "details": payload,
        }
        if isinstance(payload, dict):
            table_columns = set(position_events.c.keys())
            for column in LINEAGE_EVENT_COLUMNS:
                if column in table_columns:
                    value = payload.get(column)
                    values[column] = None if value in ("", None) else str(value)
        result = conn.execute(insert(position_events).values(**values))
        try:
            return result.inserted_primary_key[0]
        except Exception:
            return None


def record_event_safely(
    position_id: str,
    event_type: str,
    source: str,
    details: dict[str, Any] | None = None,
    *,
    event_at: datetime | None = None,
) -> bool:
    try:
        with _connection(None, required=False) as conn:
            if conn is None:
                return False
            record_event(position_id, event_type, source, details, target=conn, event_at=event_at)
            return True
    except Exception:
        return False


def _details_event_key(details: Any) -> str:
    return str(details.get("event_key") or "") if isinstance(details, dict) else ""


def record_event_once(
    position_id: str,
    event_type: str,
    source: str,
    details: dict[str, Any] | None = None,
    *,
    event_key: str | None = None,
    event_at: datetime | None = None,
    cooldown_seconds: int | None = None,
) -> bool:
    payload = dict(details or {})
    key = str(event_key or payload.get("event_key") or "").strip()
    if key:
        payload["event_key"] = key
    try:
        with _connection(None, required=False) as conn:
            if conn is None:
                return False
            conditions = [
                position_events.c.position_id == str(position_id or "").strip(),
                position_events.c.event_type == event_type,
                position_events.c.source == source,
            ]
            if cooldown_seconds is not None and cooldown_seconds > 0:
                cutoff = (event_at or _utc_now()) - __import__("datetime").timedelta(seconds=cooldown_seconds)
                conditions.append(position_events.c.event_at >= cutoff)
            rows = conn.execute(select(position_events.c.details).where(*conditions).order_by(position_events.c.event_at.desc()).limit(500)).scalars().all()
            if key and any(_details_event_key(row) == key for row in rows):
                return False
            if not key and cooldown_seconds is not None and rows:
                return False
            record_event(position_id, event_type, source, payload, target=conn, event_at=event_at)
            return True
    except Exception:
        return False
