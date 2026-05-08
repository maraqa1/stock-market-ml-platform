import pytest

from portal.app import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(tmp_path)
    app.config.update(TESTING=True)
    return app.test_client()


def test_main_routes_return_200(client):
    for route in ["/", "/universe", "/data-quality", "/gold", "/signals", "/model-validation", "/no-decision"]:
        response = client.get(route)
        assert response.status_code == 200


def test_health_returns_json(client):
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert "latest_gold_file" in payload


def test_stock_detail_missing_ticker_returns_200(client):
    response = client.get("/stock/AAPL")
    assert response.status_code == 200
    assert b"No detail rows found" in response.data

