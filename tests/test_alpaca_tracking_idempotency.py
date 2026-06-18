from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, select

from stockml.db.schema import create_all, position_events
from stockml.services import events
from stockml.trading import paper_trader
from stockml.trading.config import AlpacaConfig


def _config():
    return AlpacaConfig(
        api_key="key",
        secret_key="secret",
        base_url="paper",
        submit_orders=True,
        extended_hours=True,
        max_orders=20,
        max_notional_per_order=5000.0,
        max_total_notional=50000.0,
        min_trade_price=1.0,
        max_sector_fraction=1.0,
        min_side_probability=0.0,
        min_abs_probability_edge=0.0,
        min_intraday_volume=0,
        min_market_cap=0.0,
        min_risk_adjusted_score=-1.0,
        transaction_cost_bps=10.0,
        paper_trading_enabled=True,
        live_trading_enabled=False,
    )


class FakeClient:
    def __init__(self, cfg):
        pass

    def get_order(self, order_id):
        return {
            "id": order_id,
            "status": "filled",
            "filled_qty": "10",
            "filled_avg_price": "12.34",
            "submitted_at": "2026-06-18T10:00:00Z",
            "updated_at": "2026-06-18T10:01:00Z",
        }

    def list_positions(self):
        return []


def test_same_broker_filled_order_logged_twice_creates_one_activity_row(monkeypatch, tmp_path: Path):
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    monkeypatch.setattr(events, "get_engine", lambda required=False: engine)
    monkeypatch.setattr(paper_trader, "get_engine", lambda required=False: engine)
    monkeypatch.setattr(paper_trader, "AlpacaPaperClient", FakeClient)
    monkeypatch.setattr(paper_trader, "PORTAL_OUTPUTS_DIR", tmp_path)
    results = pd.DataFrame([{"symbol": "AAA", "side": "buy", "order_id": "ord-1", "status": "submitted", "client_order_id": "cid-1", "suggested_quantity": 10, "type": "limit", "extended_hours": True}])
    paper_trader._write_tracking_snapshot(results, _config(), "one")
    paper_trader._write_tracking_snapshot(results, _config(), "two")
    with engine.connect() as conn:
        rows = conn.execute(select(position_events).where(position_events.c.event_type == "filled")).mappings().all()
    assert len(rows) == 1
    details = rows[0]["details"]
    assert details["broker_order_id"] == "ord-1"
    assert details["details_summary"] == "buy 10 AAA filled @ 12.34 · ord-1"


def test_historical_filled_order_with_old_event_key_is_not_duplicated(monkeypatch, tmp_path: Path):
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    monkeypatch.setattr(events, "get_engine", lambda required=False: engine)
    monkeypatch.setattr(paper_trader, "get_engine", lambda required=False: engine)
    monkeypatch.setattr(paper_trader, "AlpacaPaperClient", FakeClient)
    monkeypatch.setattr(paper_trader, "PORTAL_OUTPUTS_DIR", tmp_path)
    # Simulate a pre-fix event whose event_key differed, but broker fill signature is identical.
    with engine.begin() as conn:
        conn.execute(position_events.insert().values(
            position_id="paper:AAA",
            event_type="filled",
            source="alpaca_tracking",
            details={
                "event_key": "old-key",
                "broker_order_id": "ord-1",
                "status": "filled",
                "filled_qty": "10",
                "filled_avg_price": "12.34",
            },
        ))
    results = pd.DataFrame([{
        "symbol": "AAA", "side": "buy", "order_id": "ord-1", "status": "submitted",
        "client_order_id": "cid-1", "suggested_quantity": 10, "type": "limit", "extended_hours": True,
    }])
    paper_trader._write_tracking_snapshot(results, _config(), "three")
    with engine.connect() as conn:
        rows = conn.execute(select(position_events).where(position_events.c.event_type == "filled")).mappings().all()
    assert len(rows) == 1
