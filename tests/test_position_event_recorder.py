from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError

from stockml.db.schema import create_all, position_events
from stockml.services.events import position_id_for_symbol, record_event, record_event_safely


def engine_with_schema():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    return engine


def test_record_event_writes_position_timeline_row():
    engine = engine_with_schema()

    event_id = record_event(
        position_id_for_symbol("flex"),
        "submitted",
        "paper_trader",
        {"symbol": "FLEX", "order_id": "order-1", "qty": 5},
        target=engine,
    )

    with engine.connect() as conn:
        row = conn.execute(select(position_events)).mappings().one()

    assert event_id == 1
    assert row["position_id"] == "paper:FLEX"
    assert row["event_type"] == "submitted"
    assert row["source"] == "paper_trader"
    assert row["details"]["order_id"] == "order-1"


def test_record_event_rejects_unknown_event_type():
    engine = engine_with_schema()

    try:
        record_event("paper:FLEX", "not_real", "test", {}, target=engine)
    except ValueError as exc:
        assert "Unknown position event type" in str(exc)
    else:
        raise AssertionError("Expected invalid event type to fail before insert")

    with engine.connect() as conn:
        assert conn.execute(select(position_events)).all() == []


def test_database_constraint_still_blocks_invalid_event_type():
    engine = engine_with_schema()

    with engine.begin() as conn:
        try:
            conn.execute(
                position_events.insert().values(
                    position_id="paper:FLEX",
                    event_type="bad_event",
                    source="test",
                    details={},
                )
            )
        except IntegrityError:
            pass
        else:
            raise AssertionError("Expected DB constraint to reject invalid event type")


def test_record_event_safely_returns_false_when_database_unavailable(monkeypatch):
    monkeypatch.setattr("stockml.services.events.get_engine", lambda required=False: None)

    assert record_event_safely("paper:FLEX", "monitor_safe", "test", {"symbol": "FLEX"}) is False


def test_json_details_are_sanitized_for_non_finite_values():
    engine = engine_with_schema()

    record_event(
        "paper:FLEX",
        "filled",
        "alpaca_tracking",
        {"symbol": "FLEX", "filled_avg_price": float("nan"), "nested": {"bad": float("inf")}},
        target=engine,
    )

    with engine.connect() as conn:
        details = conn.execute(select(position_events.c.details)).scalar_one()

    assert details["filled_avg_price"] is None
    assert details["nested"]["bad"] is None
