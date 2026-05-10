from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest

from portal.app import create_app


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _fixture_root(client) -> Path:
    return Path(client.application.config["PROJECT_ROOT"])


@pytest.fixture()
def api_client():
    root = Path("_tmp_trading_api_routes")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    _write_csv(
        root / "data" / "portal_outputs" / "08_alpaca_paper_order_plan_1.csv",
        [
            {
                "symbol": "AAA",
                "side": "buy",
                "approved_notional": 500,
                "trade_quality_status": "approved",
                "client_order_id": "stockml-AAA-buy",
            },
            {
                "symbol": "BBB",
                "side": "sell",
                "approved_notional": 0,
                "trade_quality_status": "rejected",
                "trade_quality_reason": "shorting_disabled",
            },
        ],
    )
    _write_csv(
        root / "data" / "portal_outputs" / "08_alpaca_paper_order_results_1.csv",
        [
            {
                "symbol": "AAA",
                "side": "buy",
                "status": "submitted",
                "notional": 500,
                "order_id": "order-aaa",
                "client_order_id": "stockml-AAA-buy",
                "message": "",
            },
            {
                "symbol": "BBB",
                "side": "sell",
                "status": "rejected",
                "notional": 0,
                "client_order_id": "stockml-BBB-sell",
                "message": "shorting_disabled",
            },
        ],
    )
    _write_csv(
        root / "data" / "portal_outputs" / "08_alpaca_paper_order_tracking_1.csv",
        [
            {
                "symbol": "AAA",
                "side": "buy",
                "status": "submitted",
                "alpaca_status": "filled",
                "notional": 500,
                "filled_qty": 2,
                "filled_avg_price": 250,
                "order_id": "order-aaa",
                "client_order_id": "stockml-AAA-buy",
            }
        ],
    )
    _write_csv(
        root / "data" / "portal_outputs" / "08_alpaca_paper_positions_1.csv",
        [{"symbol": "AAA", "qty": 2, "market_value": 520, "cost_basis": 500, "unrealized_pl": 20}],
    )
    _write_csv(
        root / "data" / "trading" / "operator_actions" / "operator_position_actions_1.csv",
        [
            {
                "timestamp": "2026-05-10T14:02:39",
                "symbol": "AAA",
                "operator_action": "close",
                "status": "submitted",
                "message": "manual_close_submitted",
                "order_id": "close-aaa",
                "client_order_id": "client-close-aaa",
                "alpaca_status": "accepted",
            }
        ],
    )
    _write_csv(
        root / "data" / "trading" / "agent_decisions" / "position_decisions_1.csv",
        [
            {
                "symbol": "AAA",
                "decision": "close",
                "recommended_action": "close_position",
                "decision_reason": "take_profit_hit",
                "unrealized_plpc": 0.04,
            },
            {
                "symbol": "CCC",
                "decision": "hold",
                "recommended_action": "keep_position",
                "decision_reason": "position_within_rules",
                "unrealized_plpc": 0.01,
            },
        ],
    )
    app = create_app(root)
    app.config.update(TESTING=True)
    return app.test_client()


def _json(client, path: str) -> dict:
    response = client.get(path)
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, dict)
    return payload


def test_pipeline_current_contract(api_client):
    payload = _json(api_client, "/api/trading/pipeline/current")
    assert set(payload) == {"source", "run", "stage_names", "stages"}
    assert isinstance(payload["stage_names"], list)
    assert isinstance(payload["stages"], list)
    assert len(payload["stages"]) == 6
    assert {"stage_name", "status", "output_count"}.issubset(payload["stages"][0])


def test_pipeline_artifact_fallback_groups_trading_stages_by_candidate_stamp(api_client):
    root = _fixture_root(api_client)
    _write_csv(root / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260510_134011.csv", [{"symbol": "NEW"}])
    _write_csv(root / "data" / "portal_outputs" / "08_alpaca_paper_order_plan_20260509_182906.csv", [{"symbol": "OLD"}])
    _write_csv(root / "data" / "portal_outputs" / "08_alpaca_paper_order_results_20260509_182906.csv", [{"symbol": "OLD"}])

    payload = _json(api_client, "/api/trading/pipeline/current")
    stages = {stage["stage_name"]: stage for stage in payload["stages"]}

    assert payload["run"]["display_label"] == "Latest Artifacts"
    assert stages["candidates"]["artifact"] == "08_alpaca_paper_candidate_pool_20260510_134011.csv"
    assert stages["selection"]["status"] == "missing"
    assert stages["submitted"]["status"] == "missing"
    assert "20260509_182906" not in str(stages["selection"])


def test_pipeline_history_contract(api_client):
    payload = _json(api_client, "/api/trading/pipeline/history?days=14")
    assert set(payload) == {"source", "days", "stage_names", "runs"}
    assert payload["days"] == 14
    assert isinstance(payload["stage_names"], list)
    assert isinstance(payload["runs"], list)


def test_positions_contract(api_client):
    payload = _json(api_client, "/api/trading/positions")
    assert set(payload) == {"source", "refreshed_at", "summary", "pending_close_order_count", "positions"}
    assert isinstance(payload["positions"], list)
    assert payload["summary"]["position_count"] == 1
    assert payload["positions"][0]["position_id"] == "paper:AAA"
    assert payload["pending_close_order_count"] == 1
    assert payload["positions"][0]["broker_order"]["label"] == "Close order accepted"


def test_position_lineage_contract(api_client):
    payload = _json(api_client, "/api/trading/positions/paper:AAA/lineage")
    assert set(payload) == {"source", "position_id", "events", "summary"}
    assert payload["position_id"] == "paper:AAA"
    assert isinstance(payload["events"], list)
    assert {"event_count", "state_change_count"}.issubset(payload["summary"])


def test_basket_today_contract(api_client):
    payload = _json(api_client, "/api/trading/basket/today")
    assert set(payload) == {"source", "run_id", "generated_at", "rows", "counts"}
    assert payload["counts"]["planned"] == 2
    assert payload["counts"]["submitted"] == 1
    assert payload["counts"]["filled"] == 1
    assert isinstance(payload["rows"], list)
    assert {"symbol", "status", "reason", "position_id"}.issubset(payload["rows"][0])


def test_basket_integrity_contract(api_client):
    payload = _json(api_client, "/api/trading/basket/integrity")
    assert set(payload) == {"source", "run_id", "selected", "submitted", "filled", "closed_since", "monitor_changes_since", "diffs"}
    assert payload["selected"] == 2
    assert payload["submitted"] == 1
    assert payload["filled"] == 1
    assert isinstance(payload["diffs"], list)


def test_monitor_today_contract(api_client):
    payload = _json(api_client, "/api/trading/monitor/today")
    assert set(payload) == {"source", "checks", "state_changes", "counts"}
    assert isinstance(payload["checks"], list)
    assert {"monitor_checks", "state_changes"}.issubset(payload["counts"])


def test_queue_contract(api_client):
    payload = _json(api_client, "/api/trading/queue")
    assert set(payload) == {"source", "generated_at", "items", "counts"}
    assert payload["counts"]["total"] == 1
    assert payload["counts"]["close"] == 1
    assert payload["items"][0]["position_id"] == "paper:AAA"


def test_positions_body_partial_contract(api_client):
    response = api_client.get("/trading/_partials/positions-body")
    assert response.status_code == 200
    assert b"data-position-id=\"paper:AAA\"" in response.data
    assert b"Close order accepted" in response.data
    assert b"Waiting for broker fill." in response.data
    assert b"close-aaa" in response.data


def test_position_lineage_fragment_contract(api_client):
    response = api_client.get("/trading/positions/paper:AAA/lineage")
    assert response.status_code == 200
    assert b"Position Lineage" in response.data
    assert b"paper:AAA" in response.data


def test_queue_apply_posts_json(api_client, monkeypatch):
    called = {}

    def fake_position_action(root, symbol, action):
        called["symbol"] = symbol
        called["action"] = action
        return {"status": "dry_run", "message": "manual_close_dry_run_submit_orders_disabled", "order_id": ""}

    monkeypatch.setattr("portal.app.position_action", fake_position_action)
    response = api_client.post(
        "/trading/queue/queue-1/apply",
        json={"symbol": "AAA", "position_id": "paper:AAA", "decision": "close"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "dry_run"
    assert called == {"symbol": "AAA", "action": "close"}


def test_position_close_posts_json(api_client, monkeypatch):
    def fake_position_action(root, symbol, action):
        return {"status": "submitted", "message": "manual_close_submitted", "order_id": "broker-1"}

    monkeypatch.setattr("portal.app.position_action", fake_position_action)
    response = api_client.post("/api/trading/positions/paper:AAA/close", json={"symbol": "AAA"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "submitted"
    assert payload["broker_order_id"] == "broker-1"
