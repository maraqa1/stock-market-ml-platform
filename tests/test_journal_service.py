from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4
import shutil

import pandas as pd
from sqlalchemy import create_engine

from portal.services.journal import JournalFilters, iter_csv, query
from stockml.db.schema import create_all, position_events


def engine_with_events():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            position_events.insert(),
            [
                {
                    "position_id": "paper:AAA",
                    "event_at": datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
                    "event_type": "selected",
                    "source": "pipeline",
                    "details": {"symbol": "AAA", "basket_pos": 1, "basket_size": 2, "run_id": "2026-05-01-A"},
                },
                {
                    "position_id": "paper:AAA",
                    "event_at": datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc),
                    "event_type": "filled",
                    "source": "broker",
                    "details": {"symbol": "AAA", "qty": 2, "avg_price": 25.5, "order_id": "ord-1"},
                },
                {
                    "position_id": "paper:BBB",
                    "event_at": datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc),
                    "event_type": "monitor_close",
                    "source": "monitor",
                    "details": {"symbol": "BBB", "reason": "stop-loss breach"},
                },
            ],
        )
    return engine


def filters(**kwargs):
    data = {
        "from_date": date(2026, 5, 1),
        "to_date": date(2026, 5, 2),
        "event_types": [],
        "sources": [],
        "symbol": "",
    }
    data.update(kwargs)
    return JournalFilters(**data)


def test_journal_query_returns_date_range_count():
    payload = query(filters(), target=engine_with_events())
    assert payload["total_in_range"] == 3
    assert payload["events"][0]["symbol"] == "BBB"
    assert payload["events"][0]["details_summary"] == "stop-loss breach"


def test_journal_cursor_pagination_has_no_duplicates():
    engine = engine_with_events()
    first = query(filters(), limit=2, target=engine)
    second = query(filters(), cursor=first["next_cursor"], limit=2, target=engine)
    first_ids = {event["id"] for event in first["events"]}
    second_ids = {event["id"] for event in second["events"]}
    assert first["next_cursor"]
    assert first_ids.isdisjoint(second_ids)
    assert len(first_ids | second_ids) == 3


def test_journal_filters_by_symbol_and_csv_count_matches():
    engine = engine_with_events()
    selected = filters(symbol="AAA")
    payload = query(selected, target=engine)
    csv_text = "".join(iter_csv(selected, target=engine))
    assert payload["total_in_range"] == 2
    assert csv_text.count("\n") == 3
    assert "ord-1" in csv_text


def test_journal_falls_back_to_artifacts_when_event_table_missing():
    root = Path(".pytest_workspace") / f"journal_{uuid4().hex}"
    try:
        path = root / "data" / "trading" / "paper_trade_journal" / "paper_trade_journal_1.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"symbol": "AAA", "lifecycle_state": "submitted", "order_id": "ord-1"}]).to_csv(path, index=False)
        payload = query(filters(from_date=date.today().replace(year=2026), to_date=date.today().replace(year=2026)), root=root)
        # Use a wide date range because the artifact timestamp is the local test runtime.
        payload = query(filters(from_date=date(2000, 1, 1), to_date=date(2100, 1, 1)), root=root)
        assert payload["total_in_range"] == 1
        assert payload["events"][0]["symbol"] == "AAA"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_journal_falls_back_to_artifacts_when_event_table_is_empty():
    root = Path(".pytest_workspace") / f"journal_{uuid4().hex}"
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    try:
        path = root / "data" / "trading" / "paper_trade_journal" / "paper_trade_journal_1.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"symbol": "AAA", "lifecycle_state": "submitted", "order_id": "ord-1"}]).to_csv(path, index=False)
        payload = query(filters(from_date=date(2000, 1, 1), to_date=date(2100, 1, 1)), target=engine, root=root)
        assert payload["total_in_range"] == 1
        assert payload["events"][0]["source"] == "trade_journal"
    finally:
        shutil.rmtree(root, ignore_errors=True)
