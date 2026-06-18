from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.services.events import position_id_for_symbol, record_event_once
from stockml.trading.order_intent import CLOSE_OR_REDUCE_INTENTS, REVERSAL_INTENTS, OrderIntent, derive_order_intent, normalize_order_side

POSITION_INTENT_REPORT_COLUMNS = [
    "cycle_id",
    "symbol",
    "attempted_side",
    "attempted_qty",
    "current_position_qty",
    "current_position_side",
    "current_avg_entry_price",
    "position_opened_at",
    "position_age_minutes",
    "derived_intent",
    "decision",
    "block_reason",
    "session_mode",
    "extended_hours",
    "order_source",
]

DEFAULT_ALLOWED_EARLY_CLOSE_REASONS = {
    "hard_stop_hit",
    "take_profit_hit",
    "manual_kill",
    "broker_error_correction",
    "duplicate_order_correction",
    "emergency_risk_breach",
}


@dataclass(frozen=True)
class PositionIntentConfig:
    enabled: bool = True
    minimum_hold_minutes: int = 30
    allow_same_day_reversal: bool = False
    allow_24x5_reversal: bool = False
    allow_short_selling: bool = True
    allow_early_close_reasons: set[str] = field(default_factory=lambda: set(DEFAULT_ALLOWED_EARLY_CLOSE_REASONS))


@dataclass(frozen=True)
class PositionIntentDecision:
    allowed: bool
    intent: OrderIntent
    block_reason: str = ""
    position: dict[str, Any] | None = None
    position_state_available: bool = True
    position_age_minutes: float | None = None

    @property
    def decision(self) -> str:
        return "allowed" if self.allowed else "blocked"


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


def _now(value: datetime | None = None) -> datetime:
    out = value or datetime.now(timezone.utc)
    if out.tzinfo is None:
        return out.replace(tzinfo=timezone.utc)
    return out.astimezone(timezone.utc)


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _symbol(value: Any) -> str:
    return _text(value).upper()


def _position_symbol(row: dict[str, Any]) -> str:
    return _symbol(row.get("symbol") or row.get("ticker"))


def _position_qty(row: dict[str, Any] | None) -> float:
    return _float((row or {}).get("qty") or (row or {}).get("quantity") or (row or {}).get("current_position_qty"), 0.0)


def _avg_entry(row: dict[str, Any] | None) -> float:
    return _float((row or {}).get("avg_entry_price") or (row or {}).get("average_entry_price") or (row or {}).get("entry") or (row or {}).get("current_avg_entry_price"), 0.0)


def _opened_at(row: dict[str, Any] | None) -> datetime | None:
    if not row:
        return None
    for key in ("opened_at", "position_opened_at", "entry_time", "filled_at", "submitted_at", "created_at", "updated_at"):
        dt = _aware(row.get(key))
        if dt:
            return dt
    return None


def _age_minutes(position: dict[str, Any] | None, now: datetime) -> float | None:
    if not position:
        return None
    for key in ("position_age_minutes", "age_minutes"):
        if position.get(key) not in (None, ""):
            return max(0.0, _float(position.get(key), 0.0))
    opened = _opened_at(position)
    if not opened:
        return None
    return max(0.0, (now - opened).total_seconds() / 60.0)


def _latest_tracking_opened_at(symbol: str, side: str, *, root: Path | str | None = None) -> datetime | None:
    base = Path(root) if root is not None else PROJECT_ROOT
    files = sorted((base / "data" / "portal_outputs").glob("08_alpaca_paper_order_tracking_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    expected_order_side = "buy" if side == "long" else "sell" if side == "short" else ""
    for path in files[:20]:
        try:
            frame = pd.read_csv(path, low_memory=False)
        except Exception:
            continue
        if frame.empty or "symbol" not in frame.columns:
            continue
        rows = frame[frame["symbol"].astype(str).str.upper().eq(symbol)]
        if "alpaca_status" in rows.columns:
            rows = rows[rows["alpaca_status"].astype(str).str.lower().eq("filled")]
        if expected_order_side and "side" in rows.columns:
            rows = rows[rows["side"].astype(str).str.lower().eq(expected_order_side)]
        if rows.empty:
            continue
        for key in ("filled_at", "updated_at", "submitted_at"):
            if key in rows.columns:
                times = pd.to_datetime(rows[key], errors="coerce", utc=True).dropna().sort_values(ascending=False)
                if not times.empty:
                    return times.iloc[0].to_pydatetime()
    return None


def load_current_positions(client: Any | None = None, *, positions: Iterable[dict[str, Any]] | None = None, root: Path | str | None = None) -> tuple[dict[str, dict[str, Any]], bool]:
    if positions is not None:
        rows = [dict(row) for row in positions]
        available = True
    elif client is not None and hasattr(client, "list_positions"):
        try:
            rows = [dict(row) for row in client.list_positions()]
            available = True
        except Exception:
            rows = []
            available = False
    else:
        rows = []
        available = True

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = _position_symbol(row)
        if not symbol:
            continue
        qty = _position_qty(row)
        side = "long" if qty > 0 else "short" if qty < 0 else _text(row.get("side")).lower()
        item = dict(row)
        item["symbol"] = symbol
        item["qty"] = qty
        item["current_position_side"] = side or "none"
        if not _opened_at(item):
            opened = _latest_tracking_opened_at(symbol, side, root=root)
            if opened:
                item["opened_at"] = opened.isoformat()
        out[symbol] = item
    return out, available


def evaluate_position_intent(
    *,
    symbol: str,
    attempted_side: str,
    attempted_qty: Any,
    position: dict[str, Any] | None = None,
    position_state_available: bool = True,
    close_reason: str = "",
    session_mode: str = "regular",
    config: PositionIntentConfig | None = None,
    now: datetime | None = None,
) -> PositionIntentDecision:
    cfg = config or PositionIntentConfig()
    stamp = _now(now)
    order_side = normalize_order_side(attempted_side)
    qty = _position_qty(position)
    intent = derive_order_intent(current_qty=qty, attempted_side=order_side, attempted_qty=attempted_qty)
    if not cfg.enabled:
        return PositionIntentDecision(True, intent, position=position, position_state_available=position_state_available, position_age_minutes=_age_minutes(position, stamp))

    reason = _text(close_reason).lower()
    age = _age_minutes(position, stamp)

    if not position_state_available and order_side in {"buy", "sell"}:
        # Without position state, an opposite-side order can accidentally close or reverse. Block the paper submit path.
        return PositionIntentDecision(False, intent, "position_state_unavailable_for_opposite_side_order", position, False, age)

    if intent.intent == "open_short" and not cfg.allow_short_selling:
        return PositionIntentDecision(False, intent, "short_selling_disabled", position, position_state_available, age)

    if intent.intent in REVERSAL_INTENTS:
        if session_mode == "24x5" and not cfg.allow_24x5_reversal:
            return PositionIntentDecision(False, intent, "24x5_reversal_blocked", position, position_state_available, age)
        if not cfg.allow_same_day_reversal:
            return PositionIntentDecision(False, intent, "same_day_reversal_blocked", position, position_state_available, age)

    if intent.intent in CLOSE_OR_REDUCE_INTENTS and age is not None and age < cfg.minimum_hold_minutes and reason not in cfg.allow_early_close_reasons:
        return PositionIntentDecision(False, intent, "minimum_hold_period_not_met", position, position_state_available, age)

    return PositionIntentDecision(True, intent, position=position, position_state_available=position_state_available, position_age_minutes=age)


def report_row(
    decision: PositionIntentDecision,
    *,
    cycle_id: str,
    symbol: str,
    attempted_side: str,
    attempted_qty: Any,
    session_mode: str,
    extended_hours: bool,
    order_source: str,
) -> dict[str, Any]:
    position = decision.position or {}
    opened = _opened_at(position)
    return {
        "cycle_id": cycle_id,
        "symbol": _symbol(symbol),
        "attempted_side": normalize_order_side(attempted_side),
        "attempted_qty": decision.intent.attempted_qty,
        "current_position_qty": decision.intent.current_qty,
        "current_position_side": decision.intent.current_side,
        "current_avg_entry_price": _avg_entry(position),
        "position_opened_at": opened.isoformat() if opened else "",
        "position_age_minutes": round(decision.position_age_minutes, 2) if decision.position_age_minutes is not None else "",
        "derived_intent": decision.intent.intent,
        "decision": decision.decision,
        "block_reason": decision.block_reason,
        "session_mode": session_mode,
        "extended_hours": bool(extended_hours),
        "order_source": order_source,
    }


def write_position_intent_report(rows: Iterable[dict[str, Any]], *, root: Path | str | None = None, stamp: str | None = None) -> Path:
    base = Path(root) if root is not None else PROJECT_ROOT
    out_dir = base / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"position_intent_guard_{stamp or timestamp()}.csv"
    pd.DataFrame(list(rows), columns=POSITION_INTENT_REPORT_COLUMNS).to_csv(path, index=False)
    return path


def record_position_intent_block(row: dict[str, Any], *, report_path: Path | str | None = None) -> None:
    symbol = _symbol(row.get("symbol"))
    intent = _text(row.get("derived_intent"))
    reason = _text(row.get("block_reason"))
    side = normalize_order_side(row.get("attempted_side"))
    event_key = f"position_intent:{row.get('cycle_id','')}:{symbol}:{side}:{intent}:{reason}"
    record_event_once(
        position_id_for_symbol(symbol),
        "position_intent_blocked",
        "position_intent_guard",
        {
            "event_key": event_key,
            "symbol": symbol,
            "attempted_side": side,
            "derived_intent": intent,
            "block_reason": reason,
            "cycle_id": row.get("cycle_id", ""),
            "session_mode": row.get("session_mode", ""),
            "extended_hours": row.get("extended_hours", ""),
            "order_source": row.get("order_source", ""),
            "position_intent_report_path": str(report_path or ""),
            "details_summary": f"{symbol} {side} {intent} {reason}".strip(),
        },
        event_key=event_key,
    )


def guard_order_submission(
    order: dict[str, Any],
    *,
    client: Any | None = None,
    positions: Iterable[dict[str, Any]] | None = None,
    config: PositionIntentConfig | None = None,
    now: datetime | None = None,
    cycle_id: str | None = None,
    order_source: str = "paper_trader",
    close_reason: str = "",
    root: Path | str | None = None,
) -> tuple[PositionIntentDecision, dict[str, Any]]:
    symbol = _symbol(order.get("symbol"))
    attempted_side = normalize_order_side(order.get("side"))
    attempted_qty = order.get("qty") or order.get("suggested_quantity") or order.get("quantity") or 0
    extended = bool(order.get("extended_hours", False))
    session_mode = "24x5" if extended else "regular"
    position_map, available = load_current_positions(client, positions=positions, root=root)
    position = position_map.get(symbol)
    decision = evaluate_position_intent(
        symbol=symbol,
        attempted_side=attempted_side,
        attempted_qty=attempted_qty,
        position=position,
        position_state_available=available,
        close_reason=close_reason or str(order.get("close_reason") or order.get("reason") or order.get("decision_reason") or ""),
        session_mode=session_mode,
        config=config,
        now=now,
    )
    row = report_row(
        decision,
        cycle_id=cycle_id or timestamp(),
        symbol=symbol,
        attempted_side=attempted_side,
        attempted_qty=attempted_qty,
        session_mode=session_mode,
        extended_hours=extended,
        order_source=order_source,
    )
    return decision, row
