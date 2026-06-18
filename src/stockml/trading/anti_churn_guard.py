from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.services.events import position_id_for_symbol, record_event_once

ANTI_CHURN_REPORT_COLUMNS = [
    "symbol",
    "blocked_action",
    "reason",
    "existing_position_age_minutes",
    "last_trade_time",
    "last_trade_side",
    "attempted_side",
    "cycle_id",
    "decision",
]

DEFAULT_ALLOWED_EARLY_CLOSE_REASONS = {
    "hard_stop_hit",
    "hard_stop_loss",
    "take_profit_hit",
    "manual_kill",
    "broker_error_correction",
    "duplicate_order_correction",
    "emergency_risk_breach",
    "trailing_profit_giveback",
    "fresh_signal_profit_giveback",
}


@dataclass(frozen=True)
class AntiChurnConfig:
    enabled: bool = True
    minimum_hold_minutes: int = 30
    cooldown_minutes_after_close: int = 60
    block_same_cycle_open_close: bool = True
    block_reverse_same_symbol_same_day: bool = True
    allow_early_close_reasons: set[str] = field(default_factory=lambda: set(DEFAULT_ALLOWED_EARLY_CLOSE_REASONS))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _symbol(row: dict[str, Any]) -> str:
    return _text(row.get("symbol") or row.get("ticker")).upper()


def normalize_side(value: Any) -> str:
    side = _text(value).lower()
    if side in {"buy", "long", "cover", "covered"}:
        return "buy"
    if side in {"sell", "short"}:
        return "sell"
    return side


def position_side(value: Any) -> str:
    side = _text(value).lower()
    qty = None
    try:
        qty = float(value)
    except Exception:
        qty = None
    if side in {"long", "buy"}:
        return "buy"
    if side in {"short", "sell"}:
        return "sell"
    if qty is not None:
        return "sell" if qty < 0 else "buy"
    return side


def is_close_action(action: dict[str, Any]) -> bool:
    action_text = _text(action.get("action") or action.get("order_action") or action.get("decision")).lower()
    if action_text in {"close", "close_position", "sell_to_close", "buy_to_cover"}:
        return True
    if action_text in {"open", "open_position", "buy", "sell_short"}:
        return False
    return bool(action.get("is_close"))


def is_open_action(action: dict[str, Any]) -> bool:
    action_text = _text(action.get("action") or action.get("order_action") or action.get("decision")).lower()
    if action_text in {"open", "open_position", "buy", "sell_short"}:
        return True
    if action_text in {"close", "close_position", "sell_to_close", "buy_to_cover"}:
        return False
    return bool(action.get("is_open"))


def _action_side(action: dict[str, Any]) -> str:
    return normalize_side(action.get("side") or action.get("attempted_side") or action.get("order_side"))


def _close_reason(action: dict[str, Any]) -> str:
    return _text(action.get("reason") or action.get("close_reason") or action.get("decision_reason") or action.get("block_reason")).lower()


def _opened_at(position: dict[str, Any]) -> datetime | None:
    for key in ("opened_at", "entry_time", "created_at", "submitted_at", "filled_at", "opened_timestamp"):
        dt = _aware(position.get(key))
        if dt:
            return dt
    return None


def _position_age_minutes(position: dict[str, Any] | None, now: datetime) -> float | None:
    if not position:
        return None
    if position.get("age_minutes") not in (None, ""):
        try:
            return max(0.0, float(position.get("age_minutes")))
        except Exception:
            pass
    opened = _opened_at(position)
    if not opened:
        return None
    return max(0.0, (now - opened).total_seconds() / 60.0)


def _last_trade_at(row: dict[str, Any]) -> datetime | None:
    for key in ("closed_at", "filled_at", "submitted_at", "event_at", "timestamp", "time"):
        dt = _aware(row.get(key))
        if dt:
            return dt
    return None


def _last_trade_side(row: dict[str, Any]) -> str:
    return position_side(row.get("side") or row.get("direction") or row.get("last_trade_side") or row.get("qty"))


def _opposite(a: str, b: str) -> bool:
    return {a, b} == {"buy", "sell"}


def _report_row(symbol: str, action: str, reason: str, age: float | None, last_trade: dict[str, Any] | None, attempted_side: str, cycle_id: str) -> dict[str, Any]:
    last_time = _last_trade_at(last_trade or {})
    return {
        "symbol": symbol,
        "blocked_action": action,
        "reason": reason,
        "existing_position_age_minutes": round(age, 2) if age is not None else "",
        "last_trade_time": last_time.isoformat() if last_time else "",
        "last_trade_side": _last_trade_side(last_trade or {}),
        "attempted_side": attempted_side,
        "cycle_id": cycle_id,
        "decision": "manual_review",
    }


def find_latest_trade(symbol: str, trades: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    matches = [row for row in trades if _symbol(row) == symbol]
    matches.sort(key=lambda row: _last_trade_at(row) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return matches[0] if matches else None


def guard_actions(
    actions: Iterable[dict[str, Any]],
    *,
    open_positions: Iterable[dict[str, Any]] | None = None,
    trade_history: Iterable[dict[str, Any]] | None = None,
    now: datetime | None = None,
    config: AntiChurnConfig | None = None,
    cycle_id: str | None = None,
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    cfg = config or AntiChurnConfig()
    action_rows = [dict(row) for row in actions]
    if not cfg.enabled:
        return action_rows, pd.DataFrame(columns=ANTI_CHURN_REPORT_COLUMNS)

    stamp = (now or _utc_now()).astimezone(timezone.utc)
    cycle = cycle_id or stamp.strftime("%Y%m%d_%H%M%S")
    positions = {_symbol(row): dict(row) for row in (open_positions or []) if _symbol(row)}
    trades = [dict(row) for row in (trade_history or [])]
    blocked_indexes: set[int] = set()
    report: list[dict[str, Any]] = []

    by_symbol: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, action in enumerate(action_rows):
        symbol = _symbol(action)
        if symbol:
            by_symbol.setdefault(symbol, []).append((index, action))

    if cfg.block_same_cycle_open_close:
        for symbol, rows in by_symbol.items():
            has_open = any(is_open_action(action) for _, action in rows)
            has_close = any(is_close_action(action) for _, action in rows)
            if has_open and has_close:
                position = positions.get(symbol)
                age = _position_age_minutes(position, stamp)
                last_trade = find_latest_trade(symbol, trades)
                for index, action in rows:
                    blocked_indexes.add(index)
                    report.append(_report_row(symbol, _text(action.get("action") or action.get("decision") or "order"), "same_cycle_open_close", age, last_trade, _action_side(action), cycle))

    for index, action in enumerate(action_rows):
        if index in blocked_indexes:
            continue
        symbol = _symbol(action)
        if not symbol:
            continue
        attempted_side = _action_side(action)
        position = positions.get(symbol)
        age = _position_age_minutes(position, stamp)
        last_trade = find_latest_trade(symbol, trades)

        if is_close_action(action):
            reason = _close_reason(action)
            if age is not None and age < cfg.minimum_hold_minutes and reason not in cfg.allow_early_close_reasons:
                blocked_indexes.add(index)
                report.append(_report_row(symbol, "close", "minimum_hold_not_met", age, last_trade, attempted_side, cycle))
        elif is_open_action(action):
            last_at = _last_trade_at(last_trade or {})
            if last_at and (stamp - last_at).total_seconds() / 60.0 < cfg.cooldown_minutes_after_close:
                blocked_indexes.add(index)
                report.append(_report_row(symbol, "open", "reopen_cooldown_active", age, last_trade, attempted_side, cycle))
                continue
            if cfg.block_reverse_same_symbol_same_day and last_at and last_at.date() == stamp.date():
                previous_side = _last_trade_side(last_trade or {})
                if previous_side and attempted_side and _opposite(previous_side, attempted_side):
                    blocked_indexes.add(index)
                    report.append(_report_row(symbol, "open", "reverse_same_symbol_same_day", age, last_trade, attempted_side, cycle))

    allowed = [action for index, action in enumerate(action_rows) if index not in blocked_indexes]
    return allowed, pd.DataFrame(report, columns=ANTI_CHURN_REPORT_COLUMNS)


def write_anti_churn_report(report: pd.DataFrame, *, root: Path | str | None = None, stamp: str | None = None) -> Path:
    base = Path(root) if root is not None else PROJECT_ROOT
    out_dir = base / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = report.reindex(columns=ANTI_CHURN_REPORT_COLUMNS)
    path = out_dir / f"anti_churn_report_{stamp or timestamp()}.csv"
    report.to_csv(path, index=False)
    reason_aliases = {
        "minimum_hold_not_met": "minimum_hold_period_not_met",
        "reopen_cooldown_active": "cooldown_after_close_active",
        "same_cycle_open_close": "same_cycle_open_close_conflict",
        "reverse_same_symbol_same_day": "same_day_reverse_blocked",
    }
    for row in report.fillna("").to_dict("records"):
        symbol = _symbol(row)
        reason = reason_aliases.get(str(row.get("reason") or ""), str(row.get("reason") or ""))
        action = str(row.get("blocked_action") or "")
        cycle_id = str(row.get("cycle_id") or stamp or "")
        event_key = f"anti_churn:{cycle_id}:{symbol}:{action}:{reason}"
        record_event_once(
            position_id_for_symbol(symbol),
            "anti_churn_blocked",
            "anti_churn_guard",
            {
                "event_key": event_key,
                "symbol": symbol,
                "reason": reason,
                "attempted_action": action,
                "details_summary": f"{reason} {action}".strip(),
                "cycle_id": cycle_id,
                "anti_churn_report_path": str(path),
            },
            event_key=event_key,
        )
    return path


def _latest_csv(directory: Path, pattern: str) -> Path | None:
    files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def load_recent_trade_history(*, root: Path | str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
    base = Path(root) if root is not None else PROJECT_ROOT
    rows: list[dict[str, Any]] = []
    closed_path = _latest_csv(base / "data" / "trading", "closed_trades_attribution_*.csv")
    if closed_path and closed_path.exists():
        try:
            frame = pd.read_csv(closed_path, low_memory=False).tail(limit)
            for row in frame.to_dict("records"):
                rows.append(
                    {
                        "symbol": row.get("symbol"),
                        "closed_at": row.get("closed_at"),
                        "side": row.get("direction"),
                        "direction": row.get("direction"),
                        "source": "closed_trades_attribution",
                    }
                )
        except Exception:
            pass
    tracking_path = _latest_csv(base / "data" / "portal_outputs", "08_alpaca_paper_order_tracking_*.csv")
    if tracking_path and tracking_path.exists():
        try:
            frame = pd.read_csv(tracking_path, low_memory=False).tail(limit)
            terminal = frame[frame.get("alpaca_status", pd.Series(dtype=str)).astype(str).str.lower().isin({"filled", "canceled", "cancelled", "expired", "rejected"})]
            for row in terminal.to_dict("records"):
                rows.append(
                    {
                        "symbol": row.get("symbol"),
                        "closed_at": row.get("updated_at") or row.get("submitted_at"),
                        "side": row.get("side"),
                        "source": "order_tracking_terminal",
                    }
                )
        except Exception:
            pass
    return rows


def enrich_open_positions_with_order_history(open_positions: Iterable[dict[str, Any]], *, root: Path | str | None = None) -> list[dict[str, Any]]:
    base = Path(root) if root is not None else PROJECT_ROOT
    rows = [dict(row) for row in open_positions]
    if not rows:
        return rows
    tracking_path = _latest_csv(base / "data" / "portal_outputs", "08_alpaca_paper_order_tracking_*.csv")
    if not tracking_path or not tracking_path.exists():
        return rows
    try:
        tracking = pd.read_csv(tracking_path, low_memory=False)
    except Exception:
        return rows
    if tracking.empty or "symbol" not in tracking.columns:
        return rows
    tracking["__symbol"] = tracking["symbol"].astype(str).str.upper().str.strip()
    tracking["__status"] = tracking.get("alpaca_status", pd.Series("", index=tracking.index)).astype(str).str.lower()
    tracking["__time"] = pd.to_datetime(tracking.get("submitted_at"), errors="coerce", utc=True)
    filled = tracking[tracking["__status"].eq("filled")].sort_values("__time")
    opened_by_symbol = {
        str(row["__symbol"]): row.get("submitted_at")
        for row in filled.to_dict("records")
        if row.get("__symbol") and row.get("submitted_at")
    }
    out: list[dict[str, Any]] = []
    for row in rows:
        symbol = _symbol(row)
        item = dict(row)
        item.setdefault("opened_at", opened_by_symbol.get(symbol, ""))
        out.append(item)
    return out


def guard_single_close(
    symbol: str,
    *,
    position: dict[str, Any] | None = None,
    reason: str = "",
    now: datetime | None = None,
    config: AntiChurnConfig | None = None,
    cycle_id: str | None = None,
) -> tuple[bool, pd.DataFrame]:
    actions = [{"symbol": symbol, "action": "close", "side": "sell", "reason": reason}]
    allowed, report = guard_actions(actions, open_positions=[position or {"symbol": symbol}], now=now, config=config, cycle_id=cycle_id)
    return bool(allowed), report
