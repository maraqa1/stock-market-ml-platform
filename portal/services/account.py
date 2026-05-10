from __future__ import annotations

from typing import Any

from stockml.trading.alpaca_client import AlpacaPaperClient
from stockml.trading.config import AlpacaConfig, alpaca_config


def _money(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except Exception:
        return None


def account_snapshot(config: AlpacaConfig | None = None, client: AlpacaPaperClient | None = None) -> dict[str, Any]:
    cfg = config or alpaca_config()
    fallback = {
        "source": "config",
        "equity": float(cfg.account_equity),
        "cash": None,
        "buying_power": None,
        "portfolio_value": None,
        "account_id": "",
        "status": "configured",
        "error": "",
    }
    if not cfg.api_key or not cfg.secret_key:
        return {**fallback, "error": "alpaca_credentials_missing"}
    try:
        data = (client or AlpacaPaperClient(cfg)).get_account()
    except Exception as exc:
        return {**fallback, "error": str(exc)}

    equity = _money(data.get("equity")) or _money(data.get("portfolio_value")) or fallback["equity"]
    return {
        "source": "alpaca",
        "equity": equity,
        "cash": _money(data.get("cash")),
        "buying_power": _money(data.get("buying_power")),
        "portfolio_value": _money(data.get("portfolio_value")),
        "account_id": data.get("account_number") or data.get("id") or "",
        "status": data.get("status") or "",
        "error": "",
    }
