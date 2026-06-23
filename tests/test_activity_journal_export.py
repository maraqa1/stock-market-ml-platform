from __future__ import annotations

import json
from io import StringIO
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import create_engine

from portal.services.journal import JournalFilters, iter_csv
from stockml.db.schema import create_all, position_events
from stockml.trading.activity_journal_export import export_activity_journal, request_for_date


DAY = date(2026, 6, 23)


def _engine_with_events(row_count: int = 520):
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    start = datetime(2026, 6, 23, 9, 30, tzinfo=timezone.utc)
    rows = []
    for index in range(row_count):
        symbol = "AAA" if index % 2 == 0 else "BBB"
        rows.append(
            {
                "position_id": f"paper:{symbol}",
                "event_at": start + timedelta(seconds=index),
                "event_type": "filled" if index % 3 == 0 else "selected",
                "source": "broker" if index % 3 == 0 else "pipeline",
                "details": {"symbol": symbol, "qty": index + 1, "avg_price": 10.0, "order_id": f"ord-{index}"},
            }
        )
    with engine.begin() as conn:
        conn.execute(position_events.insert(), rows)
    return engine


def _request(**kwargs):
    return request_for_date(DAY, batch_size=50, **kwargs)


def test_more_than_500_fixture_rows_are_all_exported(tmp_path):
    result = export_activity_journal(_request(), tmp_path, target=_engine_with_events(525))
    frame = pd.read_csv(result.csv_path)
    assert len(frame) == 525
    assert result.metadata["total_rows"] == 525
    assert result.metadata["was_truncated"] is False


def test_no_fixed_limit_truncates_explicit_export(tmp_path):
    result = export_activity_journal(_request(), tmp_path, target=_engine_with_events(701))
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["total_rows"] == 701
    assert metadata["was_truncated"] is False


def test_date_filter_is_correct(tmp_path):
    engine = _engine_with_events(3)
    with engine.begin() as conn:
        conn.execute(
            position_events.insert(),
            {
                "position_id": "paper:CCC",
                "event_at": datetime(2026, 6, 24, 0, 0, tzinfo=timezone.utc),
                "event_type": "selected",
                "source": "pipeline",
                "details": {"symbol": "CCC"},
            },
        )
    result = export_activity_journal(_request(), tmp_path, target=engine)
    frame = pd.read_csv(result.csv_path)
    assert len(frame) == 3
    assert "CCC" not in set(frame["symbol"])


def test_source_and_event_type_filters_work(tmp_path):
    result = export_activity_journal(_request(sources=["broker"], event_types=["filled"]), tmp_path, target=_engine_with_events(12))
    frame = pd.read_csv(result.csv_path)
    assert set(frame["source"]) == {"broker"}
    assert set(frame["event_type"]) == {"filled"}


def test_rows_are_deterministically_ordered(tmp_path):
    result = export_activity_journal(_request(), tmp_path, target=_engine_with_events(40))
    frame = pd.read_csv(result.csv_path)
    assert frame[["event_at", "id"]].values.tolist() == frame.sort_values(["event_at", "id"])[["event_at", "id"]].values.tolist()


def test_portal_csv_export_pages_until_exhaustion():
    filters = JournalFilters(from_date=DAY, to_date=DAY, event_types=[], sources=[], symbol="")
    csv_text = "".join(iter_csv(filters, target=_engine_with_events(610)))
    frame = pd.read_csv(StringIO(csv_text))
    assert len(frame) == 610


def test_empty_range_produces_valid_empty_export(tmp_path):
    result = export_activity_journal(_request(symbol="ZZZ"), tmp_path, target=_engine_with_events(10))
    frame = pd.read_csv(result.csv_path)
    assert frame.empty
    assert list(frame.columns) == ["id", "event_at", "symbol", "event_type", "source", "details_summary", "position_id"]
    assert result.metadata["total_rows"] == 0
    assert result.metadata["was_truncated"] is False
