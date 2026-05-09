from __future__ import annotations

import math
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterator

from sqlalchemy import insert
from sqlalchemy.engine import Connection, Engine

from stockml.db.connection import get_engine
from stockml.db.schema import POSITION_EVENT_TYPES, position_events


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
        result = conn.execute(
            insert(position_events).values(
                position_id=clean_position_id,
                event_at=event_at or _utc_now(),
                event_type=event_type,
                source=clean_source,
                details=_json_ready(details or {}),
            )
        )
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
