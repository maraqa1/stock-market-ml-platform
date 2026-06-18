from datetime import datetime, timezone

from sqlalchemy import create_engine, select

from stockml.db.schema import create_all, position_events
from stockml.services import events
from stockml.services.events import position_id_for_symbol, record_event_once


def _engine(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    monkeypatch.setattr(events, "get_engine", lambda required=False: engine)
    return engine


def test_duplicate_selected_candidate_in_same_cycle_is_skipped(monkeypatch):
    engine = _engine(monkeypatch)
    key = "cycle1:AAA:paper_order_plan:selected"
    details = {"event_key": key, "cycle_id": "cycle1", "symbol": "AAA", "candidate_source": "paper_order_plan", "action": "selected"}
    assert record_event_once(position_id_for_symbol("AAA"), "selected", "paper_order_plan", details, event_key=key)
    assert not record_event_once(position_id_for_symbol("AAA"), "selected", "paper_order_plan", details, event_key=key)
    with engine.connect() as conn:
        rows = conn.execute(select(position_events)).all()
    assert len(rows) == 1


def test_later_cycle_selected_candidate_is_logged(monkeypatch):
    engine = _engine(monkeypatch)
    first = "cycle1:AAA:paper_order_plan:selected"
    second = "cycle2:AAA:paper_order_plan:selected"
    assert record_event_once(position_id_for_symbol("AAA"), "selected", "paper_order_plan", {"event_key": first}, event_key=first)
    assert record_event_once(position_id_for_symbol("AAA"), "selected", "paper_order_plan", {"event_key": second}, event_key=second)
    with engine.connect() as conn:
        assert len(conn.execute(select(position_events)).all()) == 2

import pandas as pd

from stockml.trading import paper_trader


class TrackingClient:
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


class TrackingConfig:
    api_key = "key"
    secret_key = "secret"
    base_url = "paper"


def test_same_broker_filled_order_logged_repeatedly_creates_one_activity_row(monkeypatch, tmp_path):
    engine = _engine(monkeypatch)
    monkeypatch.setattr(paper_trader, "get_engine", lambda required=False: engine)
    monkeypatch.setattr(paper_trader, "AlpacaPaperClient", TrackingClient)
    monkeypatch.setattr(paper_trader, "PORTAL_OUTPUTS_DIR", tmp_path)
    results = pd.DataFrame([{"symbol": "AAA", "side": "buy", "order_id": "ord-1", "status": "submitted", "client_order_id": "cid-1", "suggested_quantity": 10, "type": "limit", "extended_hours": True}])
    paper_trader._write_tracking_snapshot(results, TrackingConfig(), "one")
    paper_trader._write_tracking_snapshot(results, TrackingConfig(), "two")
    with engine.connect() as conn:
        rows = conn.execute(select(position_events).where(position_events.c.event_type == "filled")).mappings().all()
    assert len(rows) == 1
    assert rows[0]["details"]["broker_order_id"] == "ord-1"


def test_historical_filled_order_repeated_by_tracker_is_not_duplicated(monkeypatch, tmp_path):
    engine = _engine(monkeypatch)
    monkeypatch.setattr(paper_trader, "get_engine", lambda required=False: engine)
    monkeypatch.setattr(paper_trader, "AlpacaPaperClient", TrackingClient)
    monkeypatch.setattr(paper_trader, "PORTAL_OUTPUTS_DIR", tmp_path)
    with engine.begin() as conn:
        conn.execute(position_events.insert().values(
            position_id="paper:AAA",
            event_type="filled",
            source="alpaca_tracking",
            details={"event_key": "legacy-key", "broker_order_id": "ord-1", "status": "filled", "filled_qty": "10", "filled_avg_price": "12.34"},
        ))
    results = pd.DataFrame([{"symbol": "AAA", "side": "buy", "order_id": "ord-1", "status": "submitted", "client_order_id": "cid-1", "suggested_quantity": 10, "type": "limit", "extended_hours": True}])
    paper_trader._write_tracking_snapshot(results, TrackingConfig(), "three")
    with engine.connect() as conn:
        rows = conn.execute(select(position_events).where(position_events.c.event_type == "filled")).mappings().all()
    assert len(rows) == 1
