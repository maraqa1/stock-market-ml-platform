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
