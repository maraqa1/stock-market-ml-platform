from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd


CLOSE_REASONS = (
    "STOP_LOSS",
    "TIME_STOP",
    "SIGNAL_FLIP",
    "TAKE_PROFIT",
    "ROTATION_OUT",
    "EOD_FLATTEN",
    "MANUAL",
    "OTHER",
)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        parsed = float(value)
        if pd.isna(parsed):
            return default
        return parsed
    except Exception:
        return default


def as_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def direction_sign(value: Any) -> int:
    text = str(value or "").strip().lower()
    if text in {"short", "sell"}:
        return -1
    return 1


def normalize_direction(value: Any) -> str:
    return "short" if direction_sign(value) < 0 else "long"


def classify_close_reason(value: Any = None, details: dict[str, Any] | None = None) -> str:
    details = details or {}
    raw_value = str(value or "").strip().lower()
    raw_source = str(details.get("source") or details.get("trigger_source") or "").strip().lower()
    if raw_value in {"snapshot_flattened", "flattened_from_snapshot"} or raw_source == "position_snapshot_reconstruction":
        return "OTHER"
    haystack = " ".join(
        str(item or "")
        for item in [
            value,
            details.get("close_reason"),
            details.get("exit_reason"),
            details.get("autopilot_reason"),
            details.get("reason"),
            details.get("trigger"),
            details.get("source"),
        ]
    ).lower()
    if any(token in haystack for token in ["hard_stop", "stop_loss", "loss_limit"]):
        return "STOP_LOSS"
    if any(token in haystack for token in ["time_stop", "max_hold", "stale_signal", "stale"]):
        return "TIME_STOP"
    if any(token in haystack for token in ["signal_flip", "reversal", "reverse", "unknown_signal"]):
        return "SIGNAL_FLIP"
    if any(token in haystack for token in ["take_profit", "profit_target"]):
        return "TAKE_PROFIT"
    if any(token in haystack for token in ["rotation", "rotate"]):
        return "ROTATION_OUT"
    if any(token in haystack for token in ["eod", "flatten"]):
        return "EOD_FLATTEN"
    if any(token in haystack for token in ["manual", "operator_close", "operator"]):
        return "MANUAL"
    return "OTHER"


def signed_price_move_bps(start_price: float, end_price: float, direction: Any) -> float:
    start = as_float(start_price)
    end = as_float(end_price)
    if start <= 0:
        return 0.0
    return (end - start) / start * 10000.0 * direction_sign(direction)


def signal_to_entry_bps(signal_price: float, entry_fill: float, direction: Any) -> float:
    signal = as_float(signal_price)
    entry = as_float(entry_fill)
    if signal <= 0 or entry <= 0:
        return 0.0
    sign = direction_sign(direction)
    return (signal - entry) / signal * 10000.0 if sign > 0 else (entry - signal) / signal * 10000.0


def exit_slippage_bps(exit_target: float, exit_fill: float, direction: Any) -> float:
    target = as_float(exit_target)
    fill = as_float(exit_fill)
    if target <= 0 or fill <= 0:
        return 0.0
    sign = direction_sign(direction)
    return (fill - target) / target * 10000.0 if sign > 0 else (target - fill) / target * 10000.0


def modeled_costs_bps(half_spread_at_entry: float | None = None, market_impact_bps: float = 10.0) -> float:
    half_spread = max(as_float(half_spread_at_entry), 0.0)
    return half_spread * 2.0 + float(market_impact_bps)


def mfe_mae_metrics(
    bars: pd.DataFrame | None,
    *,
    entry_fill: float,
    direction: Any,
    opened_at: Any = None,
) -> dict[str, float | int | None]:
    if bars is None or bars.empty or as_float(entry_fill) <= 0:
        return {
            "max_favourable_bps": 0.0,
            "max_adverse_bps": 0.0,
            "minutes_to_first_positive": None,
            "minutes_to_max_favourable": None,
            "minutes_to_max_adverse": None,
        }
    frame = bars.copy()
    if "timestamp" in frame.columns:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        frame = frame.sort_values("timestamp")
    sign = direction_sign(direction)
    entry = as_float(entry_fill)
    opened = as_datetime(opened_at)

    if len(frame) == 1 and "close" in frame.columns:
        move = signed_price_move_bps(entry, as_float(frame.iloc[0].get("close")), direction)
        minutes = _minutes_between(opened, frame.iloc[0].get("timestamp"))
        return {
            "max_favourable_bps": round(move, 4),
            "max_adverse_bps": round(move, 4),
            "minutes_to_first_positive": minutes if move > 0 else None,
            "minutes_to_max_favourable": minutes,
            "minutes_to_max_adverse": minutes,
        }

    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        high = as_float(row.get("high") if "high" in frame.columns else row.get("close"))
        low = as_float(row.get("low") if "low" in frame.columns else row.get("close"))
        if sign > 0:
            favourable = (high - entry) / entry * 10000.0
            adverse = (low - entry) / entry * 10000.0
        else:
            favourable = (entry - low) / entry * 10000.0
            adverse = (entry - high) / entry * 10000.0
        rows.append({"timestamp": row.get("timestamp"), "favourable": favourable, "adverse": adverse})

    fav_values = [0.0, *[row["favourable"] for row in rows]]
    adv_values = [0.0, *[row["adverse"] for row in rows]]
    max_fav = max(fav_values)
    max_adv = min(adv_values)
    first_positive = next((row for row in rows if row["favourable"] > 0), None)
    max_fav_row = next((row for row in rows if row["favourable"] == max_fav), None) if max_fav != 0 else None
    max_adv_row = next((row for row in rows if row["adverse"] == max_adv), None) if max_adv != 0 else None
    return {
        "max_favourable_bps": round(max_fav, 4),
        "max_adverse_bps": round(max_adv, 4),
        "minutes_to_first_positive": _minutes_between(opened, first_positive.get("timestamp")) if first_positive else None,
        "minutes_to_max_favourable": _minutes_between(opened, max_fav_row.get("timestamp")) if max_fav_row else None,
        "minutes_to_max_adverse": _minutes_between(opened, max_adv_row.get("timestamp")) if max_adv_row else None,
    }


def _minutes_between(start: datetime | None, end: Any) -> int | None:
    if start is None:
        return None
    parsed_end = as_datetime(end)
    if parsed_end is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return int(round((parsed_end - start).total_seconds() / 60.0))
