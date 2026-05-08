import requests

from stockml.trading.alpaca_client import AlpacaAPIError


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
