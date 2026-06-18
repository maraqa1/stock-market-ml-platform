from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

US_MARKET_TZ = ZoneInfo("America/New_York")
SESSION_MODES = ("regular_session", "pre_market", "after_hours", "overnight_24_5", "weekend_closed")


def _aware(value: datetime | None = None) -> datetime:
    out = value or datetime.now(timezone.utc)
    if out.tzinfo is None:
        return out.replace(tzinfo=timezone.utc)
    return out


def classify_session_mode(value: datetime | None = None) -> str:
    eastern = _aware(value).astimezone(US_MARKET_TZ)
    weekday = eastern.weekday()
    current = eastern.time()
    if weekday >= 5:
        return "weekend_closed"
    if time(9, 30) <= current < time(16, 0):
        return "regular_session"
    if time(4, 0) <= current < time(9, 30):
        return "pre_market"
    if time(16, 0) <= current < time(20, 0):
        return "after_hours"
    return "overnight_24_5"


def is_extended_session(mode: str) -> bool:
    return mode in {"pre_market", "after_hours", "overnight_24_5"}
