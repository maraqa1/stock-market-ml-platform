from __future__ import annotations

from datetime import timedelta
from typing import Literal

import pandas as pd


Direction = Literal["long", "short"]


def _normalise_bars(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return bars.copy()
    out = bars.copy()
    if "timestamp" not in out.columns:
        raise ValueError("bars must include a timestamp column")
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    return out.sort_values("timestamp").reset_index(drop=True)


def _decision_time(value: pd.Timestamp) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def compute_continuation_label(
    bars: pd.DataFrame,
    decision_time: pd.Timestamp,
    direction: Direction,
    horizon_minutes: int = 30,
    threshold_bps: int = 50,
) -> int | None:
    """Return whether price continued by threshold after a lagged entry.

    Decision time ``t`` assumes the bar ending at ``t`` has just closed,
    but neither features nor labels may use that bar. Entry is the open of
    the next 5-minute bar at ``t + 5min``. The label then checks whether the
    high (long) or low (short) reaches the threshold within the horizon.
    """

    direction = direction.lower()  # type: ignore[assignment]
    if direction not in {"long", "short"}:
        raise ValueError("direction must be 'long' or 'short'")

    frame = _normalise_bars(bars)
    if frame.empty:
        return None
    decision = _decision_time(decision_time)
    entry_time = decision + timedelta(minutes=5)
    horizon_end = entry_time + timedelta(minutes=horizon_minutes)

    future = frame[(frame["timestamp"] >= entry_time) & (frame["timestamp"] <= horizon_end)].copy()
    if future.empty:
        return None

    entry_row = future[future["timestamp"].eq(entry_time)]
    if entry_row.empty or "open" not in future.columns:
        return None
    entry = pd.to_numeric(entry_row.iloc[0].get("open"), errors="coerce")
    if pd.isna(entry) or float(entry) <= 0:
        return None
    entry_price = float(entry)
    threshold = threshold_bps / 10_000.0

    if direction == "long":
        if "high" not in future.columns:
            return None
        high = pd.to_numeric(future["high"], errors="coerce").max()
        if pd.isna(high):
            return None
        return int((float(high) / entry_price - 1.0) >= threshold)

    if "low" not in future.columns:
        return None
    low = pd.to_numeric(future["low"], errors="coerce").min()
    if pd.isna(low):
        return None
    return int((entry_price / float(low) - 1.0) >= threshold)


def realized_move_bps(
    bars: pd.DataFrame,
    decision_time: pd.Timestamp,
    direction: Direction,
    horizon_minutes: int = 30,
) -> float | None:
    frame = _normalise_bars(bars)
    if frame.empty:
        return None
    decision = _decision_time(decision_time)
    entry_time = decision + timedelta(minutes=5)
    horizon_end = entry_time + timedelta(minutes=horizon_minutes)
    future = frame[(frame["timestamp"] >= entry_time) & (frame["timestamp"] <= horizon_end)].copy()
    if future.empty or "open" not in future.columns:
        return None
    entry_row = future[future["timestamp"].eq(entry_time)]
    if entry_row.empty:
        return None
    entry = pd.to_numeric(entry_row.iloc[0].get("open"), errors="coerce")
    if pd.isna(entry) or float(entry) <= 0:
        return None
    entry_price = float(entry)
    direction = direction.lower()  # type: ignore[assignment]
    if direction == "long":
        high = pd.to_numeric(future.get("high"), errors="coerce").max()
        return None if pd.isna(high) else (float(high) / entry_price - 1.0) * 10_000
    low = pd.to_numeric(future.get("low"), errors="coerce").min()
    return None if pd.isna(low) else (entry_price / float(low) - 1.0) * 10_000
