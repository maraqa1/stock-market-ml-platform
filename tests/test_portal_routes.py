import pytest
from pathlib import Path

from portal.app import create_app


@pytest.fixture()
def client():
    root = Path("_tmp_portal_routes")
    root.mkdir(parents=True, exist_ok=True)
    app = create_app(root)
    app.config.update(TESTING=True)
    return app.test_client()


def test_main_routes_return_200(client):
    for route in ["/", "/universe", "/data-quality", "/gold", "/signals", "/trading", "/trading/lifecycle", "/model-validation", "/no-decision", "/dev/styleguide"]:
        response = client.get(route)
        assert response.status_code == 200


def test_base_loads_spec00_theme(client):
    response = client.get("/dev/styleguide")
    assert response.status_code == 200
    assert b'data-theme="dark"' in response.data
    assert b"css/theme.css" in response.data


def test_styleguide_supports_light_theme(client):
    response = client.get("/dev/styleguide?theme=light")
    assert response.status_code == 200
    assert b'data-theme="light"' in response.data
    assert b"Theme System" in response.data


def test_styleguide_renders_shared_status_pill_and_dot_macros(client):
    response = client.get("/dev/styleguide")
    assert response.status_code == 200
    assert b"pill pill-safe" in response.data
    assert b"pill pill-rejected" in response.data
    assert b"dot dot-safe" in response.data
    assert b"dot dot-rejected" in response.data


def test_styleguide_renders_side_and_number_format_macros(client):
    response = client.get("/dev/styleguide")
    assert response.status_code == 200
    assert b"side side-long" in response.data
    assert b"side side-short" in response.data
    assert b"side side-neutral" in response.data
    assert b"+$42.25" in response.data
    assert b"-$18.50" in response.data
    assert b"+3.10%" in response.data
    assert b"-1.40%" in response.data


def test_health_returns_json(client):
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert "latest_gold_file" in payload


def test_trading_refresh_redirects(client, monkeypatch):
    called = {}

    def fake_refresh(root):
        called["root"] = root
        return {"orders_tracked": 0}

    monkeypatch.setattr("portal.app.refresh_trading_artifacts", fake_refresh)
    response = client.post("/trading/refresh")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/trading")
    assert "root" in called


def test_trading_page_renders_spec07_zone_skeleton(client):
    response = client.get("/trading")
    assert response.status_code == 200
    expected_order = [
        b'data-zone="header"',
        b'data-zone="cadence"',
        b'data-zone="pipeline-freshness"',
        b'data-zone="kpi-row"',
        b'data-zone="basket-integrity"',
        b'data-zone="monitor-activity"',
        b'data-zone="action-queue"',
        b'data-zone="open-positions"',
        b'data-zone="run-summary"',
        b'data-zone="todays-basket"',
        b'data-zone="rejected-trimmed"',
        b'data-zone="model-shortlist"',
        b'data-zone="diagnostics"',
    ]
    cursor = -1
    for marker in expected_order:
        position = response.data.find(marker)
        assert position > cursor
        cursor = position


def test_trading_page_renders_spec08_09_10_top_of_page(client):
    response = client.get("/trading")
    assert response.status_code == 200
    assert b"Paper Trading - Research Mode" in response.data
    assert b"Paper Only" in response.data
    assert b"Live Trading Disabled" in response.data
    assert b"data-next-monitor" in response.data
    assert b"Account Equity" in response.data
    assert b"Today" in response.data
    assert b"Net Exposure" in response.data
    assert b"data-pipeline-refresh-url" in response.data
    assert b"Yahoo" in response.data
    assert b"Submitted" in response.data


def test_trading_page_renders_basket_integrity_counts(client):
    response = client.get("/trading")
    assert response.status_code == 200
    assert b"Basket Integrity" in response.data
    assert b"Closed Since" in response.data
    assert b"Monitor Changes" in response.data
    assert b"Open full basket lineage" in response.data
    assert b"data-basket-lineage-template" in response.data


def test_trading_page_renders_todays_basket_table(client):
    response = client.get("/trading")
    assert response.status_code == 200
    assert b"Today's Basket" in response.data
    assert b"Reason / Note" in response.data
    assert b"Order ID" in response.data


def test_trading_page_renders_rejected_trimmed_table(client):
    response = client.get("/trading")
    assert response.status_code == 200
    assert b"Rejected &amp; Trimmed" in response.data
    assert b"Source" in response.data
    assert b"Reason" in response.data
    assert b"Planned" in response.data


def test_pipeline_strip_partial_route_returns_fragment(client):
    response = client.get("/trading/_partials/pipeline-strip")
    assert response.status_code == 200
    assert b"Pipeline Freshness" in response.data
    assert b"pipeline-stage" in response.data


def test_trading_refresh_data_returns_json(client, monkeypatch):
    def fake_refresh(root):
        return {"orders_tracked": 3, "tracking_path": "tracking.csv"}

    monkeypatch.setattr("portal.app.refresh_trading_artifacts", fake_refresh)
    response = client.post("/trading/refresh-data")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["orders_tracked"] == 3


def test_trading_position_action_redirects(client, monkeypatch):
    called = {}

    def fake_action(root, symbol, action):
        called["root"] = root
        called["symbol"] = symbol
        called["action"] = action
        return {"status": "recorded", "message": "operator_keep_position"}

    monkeypatch.setattr("portal.app.position_action", fake_action)
    response = client.post("/trading/positions/FLEX/keep")
    assert response.status_code == 302
    assert "action_status=recorded" in response.headers["Location"]
    assert called["symbol"] == "FLEX"
    assert called["action"] == "keep"


def test_stock_detail_missing_ticker_returns_200(client):
    response = client.get("/stock/AAPL")
    assert response.status_code == 200
    assert b"No detail rows found" in response.data
