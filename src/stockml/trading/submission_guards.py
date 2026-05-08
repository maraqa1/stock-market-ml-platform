from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stockml.trading.alpaca_client import AlpacaPaperClient


@dataclass
class SubmissionContext:
    account: dict[str, Any] = field(default_factory=dict)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    buying_power: float = 0.0
    healthy: bool = False
    message: str = ""


def load_submission_context(client: AlpacaPaperClient) -> SubmissionContext:
    try:
        account = client.get_account()
        open_orders = client.list_orders(status="open", limit=500)
        buying_power = float(account.get("buying_power") or 0)
        status = str(account.get("status") or "").upper()
        trading_blocked = str(account.get("trading_blocked") or "").lower() == "true"
        healthy = status == "ACTIVE" and not trading_blocked
        message = "account_ok" if healthy else f"account_not_ready: status={status}, trading_blocked={trading_blocked}"
        return SubmissionContext(account=account, open_orders=open_orders, buying_power=buying_power, healthy=healthy, message=message)
    except Exception as exc:
        return SubmissionContext(healthy=False, message=f"account_check_failed: {exc}")


def validate_order(order: dict, client: AlpacaPaperClient, context: SubmissionContext, seen_client_ids: set[str]) -> tuple[bool, str]:
    if not context.healthy:
        return False, context.message

    symbol = str(order.get("symbol") or "").upper()
    client_order_id = str(order.get("client_order_id") or "").strip()
    notional = float(order.get("notional") or 0)
    qty = int(float(order.get("suggested_quantity") or order.get("qty") or 0))
    if not symbol:
        return False, "missing_symbol"
    if not client_order_id:
        return False, "missing_client_order_id"
    if client_order_id in seen_client_ids:
        return False, "duplicate_client_order_id_in_run"
    open_client_ids = {str(row.get("client_order_id") or "") for row in context.open_orders}
    open_symbols = {str(row.get("symbol") or "").upper() for row in context.open_orders}
    if client_order_id in open_client_ids:
        return False, "duplicate_open_client_order_id"
    if symbol in open_symbols:
        return False, "symbol_already_has_open_order"
    if notional <= 0:
        return False, "invalid_notional"
    if qty < 1:
        return False, "invalid_quantity"
    if notional > context.buying_power:
        return False, "insufficient_buying_power"

    try:
        asset = client.get_asset(symbol)
    except Exception as exc:
        return False, f"asset_check_failed: {exc}"
    if not asset:
        return False, "asset_not_found"
    if not bool(asset.get("tradable")):
        return False, "asset_not_tradable"
    if str(asset.get("status") or "").lower() not in {"active", ""}:
        return False, f"asset_status_{asset.get('status')}"
    if order.get("side") == "sell" and not bool(asset.get("shortable", True)):
        return False, "asset_not_shortable"

    seen_client_ids.add(client_order_id)
    return True, "submission_preflight_passed"
