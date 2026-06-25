from datetime import datetime, timezone

from sqlalchemy import create_engine, insert

from stockml.db.schema import create_all, position_events
from stockml.trading.activity_journal_export import iter_activity_journal_rows, request_for_range


def test_export_uses_event_timestamp_for_event_session_mode():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(position_events).values(
            position_id="paper:AAA",
            event_at=datetime(2026, 6, 24, 15, 0, tzinfo=timezone.utc),
            event_type="candidate_scanned",
            source="paper_autopilot",
            details={"symbol": "AAA", "cycle_id": "cycle-1", "candidate_id": "cand-1", "session_mode": "overnight_24_5"},
        ))
    rows = list(iter_activity_journal_rows(request_for_range(datetime(2026, 6, 24, tzinfo=timezone.utc), datetime(2026, 6, 25, tzinfo=timezone.utc)), target=engine))
    assert rows[0]["event_session_mode"] == "regular_session"
    assert rows[0]["session_mode"] == "overnight_24_5"
