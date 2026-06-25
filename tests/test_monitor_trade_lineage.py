from datetime import datetime, timezone

from sqlalchemy import create_engine, insert

from stockml.db.schema import create_all, position_events
import stockml.trading.activity_journal as activity_journal
from stockml.trading.activity_journal import enrich_monitor_activity_details


def test_monitor_resolves_latest_symbol_trade_lineage(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    monkeypatch.setattr(activity_journal, "get_engine", lambda required=False: engine)
    with engine.begin() as conn:
        conn.execute(insert(position_events).values(
            position_id="position-oid-1",
            event_at=datetime(2026, 6, 24, 14, 0, tzinfo=timezone.utc),
            event_type="filled",
            source="alpaca_tracking",
            details={"symbol": "AAA", "position_id": "position-oid-1", "trade_id": "trade-oid-1", "client_order_id": "cid-1", "broker_order_id": "oid-1"},
            trade_id="trade-oid-1",
            client_order_id="cid-1",
            broker_order_id="oid-1",
        ))
    details = enrich_monitor_activity_details("AAA", {"symbol": "AAA"})
    assert details["trade_id"] == "trade-oid-1"
    assert details["position_id"] == "position-oid-1"


def test_monitor_warns_when_symbol_trade_is_ambiguous(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    monkeypatch.setattr(activity_journal, "get_engine", lambda required=False: engine)
    details = enrich_monitor_activity_details("ZZZ", {"symbol": "ZZZ"})
    assert "ambiguous_symbol_position" in details["lineage_warning"]
