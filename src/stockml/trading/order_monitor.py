from __future__ import annotations

from typing import Any


class OrderMonitor:
    def __init__(self, client) -> None:
        self.client = client

    def list_open_orders(self) -> list[dict[str, Any]]:
        return self.client.list_orders(status="open", limit=500)

    def get_positions(self) -> list[dict[str, Any]]:
        return self.client.list_positions()

    def get_buying_power(self) -> float:
        account = self.client.get_account()
        return float(account.get("buying_power") or 0)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        if hasattr(self.client, "cancel_order"):
            return self.client.cancel_order(order_id)
        raise NotImplementedError("cancel_order is not available for this client")

    def cancel_all_open_orders(self) -> list[dict[str, Any]]:
        if hasattr(self.client, "cancel_all_orders"):
            return self.client.cancel_all_orders()
        cancelled = []
        for order in self.list_open_orders():
            order_id = str(order.get("id") or "")
            if order_id:
                cancelled.append(self.cancel_order(order_id))
        return cancelled

    def has_duplicate_open_order(self, symbol: str, side: str) -> bool:
        return any(str(order.get("symbol") or "").upper() == symbol.upper() and str(order.get("side") or "").lower() == side.lower() for order in self.list_open_orders())

    def reconcile_positions(self, expected: list[dict[str, Any]]) -> list[dict[str, Any]]:
        actual = {str(row.get("symbol") or "").upper(): row for row in self.get_positions()}
        rows = []
        for row in expected:
            symbol = str(row.get("symbol") or "").upper()
            rows.append({"symbol": symbol, "expected": row, "actual": actual.get(symbol, {}), "matched": symbol in actual})
        return rows
