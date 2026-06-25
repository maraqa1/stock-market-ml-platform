from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, select

from stockml.db.schema import create_all, position_events
from stockml.trading.activity_journal_export import request_for_date


def _load_script():
    path = Path("scripts/deduplicate_activity_journal.py")
    spec = importlib.util.spec_from_file_location("deduplicate_activity_journal", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    base = datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc)
    rows = [
        {"position_id": "paper:AAA", "event_at": base, "event_type": "filled", "source": "test", "details": {"symbol": "AAA", "broker_order_id": "oid-1", "filled_qty": "10", "filled_avg_price": "12.3", "status": "filled"}, "broker_order_id": "oid-1"},
        {"position_id": "paper:AAA", "event_at": base + timedelta(seconds=1), "event_type": "filled", "source": "test", "details": {"symbol": "AAA", "broker_order_id": "oid-1", "filled_qty": "10", "filled_avg_price": "12.3", "status": "filled"}, "broker_order_id": "oid-1"},
        {"position_id": "paper:AAA", "event_at": base + timedelta(seconds=2), "event_type": "filled", "source": "test", "details": {"symbol": "AAA", "broker_order_id": "oid-1", "filled_qty": "10", "filled_avg_price": "12.3", "status": "partial"}, "broker_order_id": "oid-1"},
    ]
    with engine.begin() as conn:
        conn.execute(position_events.insert(), rows)
    return engine


def test_duplicate_historical_fills_are_detected():
    module = _load_script()
    rows = module.duplicate_fill_report_rows(request_for_date(date(2026, 6, 23)), target=_engine())
    assert sum(1 for row in rows if row["is_duplicate"]) == 1


def test_cleanup_retains_earliest_fill_and_preserves_distinct_status(tmp_path):
    module = _load_script()
    engine = _engine()
    result = module.write_duplicate_fill_report(request_for_date(date(2026, 6, 23)), tmp_path, target=engine, apply=True)
    assert result["removed_rows"] == 1
    with engine.connect() as conn:
        rows = conn.execute(select(position_events).order_by(position_events.c.id)).mappings().all()
    assert len(rows) == 2
    statuses = [row["details"]["status"] for row in rows]
    assert statuses == ["filled", "partial"]
    assert result["backup_path"].exists()


def test_duplicate_key_preserves_distinct_status_transitions(monkeypatch):
    from datetime import datetime, timezone
    from sqlalchemy import create_engine, insert
    from stockml.db.schema import create_all, position_events
    from stockml.trading.activity_journal_export import request_for_date
    from scripts.deduplicate_activity_journal import duplicate_fill_report_rows

    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    with engine.begin() as conn:
        for event_id, status in [(1, "partial_fill"), (2, "filled")]:
            conn.execute(insert(position_events).values(
                position_id="position-oid-1",
                event_at=datetime(2026, 6, 24, 12, event_id, tzinfo=timezone.utc),
                event_type="filled",
                source="alpaca_tracking",
                details={"symbol": "AAA", "broker_order_id": "oid-1", "filled_qty": "10", "filled_avg_price": "12.34", "status": status},
            ))
    rows = duplicate_fill_report_rows(request_for_date(datetime(2026, 6, 24, tzinfo=timezone.utc).date()), target=engine)
    assert len(rows) == 2
    assert not any(row["is_duplicate"] for row in rows)
