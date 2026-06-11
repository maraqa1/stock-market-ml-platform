from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import TRADING_DIR, timestamp
from stockml.trading.alpaca_client import AlpacaAPIError, AlpacaPaperClient
from stockml.trading.config import AlpacaConfig, alpaca_config


ACTIVE_ORDER_STATUSES = {"new", "accepted", "pending_new", "pending_replace", "partially_filled", "submitted"}
ENTRY_ORDER_PREFIX = "stockml-"
CLOSE_ORDER_PREFIX = "stockml-close-"
CLIENT_ORDER_TIMESTAMP_RE = re.compile(r"-(\d{14})$")
OUTPUT_COLUMNS = [
    "checked_at",
    "symbol",
    "side",
    "order_id",
    "client_order_id",
    "status",
    "submitted_at",
    "age_minutes",
    "filled_qty",
    "limit_price",
    "action",
    "reason",
]


def cancel_stale_entry_orders(
    *,
    config: AlpacaConfig | None = None,
    client: AlpacaPaperClient | None = None,
    now: datetime | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Cancel old unfilled StockML entry orders so the basket can refresh.

    This deliberately does not touch close orders, partially-filled orders, or
    non-StockML orders. It is paper-only and refuses to run with live trading.
    """
    cfg = config or alpaca_config()
    result: dict[str, Any] = {
        "stale_entry_status": "skipped",
        "stale_entry_open_orders": 0,
        "stale_entry_candidates": 0,
        "stale_entry_canceled": 0,
        "stale_entry_skipped": 0,
        "stale_entry_error": 0,
        "stale_entry_notes": "",
        "stale_entry_path": "",
    }
    if not cfg.stale_entry_cancel_enabled:
        result["stale_entry_notes"] = "stale_entry_cancel_disabled"
        return result
    if cfg.live_trading_enabled:
        result["stale_entry_notes"] = "live_trading_disabled_for_stale_entry_cancel"
        return result
    if not cfg.paper_trading_enabled:
        result["stale_entry_notes"] = "paper_trading_disabled"
        return result
    if not cfg.submit_orders:
        result["stale_entry_notes"] = "submit_orders_disabled"
        return result
    if not cfg.api_key or not cfg.secret_key:
        result["stale_entry_notes"] = "alpaca_credentials_missing"
        return result

    clock = _aware(now)
    alpaca_client = client or AlpacaPaperClient(cfg)
    try:
        open_orders = alpaca_client.list_orders(status="open", limit=500)
    except Exception as exc:
        result["stale_entry_status"] = "error"
        result["stale_entry_error"] = 1
        result["stale_entry_notes"] = f"stale_entry_load_error:{exc}"
        return result

    result["stale_entry_open_orders"] = len(open_orders)
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    for order in open_orders:
        row = _audit_row(order, clock)
        if not _is_stockml_entry_order(order):
            row["action"] = "skipped"
            row["reason"] = "not_stockml_entry_order"
            result["stale_entry_skipped"] += 1
            rows.append(row)
            continue
        result["stale_entry_candidates"] += 1
        if _float_value(order.get("filled_qty"), 0.0) > 0:
            row["action"] = "skipped"
            row["reason"] = "partially_filled"
            result["stale_entry_skipped"] += 1
            notes.append(f"{row['symbol']}:partially_filled")
            rows.append(row)
            continue
        if row["age_minutes"] < cfg.stale_entry_cancel_after_minutes:
            row["action"] = "skipped"
            row["reason"] = "too_young"
            result["stale_entry_skipped"] += 1
            rows.append(row)
            continue
        order_id = str(order.get("id") or "").strip()
        if not order_id:
            row["action"] = "skipped"
            row["reason"] = "order_id_missing"
            result["stale_entry_skipped"] += 1
            notes.append(f"{row['symbol']}:order_id_missing")
            rows.append(row)
            continue
        try:
            alpaca_client.cancel_order(order_id)
            row["action"] = "canceled"
            row["reason"] = "stale_unfilled_entry_order"
            result["stale_entry_canceled"] += 1
            notes.append(f"{row['symbol']}:canceled:{round(row['age_minutes'], 1)}m")
        except AlpacaAPIError as exc:
            row["action"] = "error"
            details = exc.as_dict()
            row["reason"] = str(details.get("api_message") or details.get("api_error") or "alpaca_api_error")
            result["stale_entry_error"] += 1
            notes.append(f"{row['symbol']}:alpaca_api_error")
        except Exception as exc:
            row["action"] = "error"
            row["reason"] = str(exc)
            result["stale_entry_error"] += 1
            notes.append(f"{row['symbol']}:error")
        rows.append(row)

    result["stale_entry_status"] = "error" if result["stale_entry_error"] else "ok"
    result["stale_entry_notes"] = "; ".join(notes[:20])
    if rows:
        output = output_path or _output_path(clock)
        output.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=OUTPUT_COLUMNS).to_csv(output, index=False)
        result["stale_entry_path"] = str(output)
    return result


def _output_path(clock: datetime) -> Path:
    return TRADING_DIR / "stale_entry_orders" / f"stale_entry_orders_{timestamp()}.csv"


def _aware(value: datetime | None) -> datetime:
    out = value or datetime.now(timezone.utc)
    if out.tzinfo is None:
        return out.replace(tzinfo=timezone.utc)
    return out.astimezone(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_minutes(order: dict[str, Any], now: datetime) -> float:
    submitted = _submitted_time(order)
    if submitted is None:
        return 0.0
    return max(0.0, (now - submitted).total_seconds() / 60.0)


def _submitted_time(order: dict[str, Any]) -> datetime | None:
    parsed = _parse_time(order.get("submitted_at") or order.get("created_at"))
    if parsed is not None:
        return parsed
    match = CLIENT_ORDER_TIMESTAMP_RE.search(str(order.get("client_order_id") or "").strip())
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _float_value(value: Any, default: float = 0.0) -> float:
    if value in [None, ""]:
        return default
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _is_stockml_entry_order(order: dict[str, Any]) -> bool:
    client_order_id = str(order.get("client_order_id") or "").strip()
    status = str(order.get("status") or "").strip().lower()
    if not client_order_id.startswith(ENTRY_ORDER_PREFIX):
        return False
    if client_order_id.startswith(CLOSE_ORDER_PREFIX):
        return False
    return status in ACTIVE_ORDER_STATUSES


def _audit_row(order: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "checked_at": now.isoformat(timespec="seconds"),
        "symbol": str(order.get("symbol") or "").upper(),
        "side": str(order.get("side") or "").lower(),
        "order_id": str(order.get("id") or ""),
        "client_order_id": str(order.get("client_order_id") or ""),
        "status": str(order.get("status") or ""),
        "submitted_at": str(order.get("submitted_at") or order.get("created_at") or ""),
        "age_minutes": round(_age_minutes(order, now), 4),
        "filled_qty": _float_value(order.get("filled_qty"), 0.0),
        "limit_price": _float_value(order.get("limit_price"), 0.0),
        "action": "",
        "reason": "",
    }
