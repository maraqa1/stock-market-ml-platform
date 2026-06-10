from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stockml.trading.config import AlpacaConfig
from stockml.trading.overnight_close_reprice import reprice_stale_overnight_close_orders


NOW = datetime(2026, 6, 10, 12, 10, tzinfo=timezone.utc)


def config(**overrides) -> AlpacaConfig:
    values = {
        "api_key": "key",
        "secret_key": "secret",
        "base_url": "https://paper-api.alpaca.markets",
        "submit_orders": True,
        "extended_hours": False,
        "max_orders": 10,
        "max_notional_per_order": 1000,
        "max_total_notional": 10000,
        "min_trade_price": 5,
        "max_sector_fraction": 0.4,
        "min_side_probability": 0.55,
        "min_abs_probability_edge": 0.05,
        "min_intraday_volume": 100000,
        "min_market_cap": 300000000,
        "min_risk_adjusted_score": 0.005,
        "transaction_cost_bps": 10,
        "live_trading_enabled": False,
        "paper_trading_enabled": True,
        "overnight_trading_enabled": True,
        "overnight_limit_buffer_bps": 50.0,
        "overnight_close_reprice_after_minutes": 5.0,
        "overnight_close_reprice_step_bps": 50.0,
        "overnight_close_reprice_max_buffer_bps": 300.0,
    }
    values.update(overrides)
    return AlpacaConfig(**values)


class FakeClient:
    def __init__(self, *, position_qty: str = "10", order_side: str = "sell", limit_price: str = "99.50", submitted_at: str = "2026-06-10T12:00:00Z"):
        self.orders = [
            {
                "id": "order-1",
                "symbol": "VPG",
                "side": order_side,
                "type": "limit",
                "status": "new",
                "extended_hours": True,
                "limit_price": limit_price,
                "submitted_at": submitted_at,
                "client_order_id": "stockml-close-20260610120000000-VPG",
            }
        ]
        self.positions = [{"symbol": "VPG", "qty": position_qty, "current_price": "100.00", "avg_entry_price": "99.00"}]
        self.canceled = []
        self.submitted = []

    def list_positions(self):
        return self.positions

    def list_orders(self, status="open", limit=500):
        return self.orders

    def cancel_order(self, order_id):
        self.canceled.append(order_id)
        return {"id": order_id, "status": "canceled"}

    def get_asset(self, symbol):
        return {"tradable": True, "status": "active", "overnight_tradable": True}

    def submit_order(self, order):
        self.submitted.append(order)
        return {"id": "order-2", "client_order_id": order["client_order_id"], "status": "accepted"}

    def close_position(self, symbol):
        raise AssertionError("overnight repricer should not fall back to market close_position")


def test_reprices_stale_long_close_order(tmp_path: Path):
    client = FakeClient()

    result = reprice_stale_overnight_close_orders(config=config(), client=client, now=NOW, output_path=tmp_path / "actions.csv")

    assert result["overnight_reprice_status"] == "ok"
    assert result["overnight_reprice_candidates"] == 1
    assert result["overnight_reprice_attempted"] == 1
    assert result["overnight_reprice_canceled"] == 1
    assert result["overnight_reprice_submitted"] == 1
    assert client.canceled == ["order-1"]
    assert client.submitted[0]["symbol"] == "VPG"
    assert client.submitted[0]["side"] == "sell"
    assert client.submitted[0]["extended_hours"] is True
    assert client.submitted[0]["limit_price"] == 99.0
    saved = pd.read_csv(tmp_path / "actions.csv")
    assert saved.iloc[0]["message"] == "manual_close_overnight_limit_submitted"


def test_reprices_stale_short_close_order(tmp_path: Path):
    client = FakeClient(position_qty="-10", order_side="buy", limit_price="100.50")

    result = reprice_stale_overnight_close_orders(config=config(), client=client, now=NOW, output_path=tmp_path / "actions.csv")

    assert result["overnight_reprice_submitted"] == 1
    assert client.submitted[0]["side"] == "buy"
    assert client.submitted[0]["limit_price"] == 101.0


def test_skips_young_order_without_cancel_or_submit(tmp_path: Path):
    client = FakeClient(submitted_at="2026-06-10T12:08:00Z")

    result = reprice_stale_overnight_close_orders(config=config(), client=client, now=NOW, output_path=tmp_path / "actions.csv")

    assert result["overnight_reprice_attempted"] == 0
    assert result["overnight_reprice_skipped"] == 1
    assert "too_young" in result["overnight_reprice_notes"]
    assert client.canceled == []
    assert client.submitted == []


def test_skips_non_stockml_close_order(tmp_path: Path):
    client = FakeClient()
    client.orders[0]["client_order_id"] = "stockml-open-VPG"

    result = reprice_stale_overnight_close_orders(config=config(), client=client, now=NOW, output_path=tmp_path / "actions.csv")

    assert result["overnight_reprice_candidates"] == 0
    assert client.canceled == []
    assert client.submitted == []


def test_skips_order_at_max_buffer(tmp_path: Path):
    client = FakeClient(limit_price="97.00")

    result = reprice_stale_overnight_close_orders(config=config(), client=client, now=NOW, output_path=tmp_path / "actions.csv")

    assert result["overnight_reprice_attempted"] == 0
    assert result["overnight_reprice_skipped"] == 1
    assert "max_buffer_reached" in result["overnight_reprice_notes"]
    assert client.canceled == []
    assert client.submitted == []


def test_refuses_live_trading_mode(tmp_path: Path):
    client = FakeClient()

    result = reprice_stale_overnight_close_orders(config=config(live_trading_enabled=True), client=client, now=NOW, output_path=tmp_path / "actions.csv")

    assert result["overnight_reprice_status"] == "skipped"
    assert result["overnight_reprice_notes"] == "live_trading_disabled_for_overnight_reprice"
    assert client.canceled == []
    assert client.submitted == []
