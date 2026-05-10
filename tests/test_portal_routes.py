import pytest
import pandas as pd
import shutil
import json
from pathlib import Path

from portal.app import create_app


@pytest.fixture()
def client():
    root = Path("_tmp_portal_routes")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    app = create_app(root)
    app.config.update(TESTING=True)
    return app.test_client()


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


@pytest.fixture()
def symbol_client():
    root = Path("_tmp_symbol_routes")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    write_csv(
        root / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [{"symbol": "TSLA", "side": "long", "qty": 2, "avg_entry_price": 240, "current_price": 245, "market_value": 490, "cost_basis": 480, "unrealized_pl": 10, "unrealized_plpc": 0.0208}],
    )
    write_csv(
        root / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_1.csv",
        [{"candidate_rank": 7, "symbol": "TSLA", "company": "Tesla, Inc.", "sector": "Consumer Cyclical", "trade_action": "Long", "risk_adjusted_score": 0.71, "expected_trade_return": 0.016, "order_eligible": True}],
    )
    app = create_app(root)
    app.config.update(TESTING=True)
    return app.test_client()


def test_main_routes_return_200(client):
    for route in ["/", "/universe", "/data-quality", "/gold", "/signals", "/trading", "/journal", "/shortlist", "/model-validation", "/no-decision", "/dev/styleguide"]:
        response = client.get(route)
        assert response.status_code == 200


def test_lifecycle_routes_redirect_to_journal(client):
    for route in ["/lifecycle", "/trading/lifecycle"]:
        response = client.get(route)
        assert response.status_code == 301
        assert response.headers["Location"].endswith("/journal")


def test_activity_journal_page_and_api_contract(client):
    response = client.get("/journal?event_type=__none__")
    assert response.status_code == 200
    assert b"Activity Journal" in response.data
    assert b"data-journal-table" in response.data
    assert b"No events match the current filters." in response.data
    assert b"trading/_partials/lineage.html" not in response.data

    payload_response = client.get("/api/journal/events")
    assert payload_response.status_code == 200
    payload = payload_response.get_json()
    assert {"events", "next_cursor", "total_in_range"}.issubset(payload)

    csv_response = client.get("/api/journal/events.csv")
    assert csv_response.status_code == 200
    assert csv_response.mimetype == "text/csv"
    assert b"id,event_at,symbol,event_type,source,details_summary,position_id" in csv_response.data


def test_base_loads_spec00_theme(client):
    response = client.get("/dev/styleguide")
    assert response.status_code == 200
    assert b'data-theme="dark"' in response.data
    assert b"css/theme.css" in response.data


def test_base_renders_spec25_top_nav_and_search(client):
    response = client.get("/trading")
    assert response.status_code == 200
    markers = [
        b"Trading Console",
        b"Activity Journal",
        b"Model Shortlist",
        b"Validation",
        b"Data Estate",
        b"Diagnostics",
    ]
    cursor = -1
    for marker in markers:
        position = response.data.find(marker)
        assert position > cursor
        cursor = position
    assert b'id="global-search"' in response.data
    assert b"js/nav_search.js" in response.data
    assert b"js/keyboard.js" in response.data
    assert b"js/table_sort.js" in response.data


def test_trading_tables_render_spec27_table_controls(symbol_client):
    response = symbol_client.get("/trading?sort=pnl_pct&dir=desc&q=tsl")
    assert response.status_code == 200
    assert b'data-table="open-positions"' in response.data
    assert b'data-table-filter' in response.data
    assert b'data-sort-key="pnl_pct"' in response.data
    assert b'data-sort-type="number"' in response.data
    assert b"class=\"num-l col-pinned\"" in response.data
    assert b'value="tsl"' in response.data


def test_theme_renders_spec27_table_control_styles(client):
    response = client.get("/static/css/theme.css")
    assert response.status_code == 200
    assert b".table-toolbar" in response.data
    assert b".col-pinned" in response.data
    assert b".sort-indicator" in response.data


def test_theme_overrides_legacy_sidebar_grid_layout(client):
    response = client.get("/static/css/theme.css")
    assert response.status_code == 200
    assert b".app-layout" in response.data
    assert b"display: block;" in response.data
    assert b".global-header-strip" in response.data
    assert b"width: 100%;" in response.data


def test_search_api_returns_symbol_payload(client):
    response = client.get("/api/search?q=tsl&limit=5")
    assert response.status_code == 200
    payload = response.get_json()
    assert "groups" in payload
    assert any(item.get("symbol") == "TSLA" for group in payload["groups"] for item in group["items"])


def test_search_api_run_id_returns_runs_only(client):
    response = client.get("/api/search?q=2026-05-09-A&limit=5")
    assert response.status_code == 200
    payload = response.get_json()
    assert [group["key"] for group in payload["groups"]] == ["runs"]


def test_symbol_detail_page_renders_fixture_data(symbol_client):
    response = symbol_client.get("/symbols/TSLA")
    assert response.status_code == 200
    assert b"TSLA" in response.data
    assert b"Position" in response.data
    assert b"Today's Signal" in response.data
    assert b"Data Freshness" in response.data
    assert b"30-Day History" in response.data
    assert b"Activity" in response.data
    assert b"data-close-position" in response.data


def test_symbol_detail_api_and_missing_symbol(symbol_client):
    response = symbol_client.get("/api/symbols/TSLA")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["symbol"] == "TSLA"
    assert {"position", "today_signal", "freshness", "history", "events"}.issubset(payload)
    assert symbol_client.get("/symbols/INVALID").status_code == 404


def test_trading_symbol_cells_link_to_symbol_detail(symbol_client):
    response = symbol_client.get("/trading")
    assert response.status_code == 200
    assert b'href="/symbols/TSLA"' in response.data


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
    assert b"every 30s" in response.data
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


def test_trading_page_renders_monitor_activity_timeline(client):
    response = client.get("/trading")
    assert response.status_code == 200
    assert b"Monitor Activity" in response.data
    assert b"monitor-timeline" in response.data
    assert b"monitor checks today" in response.data or b"No monitor checks recorded today" in response.data


def test_trading_page_renders_pipeline_history_diagnostics(client):
    response = client.get("/trading")
    assert response.status_code == 200
    assert b"Pipeline Run History" in response.data
    assert b"Candidates" in response.data
    assert b"Selected" in response.data
    assert b"Open Activity Journal" in response.data


def test_trading_page_renders_spec20_diagnostics_sections(client):
    response = client.get("/trading")
    assert response.status_code == 200
    assert b"Execution Guardrails" in response.data
    assert b"Edit settings" in response.data
    assert b"Execution Quality" in response.data
    assert b"Fill ratio" in response.data
    assert b"Slippage" in response.data
    assert b"Cadence Settings" in response.data
    assert b"monitor_interval_seconds" in response.data


def test_trading_timer_settings_post_persists_config(client):
    response = client.post(
        "/trading/timer-settings",
        data={
            "positions_refresh_seconds": "7",
            "monitor_interval_seconds": "45",
            "pipeline_refresh_seconds": "90",
        },
    )
    assert response.status_code == 302
    config_path = Path("_tmp_portal_routes") / "data" / "portal_outputs" / "portal_timer_settings.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["positions_refresh_seconds"] == 7
    assert payload["monitor_interval_seconds"] == 45
    assert payload["pipeline_refresh_seconds"] == 90

    page = client.get("/trading")
    assert b"every 45s" in page.data
    assert b'data-refresh-ms="7000"' in page.data


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


def test_trading_page_renders_model_shortlist_filters(client):
    response = client.get("/trading")
    assert response.status_code == 200
    assert b"Model Shortlist" in response.data
    assert b"data-shortlist-side" in response.data
    assert b"data-shortlist-sector" in response.data
    assert b"Show ranked model shortlist" in response.data


def test_shortlist_page_renders_historical_shortlist_filters(symbol_client):
    response = symbol_client.get("/shortlist?bias=long")
    assert response.status_code == 200
    assert b"Model Shortlist" in response.data
    assert b'name="date"' in response.data
    assert b'name="bias"' in response.data
    assert b'name="sector"' in response.data
    assert b'name="in_basket"' in response.data
    assert b'href="/symbols/TSLA"' in response.data
    assert b'value="long" selected' in response.data


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
