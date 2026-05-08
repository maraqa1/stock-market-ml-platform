from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import requests

from stockml.trading.config import AlpacaConfig


@dataclass
class AlpacaPaperClient:
    config: AlpacaConfig
    timeout_seconds: int = 20

    def _headers(self) -> dict[str, str]:
        if not self.config.api_key or not self.config.secret_key:
            raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY are required for Alpaca API calls.")
        return {
            "APCA-API-KEY-ID": self.config.api_key,
            "APCA-API-SECRET-KEY": self.config.secret_key,
            "Content-Type": "application/json",
        }

    def get_account(self) -> dict[str, Any]:
        response = requests.get(
            f"{self.config.base_url}/v2/account",
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(
            f"{self.config.base_url}/v2/orders",
            headers=self._headers(),
            json=order,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def list_orders(self, status: str = "open", limit: int = 100) -> list[dict[str, Any]]:
        response = requests.get(
            f"{self.config.base_url}/v2/orders",
            headers=self._headers(),
            params={"status": status, "limit": limit},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def get_asset(self, symbol: str) -> Optional[dict[str, Any]]:
        response = requests.get(
            f"{self.config.base_url}/v2/assets/{symbol.upper()}",
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

