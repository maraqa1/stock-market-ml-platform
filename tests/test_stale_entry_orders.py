from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stockml.trading.config import AlpacaConfig
from stockml.trading.stale_entry_orders import cancel_stale_entry_orders


def _config(**overrides) -> AlpacaConfig:
    values = dict(
        api_key="paper-key",
        secret_key="paper-secret",
        base_url="https://paper-api.alpaca.markets",
        submit_orders=True,
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
        stale_entry_cancel_enabled=True,
        stale_entry_cancel_after_minutes=15,
    )
    values.update(overrides)
    return AlpacaConfig(**values)


class FakeClient:
    def __init__(self, orders: list[dict]):
        self.orders = orders
        self.canceled: list[str] = []

    def list_orders(self, status="open", limit=500):
        return self.orders

    def cancel_order(self, order_id: str):
        self.canceled.append(order_id)
        return {"id": order_id, "status": "canceled"}


def test_cancel_stale_entry_orders_cancels_only_old_unfilled_stockml_entries(tmp_path: Path):
    now = datetime(2026, 6, 11, 16, 0, tzinfo=timezone.utc)
    client = FakeClient(
        [
            {
                "id": "old-entry",
                "symbol": "AAA",
                "side": "buy",
                "status": "new",
                "client_order_id": "stockml-20260611-AAA-buy",
                "submitted_at": "2026-06-11T15:30:00Z",
                "filled_qty": "0",
                "limit_price": "10.00",
            },
            {
                "id": "close-order",
                "symbol": "AAA",
                "side": "sell",
                "status": "new",
                "client_order_id": "stockml-close-20260611-AAA",
                "submitted_at": "2026-06-11T15:30:00Z",
                "filled_qty": "0",
                "limit_price": "9.90",
            },
            {
                "id": "partial-entry",
                "symbol": "BBB",
                "side": "buy",
                "status": "new",
                "client_order_id": "stockml-20260611-BBB-buy",
                "submitted_at": "2026-06-11T15:30:00Z",
                "filled_qty": "1",
                "limit_price": "20.00",
            },
            {
                "id": "fresh-entry",
                "symbol": "CCC",
                "side": "buy",
                "status": "new",
                "client_order_id": "stockml-20260611-CCC-buy",
                "submitted_at": "2026-06-11T15:55:00Z",
                "filled_qty": "0",
                "limit_price": "30.00",
            },
        ]
    )

    result = cancel_stale_entry_orders(
        config=_config(),
        client=client,
        now=now,
        output_path=tmp_path / "stale_entries.csv",
    )

    assert result["stale_entry_status"] == "ok"
    assert result["stale_entry_candidates"] == 3
    assert result["stale_entry_canceled"] == 1
    assert client.canceled == ["old-entry"]
    audit = pd.read_csv(result["stale_entry_path"])
    reasons = set(audit["reason"])
    assert "stale_unfilled_entry_order" in reasons
    assert "not_stockml_entry_order" in reasons
    assert "partially_filled" in reasons
    assert "too_young" in reasons


def test_cancel_stale_entry_orders_refuses_live_trading():
    result = cancel_stale_entry_orders(config=_config(live_trading_enabled=True), client=FakeClient([]))

    assert result["stale_entry_status"] == "skipped"
    assert result["stale_entry_notes"] == "live_trading_disabled_for_stale_entry_cancel"


def test_cancel_stale_entry_orders_ages_missing_submitted_time_from_client_order_id(tmp_path: Path):
    now = datetime(2026, 6, 11, 16, 0, tzinfo=timezone.utc)
    client = FakeClient(
        [
            {
                "id": "missing-submitted-time",
                "symbol": "AAA",
                "side": "buy",
                "status": "new",
                "client_order_id": "stockml-20260610-AAA-buy-20260611152557",
                "filled_qty": "0",
                "limit_price": "10.00",
            }
        ]
    )

    result = cancel_stale_entry_orders(
        config=_config(),
        client=client,
        now=now,
        output_path=tmp_path / "stale_entries.csv",
    )

    assert result["stale_entry_canceled"] == 1
    assert client.canceled == ["missing-submitted-time"]
    audit = pd.read_csv(result["stale_entry_path"])
    assert audit.iloc[0]["age_minutes"] > 30
    assert audit.iloc[0]["reason"] == "stale_unfilled_entry_order"
