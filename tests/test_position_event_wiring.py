from pathlib import Path

import pandas as pd

from stockml.agents import position_decision_engine
from stockml.trading import manual_position_actions, paper_trader
from stockml.trading.config import AlpacaConfig


TEST_OUTPUT_DIR = Path("_tmp_position_event_wiring")


def config(submit_orders: bool = True, overnight_trading_enabled: bool = False) -> AlpacaConfig:
    return AlpacaConfig(
        api_key="key",
        secret_key="secret",
        base_url="https://paper-api.alpaca.markets",
        submit_orders=submit_orders,
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
        live_trading_enabled=False,
        paper_trading_enabled=True,
        overnight_trading_enabled=overnight_trading_enabled,
    )


def _test_path(name: str) -> Path:
    TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = TEST_OUTPUT_DIR / name
    path.unlink(missing_ok=True)
    return path


def test_manual_keep_and_close_record_operator_events(monkeypatch):
    events = []
    monkeypatch.setattr(manual_position_actions, "record_event_safely", lambda *args, **kwargs: events.append(args) or True)

    path = _test_path("actions.csv")
    manual_position_actions.apply_manual_position_action("FLEX", "keep", config=config(False), output_path=path)
    manual_position_actions.apply_manual_position_action("FLEX", "close", config=config(False), output_path=path)

    assert [event[1] for event in events] == ["operator_keep", "operator_close"]
    assert all(event[0] == "paper:FLEX" for event in events)


def test_write_position_decisions_records_monitor_events(monkeypatch):
    events = []
    TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(position_decision_engine, "AGENT_DECISIONS_DIR", TEST_OUTPUT_DIR)
    monkeypatch.setattr(position_decision_engine, "record_event_safely", lambda *args, **kwargs: events.append((args, kwargs)) or True)

    decisions = pd.DataFrame(
        [
            {"symbol": "FLEX", "decision": "hold", "recommended_action": "keep_position", "decision_reason": "ok"},
            {"symbol": "DGXX", "decision": "watch", "recommended_action": "manual_review", "decision_reason": "stale"},
            {"symbol": "AUR", "decision": "close", "recommended_action": "close_position", "decision_reason": "stop"},
            {"symbol": "ADMA", "decision": "replace", "recommended_action": "close_then_open_replacement", "decision_reason": "better"},
        ]
    )

    position_decision_engine.write_position_decisions(decisions, "test")

    assert [event[0][1] for event in events] == ["monitor_safe", "monitor_watch", "monitor_close", "monitor_rotate"]
    assert [event[0][0] for event in events] == ["paper:FLEX", "paper:DGXX", "paper:AUR", "paper:ADMA"]


class FakeClient:
    def submit_order(self, request):
        return {"id": "order-1", "status": "accepted", "submitted_at": "2026-05-08T12:00:00Z"}

    def get_account(self):
        return {"status": "ACTIVE", "trading_blocked": False, "account_blocked": False, "buying_power": "100000"}

    def list_orders(self, status="open", limit=500):
        return []

    def get_asset(self, symbol):
        return {"tradable": True, "status": "active", "shortable": True, "overnight_tradable": True}

    def list_positions(self):
        return []


class OvernightMapClient(FakeClient):
    def __init__(self, overnight_symbols: set[str]):
        self.overnight_symbols = overnight_symbols

    def get_asset(self, symbol):
        attributes = ["overnight_tradable"] if str(symbol).upper() in self.overnight_symbols else []
        return {
            "tradable": True,
            "status": "active",
            "shortable": True,
            "attributes": attributes,
        }


def test_paper_trader_records_submitted_and_guardrail_events(monkeypatch):
    events = []
    plan = pd.DataFrame(
        [
            {
                "symbol": "FLEX",
                "client_order_id": "stockml-FLEX-buy",
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "extended_hours": False,
                "trade_action": "Long",
                "trade_quality_status": "approved",
                "trade_quality_reason": "",
                "order_eligible": True,
                "suggested_quantity": 2,
                "notional": 200,
            },
            {
                "symbol": "AKAN",
                "client_order_id": "stockml-AKAN-buy",
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "extended_hours": False,
                "trade_action": "Long",
                "trade_quality_status": "rejected",
                "trade_quality_reason": "market_cap_below_minimum",
                "order_eligible": False,
                "suggested_quantity": 0,
                "notional": 0,
            },
        ]
    )

    TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paper_trader, "PORTAL_OUTPUTS_DIR", TEST_OUTPUT_DIR)
    monkeypatch.setattr(paper_trader, "alpaca_config", lambda: config(True))
    monkeypatch.setattr(paper_trader, "autopilot_blocks_basket_submission", lambda: (False, ""))
    monkeypatch.setattr(paper_trader, "latest_signal_table", lambda signal_file=None: pd.DataFrame([{"symbol": "FLEX"}]))
    monkeypatch.setattr(paper_trader, "latest_model_freshness", lambda signal_file=None: (True, "model_signal_table_fresh", "signals.csv"))
    monkeypatch.setattr(paper_trader, "build_candidate_pool", lambda signals, cfg: pd.DataFrame([{"symbol": "FLEX"}]))
    monkeypatch.setattr(paper_trader, "build_order_plan", lambda signals, cfg: plan)
    monkeypatch.setattr(paper_trader, "AlpacaPaperClient", lambda cfg: FakeClient())
    monkeypatch.setattr(paper_trader, "record_event_safely", lambda *args, **kwargs: events.append((args, kwargs)) or True)

    result = paper_trader.run_paper_trading()

    assert result["orders_submitted"] == 1
    assert [event[0][1] for event in events] == ["selected", "submitted", "guardrail_blocked"]
    assert [event[0][0] for event in events] == ["paper:FLEX", "paper:FLEX", "paper:AKAN"]


def test_paper_trader_blocks_basket_submission_when_paper_autopilot_running(monkeypatch):
    client_calls = []

    class TrackingClient(FakeClient):
        def submit_order(self, request):
            client_calls.append(request)
            return super().submit_order(request)

    TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paper_trader, "PORTAL_OUTPUTS_DIR", TEST_OUTPUT_DIR)
    monkeypatch.setattr(paper_trader, "alpaca_config", lambda: config(True))
    monkeypatch.setattr(paper_trader, "autopilot_blocks_basket_submission", lambda: (True, "paper_autopilot_running_blocks_basket_submission"))
    monkeypatch.setattr(paper_trader, "latest_signal_table", lambda signal_file=None: pd.DataFrame([{"symbol": "FLEX"}]))
    monkeypatch.setattr(paper_trader, "latest_model_freshness", lambda signal_file=None: (True, "model_signal_table_fresh", "signals.csv"))
    monkeypatch.setattr(paper_trader, "build_order_plan", lambda signals, cfg: pd.DataFrame())
    monkeypatch.setattr(paper_trader, "AlpacaPaperClient", lambda cfg: TrackingClient())

    try:
        paper_trader.run_paper_trading()
    except RuntimeError as exc:
        assert str(exc) == "paper_autopilot_running_blocks_basket_submission"
    else:
        raise AssertionError("expected basket submission to be blocked")

    assert client_calls == []


def test_paper_trader_submits_extended_hours_limit_payload(monkeypatch):
    client_calls = []
    plan = pd.DataFrame(
        [
            {
                "symbol": "VSTM",
                "client_order_id": "stockml-VSTM-buy",
                "side": "buy",
                "type": "limit",
                "time_in_force": "day",
                "extended_hours": True,
                "limit_price": 20.1,
                "trade_action": "Long",
                "trade_quality_status": "approved",
                "trade_quality_reason": "",
                "order_eligible": True,
                "suggested_quantity": 2,
                "notional": 40.2,
            }
        ]
    )

    class TrackingClient(FakeClient):
        def submit_order(self, request):
            client_calls.append(request)
            return super().submit_order(request)

    TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paper_trader, "PORTAL_OUTPUTS_DIR", TEST_OUTPUT_DIR)
    monkeypatch.setattr(paper_trader, "alpaca_config", lambda: config(True))
    monkeypatch.setattr(paper_trader, "autopilot_blocks_basket_submission", lambda: (False, ""))
    monkeypatch.setattr(paper_trader, "latest_signal_table", lambda signal_file=None: pd.DataFrame([{"symbol": "VSTM"}]))
    monkeypatch.setattr(paper_trader, "latest_model_freshness", lambda signal_file=None: (True, "model_signal_table_fresh", "signals.csv"))
    monkeypatch.setattr(paper_trader, "build_candidate_pool", lambda signals, cfg: pd.DataFrame([{"symbol": "VSTM"}]))
    monkeypatch.setattr(paper_trader, "build_order_plan", lambda signals, cfg: plan)
    monkeypatch.setattr(paper_trader, "AlpacaPaperClient", lambda cfg: TrackingClient())
    monkeypatch.setattr(paper_trader, "record_event_safely", lambda *args, **kwargs: True)

    result = paper_trader.run_paper_trading()

    assert result["orders_submitted"] == 1
    assert len(client_calls) == 1
    request = client_calls[0]
    assert request["symbol"] == "VSTM"
    assert request["qty"] == "2"
    assert request["type"] == "limit"
    assert request["time_in_force"] == "day"
    assert request["extended_hours"] is True
    assert request["limit_price"] == 20.1


def test_paper_trader_filters_non_overnight_assets_before_final_selection(monkeypatch):
    client_calls = []
    candidate_pool = pd.DataFrame(
        [
            {
                "symbol": "BNY",
                "client_order_id": "stockml-BNY-buy",
                "side": "buy",
                "type": "limit",
                "time_in_force": "day",
                "extended_hours": True,
                "limit_price": 100.5,
                "trade_action": "Long",
                "trade_quality_status": "approved",
                "trade_quality_reason": "",
                "order_eligible": True,
                "suggested_quantity": 2,
                "notional": 200,
                "approved_notional": 200,
                "risk_adjusted_score": 2.0,
                "directional_strength": 1.0,
                "risk_tier": "high_quality",
            },
            {
                "symbol": "VSTM",
                "client_order_id": "stockml-VSTM-buy",
                "side": "buy",
                "type": "limit",
                "time_in_force": "day",
                "extended_hours": True,
                "limit_price": 20.1,
                "trade_action": "Long",
                "trade_quality_status": "approved",
                "trade_quality_reason": "",
                "order_eligible": True,
                "suggested_quantity": 2,
                "notional": 40.2,
                "approved_notional": 40.2,
                "risk_adjusted_score": 1.0,
                "directional_strength": 0.9,
                "risk_tier": "medium",
            },
        ]
    )

    class TrackingClient(OvernightMapClient):
        def submit_order(self, request):
            client_calls.append(request)
            return super().submit_order(request)

    TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paper_trader, "PORTAL_OUTPUTS_DIR", TEST_OUTPUT_DIR)
    monkeypatch.setattr(paper_trader, "alpaca_config", lambda: config(True, overnight_trading_enabled=True))
    monkeypatch.setattr(paper_trader, "autopilot_blocks_basket_submission", lambda: (False, ""))
    monkeypatch.setattr(paper_trader, "latest_signal_table", lambda signal_file=None: pd.DataFrame([{"symbol": "VSTM"}]))
    monkeypatch.setattr(paper_trader, "latest_model_freshness", lambda signal_file=None: (True, "model_signal_table_fresh", "signals.csv"))
    monkeypatch.setattr(paper_trader, "build_candidate_pool", lambda signals, cfg: candidate_pool)
    monkeypatch.setattr(paper_trader, "AlpacaPaperClient", lambda cfg: TrackingClient({"VSTM"}))
    monkeypatch.setattr(paper_trader, "record_event_safely", lambda *args, **kwargs: True)

    result = paper_trader.run_paper_trading()

    assert result["orders_submitted"] == 1
    assert result["result_rejected"] == 0
    assert client_calls[0]["symbol"] == "VSTM"
    written_pool = pd.read_csv(result["candidate_pool_path"])
    blocked = written_pool[written_pool["symbol"].eq("BNY")].iloc[0]
    assert blocked["trade_quality_status"] == "rejected"
    assert blocked["overnight_tradable"] is False or str(blocked["overnight_tradable"]).lower() == "false"
    assert "asset_not_overnight_tradable" in blocked["trade_quality_reason"]


def test_paper_trader_blocks_submission_when_model_is_stale(monkeypatch):
    TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paper_trader, "PORTAL_OUTPUTS_DIR", TEST_OUTPUT_DIR)
    monkeypatch.setattr(paper_trader, "alpaca_config", lambda: config(True))
    monkeypatch.setattr(paper_trader, "autopilot_blocks_basket_submission", lambda: (False, ""))
    monkeypatch.setattr(paper_trader, "latest_model_freshness", lambda signal_file=None: (False, "model_signal_table_stale:2026-05-20", "signals.csv"))

    try:
        paper_trader.run_paper_trading()
    except RuntimeError as exc:
        assert str(exc) == "model_signal_table_stale:2026-05-20"
    else:
        raise AssertionError("expected stale model trading block")


def test_paper_trader_plan_only_writes_artifacts_without_submitting(monkeypatch):
    events = []
    client_calls = []
    plan = pd.DataFrame(
        [
            {
                "symbol": "FLEX",
                "client_order_id": "stockml-FLEX-buy",
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "extended_hours": False,
                "trade_action": "Long",
                "trade_quality_status": "approved",
                "trade_quality_reason": "",
                "order_eligible": True,
                "suggested_quantity": 2,
                "notional": 200,
            }
        ]
    )

    class TrackingClient(FakeClient):
        def submit_order(self, request):
            client_calls.append(request)
            return super().submit_order(request)

    TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paper_trader, "PORTAL_OUTPUTS_DIR", TEST_OUTPUT_DIR)
    monkeypatch.setattr(paper_trader, "alpaca_config", lambda: config(True))
    monkeypatch.setattr(paper_trader, "autopilot_blocks_basket_submission", lambda: (True, "paper_autopilot_running_blocks_basket_submission"))
    monkeypatch.setattr(paper_trader, "latest_signal_table", lambda signal_file=None: pd.DataFrame([{"symbol": "FLEX"}]))
    monkeypatch.setattr(paper_trader, "latest_model_freshness", lambda signal_file=None: (True, "model_signal_table_fresh", "signals.csv"))
    monkeypatch.setattr(paper_trader, "build_candidate_pool", lambda signals, cfg: pd.DataFrame([{"symbol": "FLEX"}]))
    monkeypatch.setattr(paper_trader, "build_order_plan", lambda signals, cfg: plan)
    monkeypatch.setattr(paper_trader, "AlpacaPaperClient", lambda cfg: TrackingClient())
    monkeypatch.setattr(paper_trader, "record_event_safely", lambda *args, **kwargs: events.append((args, kwargs)) or True)

    result = paper_trader.run_paper_trading(plan_only=True)

    assert result["plan_only"] is True
    assert result["dry_run"] is True
    assert result["orders_submitted"] == 0
    assert client_calls == []
    assert result["candidate_pool_path"].exists()
    assert result["plan_path"].exists()
    results = pd.read_csv(result["result_path"])
    assert results.iloc[0]["status"] == "dry_run"
    assert results.iloc[0]["message"] == "plan_only: no broker submission"
    assert [event[0][1] for event in events] == ["selected"]


def test_paper_trader_stamps_client_order_ids_per_run():
    plan = pd.DataFrame(
        [
            {"client_order_id": "stockml-20260508-FWRD-buy"},
            {"client_order_id": "stockml-20260508-VERY-LONG-SYMBOL-NAME-buy"},
        ]
    )

    stamped = paper_trader._stamp_client_order_ids(plan, "20260509_172837")

    assert stamped.iloc[0]["client_order_id"] == "stockml-20260508-FWRD-buy-20260509172837"
    assert stamped.iloc[0]["client_order_id"] != plan.iloc[0]["client_order_id"]
    assert all(len(value) <= 48 for value in stamped["client_order_id"])
