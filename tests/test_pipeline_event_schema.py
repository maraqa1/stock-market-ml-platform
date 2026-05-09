import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.exc import IntegrityError

from stockml.db.schema import (
    PIPELINE_STAGE_NAMES,
    POSITION_EVENT_TYPES,
    create_all,
    metadata,
    pipeline_runs,
    pipeline_stages,
    position_events,
)


def test_pipeline_and_position_event_tables_are_registered():
    assert "pipeline_runs" in metadata.tables
    assert "pipeline_stages" in metadata.tables
    assert "position_events" in metadata.tables
    assert pipeline_runs.primary_key.columns.keys() == ["run_id"]
    assert pipeline_stages.primary_key.columns.keys() == ["run_id", "stage_name"]
    assert position_events.primary_key.columns.keys() == ["id"]


def test_pipeline_and_position_tables_create_and_empty_queries_return():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    with engine.begin() as conn:
        assert conn.execute(select(pipeline_runs)).all() == []
        assert conn.execute(select(pipeline_stages)).all() == []
        assert conn.execute(select(position_events)).all() == []
    metadata.drop_all(engine)


def test_pipeline_stage_values_are_constrained():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(pipeline_runs).values(run_id="run-1", status="running", triggered_by="test"))
        conn.execute(
            insert(pipeline_stages).values(
                run_id="run-1",
                stage_name=PIPELINE_STAGE_NAMES[0],
                status="success",
                output_count=1,
            )
        )
        with pytest.raises(IntegrityError):
            conn.execute(insert(pipeline_stages).values(run_id="run-1", stage_name="bad_stage", status="failed"))


def test_position_event_values_are_constrained_and_indexed():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    index_names = {index.name for index in position_events.indexes}
    assert "ix_position_events_position_event_at" in index_names
    assert "ix_position_events_event_at" in index_names
    with engine.begin() as conn:
        conn.execute(
            insert(position_events).values(
                position_id="paper:FLEX",
                event_type=POSITION_EVENT_TYPES[0],
                source="test",
                details={"symbol": "FLEX"},
            )
        )
        with pytest.raises(IntegrityError):
            conn.execute(
                insert(position_events).values(
                    position_id="paper:FLEX",
                    event_type="bad_event",
                    source="test",
                    details={},
                )
            )
