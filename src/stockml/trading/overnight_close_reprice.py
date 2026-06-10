from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.trading.alpaca_client import AlpacaAPIError, AlpacaPaperClient
from stockml.trading.config import AlpacaConfig, alpaca_config
from stockml.trading.manual_position_actions import apply_manual_position_action


ACTIVE_ORDER_STATUSES = {"new", "accepted", "pending_new", "pending_replace", "partially_filled"}
CLOSE_ORDER_PREFIX = "stockml-close-"


def _bool_value(value: Any) -> bool:
    if value in [None, ""]:
        return False
    try:
        if pd.isna(value):
            return False
    except Exception:
        pass
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _float_value(value: Any, default: float = 0.0) -> float:
    if value in [None, ""]:
        return default
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


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


def _order_age_minutes(order: dict[str, Any], now: datetime) -> float:
    submitted = _parse_time(order.get("submitted_at") or order.get("created_at"))
    if submitted is None:
        return 0.0
    return max(0.0, (now - submitted).total_seconds() / 60.0)


def _position_map(client: AlpacaPaperClient) -> dict[str, dict[str, Any]]:
    positions: dict[str, dict[str, Any]] = {}
    for row in client.list_positions():
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol:
            positions[symbol] = row
    return positions


def _closing_side(position: dict[str, Any]) -> str | None:
    qty = _float_value(position.get("qty"), 0.0)
    if qty > 0:
        return "sell"
    if qty < 0:
        return "buy"
    return None


def _current_price(position: dict[str, Any]) -> float:
    price = _float_value(position.get("current_price"), 0.0)
    if price <= 0:
        price = _float_value(position.get("avg_entry_price"), 0.0)
    return price


def _current_buffer_bps(order: dict[str, Any], position: dict[str, Any], side: str) -> float:
    price = _current_price(position)
    limit_price = _float_value(order.get("limit_price"), 0.0)
    if price <= 0 or limit_price <= 0:
        return 0.0
    if side == "sell":
        return max(0.0, (1.0 - (limit_price / price)) * 10000.0)
    return max(0.0, ((limit_price / price) - 1.0) * 10000.0)


def _is_stockml_close_order(order: dict[str, Any]) -> bool:
    client_order_id = str(order.get("client_order_id") or "").strip()
    status = str(order.get("status") or "").strip().lower()
    order_type = str(order.get("type") or order.get("order_type") or "").strip().lower()
    return (
        client_order_id.startswith(CLOSE_ORDER_PREFIX)
        and status in ACTIVE_ORDER_STATUSES
        and order_type == "limit"
        and _bool_value(order.get("extended_hours"))
    )


def reprice_stale_overnight_close_orders(
    *,
    config: AlpacaConfig | None = None,
    client: AlpacaPaperClient | None = None,
    now: datetime | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Cancel-replace stale 24/5 close orders with a wider capped limit.

    This intentionally touches only StockML close orders for currently held
    positions. It never opens exposure and refuses to run when live trading is
    enabled.
    """
    cfg = config or alpaca_config()
    result: dict[str, Any] = {
        "overnight_reprice_status": "skipped",
        "overnight_reprice_candidates": 0,
        "overnight_reprice_attempted": 0,
        "overnight_reprice_canceled": 0,
        "overnight_reprice_submitted": 0,
        "overnight_reprice_skipped": 0,
        "overnight_reprice_error": 0,
        "overnight_reprice_notes": "",
    }
    notes: list[str] = []

    if not cfg.overnight_trading_enabled:
        result["overnight_reprice_notes"] = "overnight_trading_disabled"
        return result
    if cfg.live_trading_enabled:
        result["overnight_reprice_notes"] = "live_trading_disabled_for_overnight_reprice"
        return result
    if not cfg.submit_orders:
        result["overnight_reprice_notes"] = "submit_orders_disabled"
        return result
    if not cfg.api_key or not cfg.secret_key:
        result["overnight_reprice_notes"] = "alpaca_credentials_missing"
        return result

    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    alpaca_client = client or AlpacaPaperClient(cfg)
    try:
        positions = _position_map(alpaca_client)
        orders = alpaca_client.list_orders(status="open", limit=500)
    except Exception as exc:
        result["overnight_reprice_status"] = "error"
        result["overnight_reprice_error"] = 1
        result["overnight_reprice_notes"] = f"overnight_reprice_load_error:{exc}"
        return result

    close_orders = [order for order in orders if _is_stockml_close_order(order)]
    result["overnight_reprice_candidates"] = len(close_orders)
    if not close_orders:
        result["overnight_reprice_status"] = "ok"
        return result

    for order in close_orders:
        symbol = str(order.get("symbol") or "").strip().upper()
        position = positions.get(symbol)
        if not position:
            result["overnight_reprice_skipped"] += 1
            notes.append(f"{symbol}:position_not_found")
            continue

        expected_side = _closing_side(position)
        order_side = str(order.get("side") or "").strip().lower()
        if expected_side is None or order_side != expected_side:
            result["overnight_reprice_skipped"] += 1
            notes.append(f"{symbol}:side_mismatch")
            continue

        age_minutes = _order_age_minutes(order, clock)
        if age_minutes < cfg.overnight_close_reprice_after_minutes:
            result["overnight_reprice_skipped"] += 1
            notes.append(f"{symbol}:too_young")
            continue

        current_buffer = _current_buffer_bps(order, position, expected_side)
        if current_buffer >= cfg.overnight_close_reprice_max_buffer_bps:
            result["overnight_reprice_skipped"] += 1
            notes.append(f"{symbol}:max_buffer_reached")
            continue

        next_buffer = min(
            cfg.overnight_close_reprice_max_buffer_bps,
            max(cfg.overnight_limit_buffer_bps, current_buffer + cfg.overnight_close_reprice_step_bps),
        )
        order_id = str(order.get("id") or "").strip()
        if not order_id:
            result["overnight_reprice_skipped"] += 1
            notes.append(f"{symbol}:order_id_missing")
            continue

        result["overnight_reprice_attempted"] += 1
        try:
            alpaca_client.cancel_order(order_id)
            result["overnight_reprice_canceled"] += 1
            updated_cfg = replace(cfg, overnight_limit_buffer_bps=next_buffer)
            submitted = apply_manual_position_action(symbol, "close", config=updated_cfg, client=alpaca_client, output_path=output_path)
            if submitted.get("status") == "submitted":
                result["overnight_reprice_submitted"] += 1
                notes.append(f"{symbol}:repriced_to_{round(next_buffer, 2)}bps")
            else:
                result["overnight_reprice_error"] += 1
                notes.append(f"{symbol}:resubmit_{submitted.get('status')}:{submitted.get('message')}")
        except AlpacaAPIError as exc:
            result["overnight_reprice_error"] += 1
            details = exc.as_dict()
            notes.append(f"{symbol}:alpaca_api_error:{details.get('api_message') or details.get('api_error')}")
        except Exception as exc:
            result["overnight_reprice_error"] += 1
            notes.append(f"{symbol}:error:{exc}")

    result["overnight_reprice_status"] = "error" if result["overnight_reprice_error"] else "ok"
    result["overnight_reprice_notes"] = "; ".join(notes[:20])
    return result
