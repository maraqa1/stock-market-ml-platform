from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import OPERATOR_ACTIONS_DIR, ensure_data_dirs
from stockml.services.events import position_id_for_symbol, record_event_safely
from stockml.trading.activity_journal import enrich_exit_activity_details, enrich_monitor_activity_details
from stockml.trading.alpaca_client import AlpacaAPIError, AlpacaPaperClient
from stockml.trading.config import AlpacaConfig, alpaca_config
from stockml.trading.order_builder import extended_limit_price
from stockml.trading.submission_guards import asset_is_overnight_tradable


ACTION_COLUMNS = [
    "timestamp",
    "symbol",
    "operator_action",
    "status",
    "message",
    "order_id",
    "client_order_id",
    "alpaca_status",
    "http_status",
    "request_id",
    "api_error",
]


def _today_actions_path() -> Path:
    ensure_data_dirs()
    OPERATOR_ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
    return OPERATOR_ACTIONS_DIR / f"operator_position_actions_{datetime.now().strftime('%Y%m%d')}.csv"


def _append_action(row: dict[str, Any], path: Path | None = None) -> Path:
    output = path or _today_actions_path()
    frame = pd.DataFrame([{column: row.get(column, "") for column in ACTION_COLUMNS}])
    if output.exists():
        frame.to_csv(output, mode="a", index=False, header=False)
    else:
        frame.to_csv(output, index=False)
    return output


def _base_result(symbol: str, action: str) -> dict[str, Any]:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "symbol": symbol.upper(),
        "operator_action": action,
        "status": "rejected",
        "message": "",
        "order_id": "",
        "client_order_id": "",
        "alpaca_status": "",
        "http_status": "",
        "request_id": "",
        "api_error": "",
    }


def _number(value: Any, default: float = 0.0) -> float:
    if value in [None, ""]:
        return default
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _qty_text(quantity: float) -> str:
    if float(quantity).is_integer():
        return str(int(quantity))
    return f"{quantity:.6f}".rstrip("0").rstrip(".")


def _position_for_symbol(client: AlpacaPaperClient, symbol: str) -> dict[str, Any] | None:
    for position in client.list_positions():
        if str(position.get("symbol") or "").upper() == symbol:
            return position
    return None


def _overnight_limit_close_order(
    symbol: str,
    *,
    cfg: AlpacaConfig,
    client: AlpacaPaperClient,
) -> tuple[dict[str, Any] | None, str]:
    if not cfg.overnight_trading_enabled:
        return None, "overnight_close_disabled"

    position = _position_for_symbol(client, symbol)
    if not position:
        return None, "position_not_found"

    asset = client.get_asset(symbol)
    if not asset_is_overnight_tradable(asset):
        return None, "asset_not_overnight_tradable"

    raw_qty = _number(position.get("qty"), 0.0)
    quantity = abs(raw_qty)
    if quantity <= 0:
        return None, "position_quantity_zero"

    side = "sell" if raw_qty > 0 else "buy"
    price = _number(position.get("current_price"), 0.0)
    if price <= 0:
        price = _number(position.get("avg_entry_price"), 0.0)
    limit_price = extended_limit_price(pd.Series({"current_price": price}), side, cfg.overnight_limit_buffer_bps)
    if limit_price is None:
        return None, "overnight_close_limit_price_missing"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:17]
    order = {
        "symbol": symbol,
        "qty": _qty_text(quantity),
        "side": side,
        "type": "limit",
        "time_in_force": "day",
        "extended_hours": True,
        "limit_price": limit_price,
        "client_order_id": f"stockml-close-{stamp}-{symbol}"[:48],
    }
    return client.submit_order(order), "close_submitted_overnight_limit"


def apply_manual_position_action(
    symbol: str,
    action: str,
    *,
    config: AlpacaConfig | None = None,
    client: AlpacaPaperClient | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Record or execute a manual paper-position action from the portal.

    `keep` never calls Alpaca. `close` only submits to Alpaca paper when paper
    submission is explicitly enabled and live trading remains disabled.
    """
    clean_symbol = str(symbol or "").strip().upper()
    clean_action = str(action or "").strip().lower()
    result = _base_result(clean_symbol, clean_action)

    if not clean_symbol:
        result["message"] = "symbol_required"
    elif clean_action not in {"keep", "close"}:
        result["message"] = "unsupported_operator_action"
    elif clean_action == "keep":
        result["status"] = "recorded"
        result["message"] = "keep_recorded"
    else:
        cfg = config or alpaca_config()
        if cfg.live_trading_enabled:
            result["message"] = "close_blocked_live_trading_disabled"
        elif not cfg.paper_trading_enabled:
            result["message"] = "paper_trading_disabled"
        elif not cfg.submit_orders:
            result["status"] = "dry_run"
            result["message"] = "close_blocked_submit_disabled"
        elif not cfg.api_key or not cfg.secret_key:
            result["message"] = "alpaca_credentials_missing"
        else:
            try:
                alpaca_client = client or AlpacaPaperClient(cfg)
                response, submitted_message = _overnight_limit_close_order(clean_symbol, cfg=cfg, client=alpaca_client)
                if response is None:
                    response = alpaca_client.close_position(clean_symbol)
                    submitted_message = "close_submitted_regular"
                result["status"] = "submitted"
                result["message"] = submitted_message
                result["order_id"] = response.get("id", "")
                result["client_order_id"] = response.get("client_order_id", "")
                result["alpaca_status"] = response.get("status", "")
            except AlpacaAPIError as exc:
                result.update(exc.as_dict())
                result["status"] = "error"
                result["message"] = "manual_close_alpaca_api_error"
            except Exception as exc:
                result["status"] = "error"
                result["message"] = f"manual_close_error: {exc}"

    path = _append_action(result, output_path)
    if clean_symbol:
        event_type = "operator_keep" if clean_action == "keep" else "operator_close"
        if clean_action in {"keep", "close"}:
            record_event_safely(
                position_id_for_symbol(clean_symbol),
                event_type,
                "manual_position_actions",
                (
                    enrich_monitor_activity_details(clean_symbol, {
                        "symbol": clean_symbol,
                        "operator_action": clean_action,
                        "status": result.get("status"),
                        "final_outcome": result.get("message"),
                        "order_id": result.get("order_id"),
                        "client_order_id": result.get("client_order_id"),
                        "alpaca_status": result.get("alpaca_status"),
                        "action_path": str(path),
                    })
                    if clean_action == "keep"
                    else enrich_exit_activity_details(clean_symbol, {
                        "symbol": clean_symbol,
                        "operator_action": clean_action,
                        "status": result.get("status"),
                        "final_outcome": result.get("message"),
                        "order_id": result.get("order_id"),
                        "broker_order_id": result.get("order_id"),
                        "client_order_id": result.get("client_order_id"),
                        "alpaca_status": result.get("alpaca_status"),
                        "action_path": str(path),
                    }, reason="manual_close")
                ),
            )
    result["action_path"] = str(path)
    return result
