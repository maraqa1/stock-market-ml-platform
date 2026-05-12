import pytest
from pathlib import Path
from sqlalchemy import inspect
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
    shortlist_snapshots,
    model_feature_importance,
    model_folds,
    model_runs,
    kill_switch_events,
    intraday_decisions,
    promotion_dry_runs,
    promotion_evaluations,
    shadow_outcomes,
    shadow_would_trades,
    eod_flatten_log,
    eod_summary,
    intraday_candidate_snapshots,
    intraday_promotion_log,
    rotation_recommendation_log,
    autopilot_open_log,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_UP = PROJECT_ROOT / "migrations" / "001_pipeline_position_events_up.sql"
MIGRATION_DOWN = PROJECT_ROOT / "migrations" / "001_pipeline_position_events_down.sql"


def test_pipeline_and_position_event_tables_are_registered():
    assert "pipeline_runs" in metadata.tables
    assert "pipeline_stages" in metadata.tables
    assert "position_events" in metadata.tables
    assert "shortlist_snapshots" in metadata.tables
    assert "model_runs" in metadata.tables
    assert "model_folds" in metadata.tables
    assert "model_feature_importance" in metadata.tables
    assert "kill_switch_events" in metadata.tables
    assert "intraday_decisions" in metadata.tables
    assert "shadow_would_trades" in metadata.tables
    assert "shadow_outcomes" in metadata.tables
    assert "promotion_evaluations" in metadata.tables
    assert "promotion_dry_runs" in metadata.tables
    assert "eod_flatten_log" in metadata.tables
    assert "eod_summary" in metadata.tables
    assert "intraday_candidate_snapshots" in metadata.tables
    assert "intraday_promotion_log" in metadata.tables
    assert "rotation_recommendation_log" in metadata.tables
    assert "autopilot_open_log" in metadata.tables
    assert pipeline_runs.primary_key.columns.keys() == ["run_id"]
    assert pipeline_stages.primary_key.columns.keys() == ["run_id", "stage_name"]
    assert position_events.primary_key.columns.keys() == ["id"]
    assert shortlist_snapshots.primary_key.columns.keys() == ["run_id", "symbol"]
    assert model_runs.primary_key.columns.keys() == ["model_version"]
    assert model_folds.primary_key.columns.keys() == ["model_version", "period"]
    assert model_feature_importance.primary_key.columns.keys() == ["model_version", "feature_name"]
    assert kill_switch_events.primary_key.columns.keys() == ["id"]
    assert intraday_decisions.primary_key.columns.keys() == ["id"]
    assert shadow_would_trades.primary_key.columns.keys() == ["id"]
    assert shadow_outcomes.primary_key.columns.keys() == ["would_trade_id"]
    assert promotion_evaluations.primary_key.columns.keys() == ["evaluated_at"]
    assert promotion_dry_runs.primary_key.columns.keys() == ["id"]
    assert eod_flatten_log.primary_key.columns.keys() == ["id"]
    assert eod_summary.primary_key.columns.keys() == ["session_date"]
    assert intraday_candidate_snapshots.primary_key.columns.keys() == ["id"]
    assert intraday_promotion_log.primary_key.columns.keys() == ["id"]
    assert rotation_recommendation_log.primary_key.columns.keys() == ["id"]
    assert autopilot_open_log.primary_key.columns.keys() == ["id"]


def test_pipeline_and_position_tables_create_query_and_drop_self_contained():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    inspector = inspect(engine)
    assert {"pipeline_runs", "pipeline_stages", "position_events", "shortlist_snapshots"}.issubset(set(inspector.get_table_names()))
    with engine.begin() as conn:
        assert conn.execute(select(pipeline_runs)).all() == []
        assert conn.execute(select(pipeline_stages)).all() == []
        assert conn.execute(select(position_events)).all() == []
        assert conn.execute(select(shortlist_snapshots)).all() == []
    metadata.drop_all(engine)
    assert not {"pipeline_runs", "pipeline_stages", "position_events", "shortlist_snapshots"}.intersection(set(inspect(engine).get_table_names()))


def test_pipeline_run_and_stage_happy_path_insert_shape():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(pipeline_runs).values(run_id="run-1", status="running", current_stage="yahoo", triggered_by="pytest"))
        conn.execute(
            insert(pipeline_stages).values(
                run_id="run-1",
                stage_name="yahoo",
                status="success",
                output_count=500,
                output_metadata={"file": "universe.csv"},
            )
        )
        run = conn.execute(select(pipeline_runs.c.run_id, pipeline_runs.c.current_stage)).one()
        stage = conn.execute(select(pipeline_stages.c.stage_name, pipeline_stages.c.output_count)).one()
    assert run == ("run-1", "yahoo")
    assert stage == ("yahoo", 500)


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


def test_migration_files_are_self_contained_and_reversible():
    up_sql = MIGRATION_UP.read_text(encoding="utf-8")
    down_sql = MIGRATION_DOWN.read_text(encoding="utf-8")

    for table in ["pipeline_runs", "pipeline_stages", "position_events"]:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in up_sql
        assert f"DROP TABLE IF EXISTS {table}" in down_sql

    for index in ["ix_position_events_position_event_at", "ix_position_events_event_at"]:
        assert f"CREATE INDEX IF NOT EXISTS {index}" in up_sql
        assert f"DROP INDEX IF EXISTS {index}" in down_sql

    for stage_name in PIPELINE_STAGE_NAMES:
        assert f"'{stage_name}'" in up_sql

    for event_type in POSITION_EVENT_TYPES:
        assert f"'{event_type}'" in up_sql
