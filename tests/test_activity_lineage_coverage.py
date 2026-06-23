from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine

from stockml.db.schema import create_all, position_events
from stockml.trading.activity_journal_export import request_for_date


def _load_script():
    path = Path("scripts/diagnose_activity_lineage.py")
    spec = importlib.util.spec_from_file_location("diagnose_activity_lineage", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    with engine.begin() as conn:
        conn.execute(position_events.insert(), {
            "position_id": "paper:AAA",
            "event_at": datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc),
            "event_type": "selected",
            "source": "test",
            "details": {"symbol": "AAA"},
            "cycle_id": "cycle-1",
            "candidate_id": "cand-1",
            "event_key": "evt-1",
            "session_mode": "regular_session",
        })
        conn.execute(position_events.insert(), {
            "position_id": "paper:BBB",
            "event_at": datetime(2026, 6, 23, 10, 1, tzinfo=timezone.utc),
            "event_type": "filled",
            "source": "test",
            "details": {"symbol": "BBB"},
            "client_order_id": "cid-1",
            "broker_order_id": "oid-1",
            "trade_id": "trade-oid-1",
            "order_intent": "open_long",
            "session_mode": "regular_session",
        })
    return engine


def test_activity_lineage_coverage_reports_by_event_type(tmp_path):
    module = _load_script()
    request = request_for_date(date(2026, 6, 23))
    frame = module.build_lineage_coverage(request, target=_engine())
    assert set(frame["event_type"]) == {"filled", "selected"}
    filled = frame[frame["event_type"].eq("filled")].iloc[0]
    assert filled["client_order_id_coverage"] == 1.0
    assert filled["trade_id_coverage"] == 1.0
