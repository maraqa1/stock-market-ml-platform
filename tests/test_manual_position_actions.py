from pathlib import Path

import pandas as pd

from stockml.trading.config import AlpacaConfig
from stockml.trading.manual_position_actions import apply_manual_position_action


def config(submit_orders: bool = False, live: bool = False, overnight: bool = False) -> AlpacaConfig:
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
        live_trading_enabled=live,
        paper_trading_enabled=True,
        overnight_trading_enabled=overnight,
    )


class FakeClient:
    def __init__(self):
        self.closed = []
        self.orders = []

    def close_position(self, symbol):
        self.closed.append(symbol)
        return {"id": "order-1", "client_order_id": "manual-close-1", "status": "accepted"}

    def list_positions(self):
        return [{"symbol": "FLEX", "qty": "4", "current_price": "10.00", "avg_entry_price": "9.90"}]

    def get_asset(self, symbol):
        return {"tradable": True, "status": "active", "shortable": True, "overnight_tradable": True}

    def submit_order(self, order):
        self.orders.append(order)
        return {"id": "order-2", "client_order_id": order["client_order_id"], "status": "accepted"}


def test_keep_records_operator_action_without_alpaca_call(tmp_path: Path):
    client = FakeClient()
    path = tmp_path / "actions.csv"
    result = apply_manual_position_action("FLEX", "keep", config=config(), client=client, output_path=path)
    assert result["status"] == "recorded"
    assert result["message"] == "operator_keep_position"
    assert client.closed == []
    saved = pd.read_csv(path)
    assert saved.iloc[0]["symbol"] == "FLEX"


def test_close_is_dry_run_when_submission_disabled(tmp_path: Path):
    client = FakeClient()
    result = apply_manual_position_action("FLEX", "close", config=config(submit_orders=False), client=client, output_path=tmp_path / "actions.csv")
    assert result["status"] == "dry_run"
    assert result["message"] == "manual_close_dry_run_submit_orders_disabled"
    assert client.closed == []


def test_close_submits_only_in_paper_submit_mode(tmp_path: Path):
    client = FakeClient()
    result = apply_manual_position_action("FLEX", "close", config=config(submit_orders=True), client=client, output_path=tmp_path / "actions.csv")
    assert result["status"] == "submitted"
    assert result["order_id"] == "order-1"
    assert client.closed == ["FLEX"]


def test_close_uses_overnight_limit_order_when_enabled(tmp_path: Path):
    client = FakeClient()
    result = apply_manual_position_action("FLEX", "close", config=config(submit_orders=True, overnight=True), client=client, output_path=tmp_path / "actions.csv")
    assert result["status"] == "submitted"
    assert result["message"] == "manual_close_overnight_limit_submitted"
    assert client.closed == []
    assert client.orders[0]["symbol"] == "FLEX"
    assert client.orders[0]["side"] == "sell"
    assert client.orders[0]["type"] == "limit"
    assert client.orders[0]["time_in_force"] == "day"
    assert client.orders[0]["extended_hours"] is True
    assert client.orders[0]["limit_price"] == 9.95


def test_close_falls_back_when_asset_is_not_overnight_tradable(tmp_path: Path):
    class RegularOnlyClient(FakeClient):
        def get_asset(self, symbol):
            return {"tradable": True, "status": "active", "shortable": True, "overnight_tradable": False}

    client = RegularOnlyClient()
    result = apply_manual_position_action("FLEX", "close", config=config(submit_orders=True, overnight=True), client=client, output_path=tmp_path / "actions.csv")
    assert result["status"] == "submitted"
    assert result["message"] == "manual_close_submitted"
    assert client.closed == ["FLEX"]
    assert client.orders == []


def test_close_refuses_live_trading(tmp_path: Path):
    client = FakeClient()
    result = apply_manual_position_action("FLEX", "close", config=config(submit_orders=True, live=True), client=client, output_path=tmp_path / "actions.csv")
    assert result["status"] == "rejected"
    assert result["message"] == "live_trading_disabled_for_manual_close"
    assert client.closed == []
