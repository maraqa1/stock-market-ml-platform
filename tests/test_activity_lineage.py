from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, select

from stockml.db.schema import create_all, position_events
from stockml.services import events
from stockml.services.events import position_id_for_symbol, record_event_once
from stockml.trading.activity_journal import enrich_activity_details, lineage_from_activity
from stockml.trading.lifecycle_ids import candidate_lineage, fill_lineage
from stockml.trading.trade_journal import build_trade_journal


def _engine(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    monkeypatch.setattr(events, "get_engine", lambda required=False: engine)
    return engine


def test_activity_details_preserve_lineage_fields(monkeypatch):
    engine = _engine(monkeypatch)
    lineage = candidate_lineage(symbol="AAA", cycle_id="cycle-1", pipeline_run_id="run-1", candidate_source="scan", model_version="model-a", side="buy", client_order_id="cid-1")
    payload = enrich_activity_details({"symbol": "AAA", "action": "selected"}, lineage)
    assert record_event_once(position_id_for_symbol("AAA"), "selected", "paper_order_plan", payload, event_key=payload["event_key"], event_at=datetime(2026, 6, 23, tzinfo=timezone.utc))
    with engine.connect() as conn:
        row = conn.execute(select(position_events.c.details)).scalar_one()
    assert row["candidate_id"] == lineage.values["candidate_id"]
    assert row["client_order_id"] == "cid-1"
    assert lineage_from_activity(row)["cycle_id"] == "cycle-1"


def test_candidate_block_can_reuse_candidate_id(monkeypatch):
    engine = _engine(monkeypatch)
    lineage = candidate_lineage(symbol="AAA", cycle_id="cycle-1", candidate_source="scan", side="buy")
    first = enrich_activity_details({"symbol": "AAA", "reason": "scan"}, lineage)
    second = enrich_activity_details({"symbol": "AAA", "reason": "blocked"}, lineage)
    assert first["candidate_id"] == second["candidate_id"]
    with engine.begin() as conn:
        conn.execute(position_events.insert(), [
            {"position_id": "paper:AAA", "event_at": datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc), "event_type": "selected", "source": "scan", "details": first},
            {"position_id": "paper:AAA", "event_at": datetime(2026, 6, 23, 10, 1, tzinfo=timezone.utc), "event_type": "guardrail_blocked", "source": "scan", "details": second},
        ])
    with engine.connect() as conn:
        rows = conn.execute(select(position_events.c.details).order_by(position_events.c.id)).scalars().all()
    assert rows[0]["candidate_id"] == rows[1]["candidate_id"]


def test_fill_lineage_maps_to_position_and_trade():
    lineage = fill_lineage({"symbol": "AAA", "client_order_id": "cid-1", "order_id": "oid-1", "order_intent": "open_long"})
    assert lineage.values["position_id"] == "position-oid-1"
    assert lineage.values["trade_id"]
    assert lineage.values["lineage_warning"] == ""


def test_trade_journal_carries_lineage_columns():
    plan = pd.DataFrame([
        {
            "symbol": "AAA",
            "trade_quality_status": "approved",
            "status": "submitted",
            "client_order_id": "cid-1",
            "candidate_id": "cand-1",
            "trade_id": "trade-1",
            "order_intent": "open_long",
        }
    ])
    journal = build_trade_journal(plan)
    assert journal.iloc[0]["candidate_id"] == "cand-1"
    assert journal.iloc[0]["client_order_id"] == "cid-1"
    assert journal.iloc[0]["trade_id"] == "trade-1"
