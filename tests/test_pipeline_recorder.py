from sqlalchemy import create_engine, select

from stockml.db.schema import create_all, pipeline_runs, pipeline_stages
from stockml.pipeline.recorder import (
    complete_run,
    complete_stage,
    fail_stage,
    skip_stage,
    start_run,
    start_stage,
)


def engine_with_schema():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    return engine


def stage_row(engine, run_id, stage_name):
    with engine.connect() as conn:
        return (
            conn.execute(
                select(pipeline_stages).where(
                    pipeline_stages.c.run_id == run_id,
                    pipeline_stages.c.stage_name == stage_name,
                )
            )
            .mappings()
            .one()
        )


def run_row(engine, run_id):
    with engine.connect() as conn:
        return conn.execute(select(pipeline_runs).where(pipeline_runs.c.run_id == run_id)).mappings().one()


def test_recorder_happy_path_records_run_and_stage_states():
    engine = engine_with_schema()

    start_run("run-1", triggered_by="nightly", target=engine)
    start_stage("run-1", "yahoo", target=engine)
    complete_stage("run-1", "yahoo", output_count=500, metadata={"file": "universe.csv"}, target=engine)
    complete_run("run-1", "success", target=engine)

    run = run_row(engine, "run-1")
    stage = stage_row(engine, "run-1", "yahoo")

    assert run["status"] == "success"
    assert run["triggered_by"] == "nightly"
    assert run["completed_at"] is not None
    assert stage["status"] == "success"
    assert stage["output_count"] == 500
    assert stage["output_metadata"]["file"] == "universe.csv"
    assert stage["completed_at"] is not None


def test_recorder_retry_path_is_idempotent_on_run_and_stage():
    engine = engine_with_schema()

    start_run("run-2", triggered_by="manual", target=engine)
    start_stage("run-2", "gold", target=engine)
    complete_stage("run-2", "gold", output_count=10, metadata={"attempt": 1}, target=engine)

    start_run("run-2", triggered_by="manual_retry", target=engine)
    start_stage("run-2", "gold", target=engine)
    complete_stage("run-2", "gold", output_count=12, metadata={"attempt": 2}, target=engine)

    run = run_row(engine, "run-2")
    stage = stage_row(engine, "run-2", "gold")
    with engine.connect() as conn:
        stage_count = conn.execute(select(pipeline_stages).where(pipeline_stages.c.run_id == "run-2")).all()

    assert run["status"] == "running"
    assert run["triggered_by"] == "manual_retry"
    assert len(stage_count) == 1
    assert stage["status"] == "success"
    assert stage["output_count"] == 12
    assert stage["output_metadata"]["attempt"] == 2
    assert stage["error"] is None


def test_recorder_stage_failed_then_downstream_skipped_path():
    engine = engine_with_schema()

    start_run("run-3", triggered_by="nightly", target=engine)
    start_stage("run-3", "model", target=engine)
    fail_stage("run-3", "model", "model validation failed", target=engine)
    skip_stage("run-3", "candidates", "upstream model failed", target=engine)
    skip_stage("run-3", "selection", "upstream model failed", target=engine)

    run = run_row(engine, "run-3")
    failed = stage_row(engine, "run-3", "model")
    skipped = stage_row(engine, "run-3", "candidates")

    assert run["status"] == "failed"
    assert run["current_stage"] == "model"
    assert run["error"] == "model validation failed"
    assert failed["status"] == "failed"
    assert failed["error"] == "model validation failed"
    assert skipped["status"] == "skipped"
    assert skipped["output_count"] == 0
    assert skipped["output_metadata"]["skip_reason"] == "upstream model failed"
