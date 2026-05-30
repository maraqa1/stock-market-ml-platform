from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert
from sqlalchemy.engine import Engine

from stockml.db.connection import get_engine
from stockml.db.schema import arbitration_conflicts


def _aware(value: datetime | None = None) -> datetime:
    stamp = value or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def log_conflict(
    symbol: str,
    multi_day_action: str | None,
    same_day_action: str | None,
    resolution: str,
    *,
    details: dict[str, Any] | None = None,
    engine: Engine | None = None,
    now: datetime | None = None,
) -> None:
    db = engine or get_engine(required=False)
    if db is None:
        return
    with db.begin() as conn:
        conn.execute(
            insert(arbitration_conflicts).values(
                logged_at=_aware(now),
                symbol=str(symbol).upper(),
                multi_day_action=multi_day_action,
                same_day_action=same_day_action,
                resolution=resolution,
                details=dict(details or {}),
            )
        )
