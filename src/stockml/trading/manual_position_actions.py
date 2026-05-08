from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import OPERATOR_ACTIONS_DIR, ensure_data_dirs
from stockml.trading.alpaca_client import AlpacaAPIError, AlpacaPaperClient
from stockml.trading.config import AlpacaConfig, alpaca_config


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
        result["message"] = "operator_keep_position"
    else:
        cfg = config or alpaca_config()
        if cfg.live_trading_enabled:
            result["message"] = "live_trading_disabled_for_manual_close"
        elif not cfg.paper_trading_enabled:
            result["message"] = "paper_trading_disabled"
        elif not cfg.submit_orders:
            result["status"] = "dry_run"
            result["message"] = "manual_close_dry_run_submit_orders_disabled"
        elif not cfg.api_key or not cfg.secret_key:
            result["message"] = "alpaca_credentials_missing"
        else:
            try:
                response = (client or AlpacaPaperClient(cfg)).close_position(clean_symbol)
                result["status"] = "submitted"
                result["message"] = "manual_close_submitted"
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
    result["action_path"] = str(path)
    return result

