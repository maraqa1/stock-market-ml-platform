from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Optional

import requests

from stockml.trading.config import AlpacaConfig


class AlpacaAPIError(RuntimeError):
    def __init__(self, method: str, url: str, response: requests.Response):
        self.method = method
        self.url = url
        self.status_code = response.status_code
        self.request_id = response.headers.get("X-Request-ID", "")
        self.response_text = response.text[:1000]
        super().__init__(f"alpaca_api_error status={self.status_code} request_id={self.request_id} body={self.response_text}")

    def as_dict(self) -> dict[str, str | int]:
        details: dict[str, str | int] = {
            "http_status": self.status_code,
            "request_id": self.request_id,
            "api_error": self.response_text,
        }
        try:
            payload = json.loads(self.response_text)
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            if payload.get("code") is not None:
                details["api_code"] = str(payload.get("code"))
            if payload.get("message") is not None:
                details["api_message"] = str(payload.get("message"))
        return details


def _raise_for_status(method: str, url: str, response: requests.Response) -> None:
    if response.status_code >= 400:
        raise AlpacaAPIError(method, url, response)
    response.raise_for_status()


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
        url = f"{self.config.base_url}/v2/account"
        response = requests.get(
            url,
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        _raise_for_status("GET", url, response)
        return response.json()

    def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.config.base_url}/v2/orders"
        response = requests.post(
            url,
            headers=self._headers(),
            json=order,
            timeout=self.timeout_seconds,
        )
        _raise_for_status("POST", url, response)
        return response.json()

    def list_orders(self, status: str = "open", limit: int = 100) -> list[dict[str, Any]]:
        url = f"{self.config.base_url}/v2/orders"
        response = requests.get(
            url,
            headers=self._headers(),
            params={"status": status, "limit": limit},
            timeout=self.timeout_seconds,
        )
        _raise_for_status("GET", url, response)
        return response.json()

    def get_order(self, order_id: str) -> dict[str, Any]:
        url = f"{self.config.base_url}/v2/orders/{order_id}"
        response = requests.get(
            url,
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        _raise_for_status("GET", url, response)
        return response.json()

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        url = f"{self.config.base_url}/v2/orders/{order_id}"
        response = requests.delete(
            url,
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        _raise_for_status("DELETE", url, response)
        if response.status_code == 204 or not response.content:
            return {"id": order_id, "status": "canceled"}
        return response.json()

    def cancel_all_orders(self) -> list[dict[str, Any]]:
        url = f"{self.config.base_url}/v2/orders"
        response = requests.delete(
            url,
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        _raise_for_status("DELETE", url, response)
        if not response.content:
            return []
        return response.json()

    def close_position(self, symbol: str) -> dict[str, Any]:
        url = f"{self.config.base_url}/v2/positions/{symbol.upper()}"
        response = requests.delete(
            url,
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        _raise_for_status("DELETE", url, response)
        return response.json()

    def list_positions(self) -> list[dict[str, Any]]:
        url = f"{self.config.base_url}/v2/positions"
        response = requests.get(
            url,
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        _raise_for_status("GET", url, response)
        return response.json()

    def get_asset(self, symbol: str) -> Optional[dict[str, Any]]:
        url = f"{self.config.base_url}/v2/assets/{symbol.upper()}"
        response = requests.get(
            url,
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        if response.status_code == 404:
            return None
        _raise_for_status("GET", url, response)
        return response.json()
