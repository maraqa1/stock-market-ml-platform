import requests

from stockml.trading.alpaca_client import AlpacaAPIError, AlpacaPaperClient
from stockml.trading.config import AlpacaConfig


def config():
    return AlpacaConfig(
        api_key="key",
        secret_key="secret",
        base_url="https://paper-api.alpaca.markets",
        submit_orders=False,
        extended_hours=False,
        max_orders=10,
        max_notional_per_order=1000,
        max_total_notional=10000,
        min_trade_price=5,
        max_sector_fraction=0.4,
        min_side_probability=0.55,
        min_abs_probability_edge=0.05,
        min_intraday_volume=100000,
        min_market_cap=300000000,
        min_risk_adjusted_score=0.005,
        transaction_cost_bps=10,
    )


def test_alpaca_api_error_captures_request_id_and_body():
    response = requests.Response()
    response.status_code = 422
    response._content = b'{"message":"market is closed"}'
    response.headers["X-Request-ID"] = "req-123"
    error = AlpacaAPIError("POST", "https://paper-api.alpaca.markets/v2/orders", response)
    assert error.status_code == 422
    assert error.request_id == "req-123"
    assert "market is closed" in error.response_text
    assert error.as_dict()["http_status"] == 422


def test_cancel_order_uses_alpaca_delete_endpoint(monkeypatch):
    calls = []

    def fake_delete(url, headers, timeout):
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        response = requests.Response()
        response.status_code = 204
        response._content = b""
        return response

    monkeypatch.setattr("stockml.trading.alpaca_client.requests.delete", fake_delete)
    result = AlpacaPaperClient(config()).cancel_order("order-123")

    assert result == {"id": "order-123", "status": "canceled"}
    assert calls[0]["url"].endswith("/v2/orders/order-123")
    assert calls[0]["headers"]["APCA-API-KEY-ID"] == "key"


def test_cancel_all_orders_uses_alpaca_bulk_delete_endpoint(monkeypatch):
    def fake_delete(url, headers, timeout):
        response = requests.Response()
        response.status_code = 200
        response._content = b'[{"id":"order-1","status":200}]'
        return response

    monkeypatch.setattr("stockml.trading.alpaca_client.requests.delete", fake_delete)
    result = AlpacaPaperClient(config()).cancel_all_orders()

    assert result == [{"id": "order-1", "status": 200}]
