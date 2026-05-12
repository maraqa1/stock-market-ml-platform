from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from stockml.common.paths import PROJECT_ROOT
from stockml.autopilot.policy import guarded_paper_close


CONFIG_PATH = PROJECT_ROOT / "config" / "eod.yaml"
MARKET_TZ = ZoneInfo("America/New_York")
EOD_STATES = ("review", "trim", "observe", "flatten", "verify", "postclose", "inactive")


@dataclass(frozen=True)
class EODConfig:
    flatten_all_at_t_minus_5: bool = True
    trim_weak_at_t_minus_15: bool = True
    holdover_allowed: bool = False
    time_stop_days: int = 20
    weak_loss_pct: float = -1.0
    winner_hold_pct: float = 2.0
    t_minus_30_min: int = 30
    t_minus_15_min: int = 15
    t_minus_5_min: int = 5
    t_minus_1_min: int = 1
    market_close_time_local: str = "16:00"


def load_config(path: Path | str = CONFIG_PATH) -> EODConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    data = dict(payload.get("eod") or {})
    return EODConfig(
        flatten_all_at_t_minus_5=bool(data.get("flatten_all_at_t_minus_5", True)),
        trim_weak_at_t_minus_15=bool(data.get("trim_weak_at_t_minus_15", True)),
        holdover_allowed=bool(data.get("holdover_allowed", False)),
        time_stop_days=int(data.get("time_stop_days", 20)),
        weak_loss_pct=float(data.get("weak_loss_pct", -1.0)),
        winner_hold_pct=float(data.get("winner_hold_pct", 2.0)),
        t_minus_30_min=int(data.get("t_minus_30_min", 30)),
        t_minus_15_min=int(data.get("t_minus_15_min", 15)),
        t_minus_5_min=int(data.get("t_minus_5_min", 5)),
        t_minus_1_min=int(data.get("t_minus_1_min", 1)),
        market_close_time_local=str(data.get("market_close_time_local", "16:00")),
    )


def _local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=MARKET_TZ)
    return value.astimezone(MARKET_TZ)


def _close_at(session_date: date, config: EODConfig) -> datetime:
    hour, minute = [int(part) for part in config.market_close_time_local.split(":", 1)]
    return datetime.combine(session_date, time(hour, minute), tzinfo=MARKET_TZ)


def eod_state(now: datetime, config: EODConfig | None = None, *, close_at: datetime | None = None) -> str:
    cfg = config or load_config()
    local_now = _local(now)
    close = _local(close_at) if close_at else _close_at(local_now.date(), cfg)
    if local_now >= close:
        return "postclose"
    minutes = (close - local_now).total_seconds() / 60
    if minutes <= cfg.t_minus_1_min:
        return "verify"
    if minutes <= cfg.t_minus_5_min:
        return "flatten"
    if minutes <= cfg.t_minus_15_min:
        return "observe" if minutes <= 10 else "trim"
    if minutes <= cfg.t_minus_30_min:
        return "review"
    return "inactive"


def _float(value: Any) -> float:
    try:
        number = float(value)
        if pd.isna(number):
            return 0.0
        return number
    except Exception:
        return 0.0


def _symbol(row: dict[str, Any]) -> str:
    return str(row.get("symbol") or "").upper()


def _latest_monitor_by_symbol(monitor_decisions: pd.DataFrame | None) -> dict[str, str]:
    if monitor_decisions is None or monitor_decisions.empty or "symbol" not in monitor_decisions.columns:
        return {}
    frame = monitor_decisions.copy()
    if "decision" not in frame.columns:
        return {}
    return {
        str(row.get("symbol") or "").upper(): str(row.get("decision") or "").lower()
        for row in frame.fillna("").to_dict("records")
        if row.get("symbol")
    }


def tag_dispositions(
    positions: pd.DataFrame,
    *,
    monitor_decisions: pd.DataFrame | None = None,
    shortlist_symbols: set[str] | None = None,
    config: EODConfig | None = None,
) -> list[dict[str, Any]]:
    cfg = config or load_config()
    monitor = _latest_monitor_by_symbol(monitor_decisions)
    shortlist = {symbol.upper() for symbol in (shortlist_symbols or set()) if symbol}
    rows: list[dict[str, Any]] = []
    for row in positions.fillna("").to_dict("records"):
        symbol = _symbol(row)
        plpc = _float(row.get("unrealized_plpc"))
        age = _float(row.get("age_days") or row.get("age") or 0)
        decision = monitor.get(symbol, "")
        dropped = bool(shortlist and symbol not in shortlist)
        disposition = "none"
        reason = "position_within_eod_rules"
        if age >= cfg.time_stop_days:
            disposition, reason = "stale", "time_stop"
        elif plpc < (cfg.weak_loss_pct / 100.0) and age >= 1:
            disposition, reason = "weak", "loss_and_age"
        elif decision and decision not in {"keep", "safe", "hold", "watch"}:
            disposition, reason = "weak", "negative_monitor_recommendation"
        elif dropped:
            disposition, reason = "weak", "dropped_from_shortlist"
        elif plpc >= (cfg.winner_hold_pct / 100.0) and decision in {"", "safe", "keep", "hold", "watch"}:
            disposition, reason = "winner_hold", "profitable_winner"
        rows.append({"symbol": symbol, "disposition": disposition, "reason": reason, "plpc": plpc, "age": age})
    return rows


def banner_for_state(state: str, *, trim_count: int = 0, flatten_count: int = 0, remaining_count: int = 0, flattened_count: int = 0) -> str:
    if state == "review":
        return "EOD review running."
    if state == "trim":
        return f"EOD trim: closing {trim_count} weak/stale positions."
    if state == "flatten":
        return f"EOD flatten in progress: closing {flatten_count} positions."
    if state == "postclose" and remaining_count > 0:
        return f"Held overnight: {remaining_count} positions did not flatten."
    if state == "postclose":
        return f"Market closed. {flattened_count} positions flattened, {remaining_count} remaining."
    if state == "verify" and remaining_count > 0:
        return f"EOD verify: {remaining_count} positions still open."
    return ""


def run_eod_tick(
    positions: pd.DataFrame,
    *,
    now: datetime,
    state: dict[str, Any],
    monitor_decisions: pd.DataFrame | None = None,
    shortlist_symbols: set[str] | None = None,
    config: EODConfig | None = None,
    open_orders: int = 0,
    close_func: Callable[[str, str], dict[str, Any]] | None = None,
    close_at: datetime | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    stage = eod_state(now, cfg, close_at=close_at)
    if stage == "inactive":
        return {"eod_state": "inactive", "eod_banner": "", "eod_actions": 0, "eod_flatten_submitted": 0, "eod_remaining": int(len(positions))}

    dispositions = tag_dispositions(positions, monitor_decisions=monitor_decisions, shortlist_symbols=shortlist_symbols, config=cfg)
    disposition_by_symbol = {row["symbol"]: row for row in dispositions}
    symbols_to_close: list[str] = []
    if stage == "trim" and cfg.trim_weak_at_t_minus_15:
        symbols_to_close = [row["symbol"] for row in dispositions if row["disposition"] in {"weak", "stale"}]
    elif stage == "flatten" and cfg.flatten_all_at_t_minus_5:
        symbols_to_close = [
            row["symbol"]
            for row in dispositions
            if not (cfg.holdover_allowed and row["disposition"] == "winner_hold")
        ]

    submitted = 0
    notes: list[str] = []
    if open_orders > 0 and symbols_to_close:
        notes.append("skipped:open_orders_in_flight")
        symbols_to_close = []

    for symbol in symbols_to_close:
        result = guarded_paper_close(symbol, source=f"eod_{stage}", action_func=close_func)
        status = str(result.get("status") or "")
        message = str(result.get("message") or "")
        if status == "submitted":
            submitted += 1
        reason = (disposition_by_symbol.get(symbol) or {}).get("reason", "eod_flatten")
        notes.append(f"{symbol}:{reason}:{status or 'unknown'}:{message or 'no_message'}")

    remaining = int(len(positions))
    flattened = submitted
    banner = banner_for_state(
        stage,
        trim_count=len(symbols_to_close),
        flatten_count=len(symbols_to_close),
        remaining_count=remaining,
        flattened_count=flattened,
    )
    return {
        "eod_state": stage,
        "eod_banner": banner,
        "eod_actions": len(symbols_to_close),
        "eod_flatten_submitted": submitted,
        "eod_remaining": remaining,
        "eod_action_notes": "; ".join(notes[:10]),
        "eod_dispositions": dispositions,
        "eod_holdover_allowed": cfg.holdover_allowed,
    }
