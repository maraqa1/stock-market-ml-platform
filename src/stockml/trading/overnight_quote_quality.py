from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class QuoteQualityResult:
    ok: bool
    spread_bps: float | None
    freshness_seconds: float | None
    reason: str = ""


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


def _aware(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = pd.to_datetime(value, utc=True).to_pydatetime()
        except Exception:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def evaluate_quote_quality(data: dict[str, Any], *, max_spread_bps: float, max_freshness_seconds: float = 900.0, now: datetime | None = None) -> QuoteQualityResult:
    spread = _float(data.get("spread_bps"))
    bid = _float(data.get("bid") or data.get("bid_price"))
    ask = _float(data.get("ask") or data.get("ask_price"))
    if spread is None and bid and ask and bid > 0 and ask >= bid:
        mid = (bid + ask) / 2.0
        spread = ((ask - bid) / mid) * 10000.0 if mid > 0 else None
    if spread is not None and spread > max_spread_bps:
        return QuoteQualityResult(False, spread, None, "spread_too_wide")
    quote_time = _aware(data.get("quote_timestamp") or data.get("quote_time") or data.get("latest_quote_at"))
    freshness = None
    if quote_time is not None:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        freshness = max(0.0, (current.astimezone(timezone.utc) - quote_time).total_seconds())
        if freshness > max_freshness_seconds:
            return QuoteQualityResult(False, spread, freshness, "quote_stale")
    return QuoteQualityResult(True, spread, freshness, "")
