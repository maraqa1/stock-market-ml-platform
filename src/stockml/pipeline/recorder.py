from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Connection, Engine

from stockml.db.connection import get_engine
from stockml.db.schema import PIPELINE_STAGE_NAMES, pipeline_runs, pipeline_stages


TERMINAL_RUN_STATUSES = {"success", "failed", "skipped"}
TERMINAL_STAGE_STATUSES = {"success", "failed", "skipped"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def _connection(target: Engine | Connection | None = None) -> Iterator[Connection]:
    if target is None:
        engine = get_engine()
        if engine is None:
            raise RuntimeError("Database engine is unavailable")
        with engine.begin() as conn:
            yield conn
        return

    if isinstance(target, Engine):
        with target.begin() as conn:
            yield conn
        return

    yield target


def _validate_stage(stage_name: str) -> None:
    if stage_name not in PIPELINE_STAGE_NAMES:
        allowed = ", ".join(PIPELINE_STAGE_NAMES)
        raise ValueError(f"Unknown pipeline stage '{stage_name}'. Expected one of: {allowed}")


def _run_exists(conn: Connection, run_id: str) -> bool:
    return conn.execute(select(pipeline_runs.c.run_id).where(pipeline_runs.c.run_id == run_id)).first() is not None


def _stage_exists(conn: Connection, run_id: str, stage_name: str) -> bool:
    return (
        conn.execute(
            select(pipeline_stages.c.run_id).where(
                and_(pipeline_stages.c.run_id == run_id, pipeline_stages.c.stage_name == stage_name)
            )
        ).first()
        is not None
    )


def start_run(run_id: str, triggered_by: str = "system", target: Engine | Connection | None = None) -> None:
    """Create or resume a pipeline run.

    The operation is idempotent on ``run_id``: retrying the same run marks it running
    again without deleting completed stage metadata.
    """
    now = utc_now()
    with _connection(target) as conn:
        if _run_exists(conn, run_id):
            conn.execute(
                update(pipeline_runs)
                .where(pipeline_runs.c.run_id == run_id)
                .values(status="running", completed_at=None, error=None, triggered_by=triggered_by)
            )
            return

        conn.execute(
            insert(pipeline_runs).values(
                run_id=run_id,
                started_at=now,
                status="running",
                current_stage=None,
                error=None,
                triggered_by=triggered_by,
            )
        )


def start_stage(run_id: str, stage_name: str, target: Engine | Connection | None = None) -> None:
    """Mark a stage as running, creating the parent run if needed."""
    _validate_stage(stage_name)
    now = utc_now()
    with _connection(target) as conn:
        if not _run_exists(conn, run_id):
            conn.execute(
                insert(pipeline_runs).values(
                    run_id=run_id,
                    started_at=now,
                    status="running",
                    current_stage=stage_name,
                    triggered_by="recorder",
                )
            )
        else:
            conn.execute(
                update(pipeline_runs)
                .where(pipeline_runs.c.run_id == run_id)
                .values(status="running", current_stage=stage_name, completed_at=None, error=None)
            )

        values = {
            "started_at": now,
            "completed_at": None,
            "status": "running",
            "output_count": 0,
            "output_metadata": None,
            "error": None,
        }
        if _stage_exists(conn, run_id, stage_name):
            conn.execute(
                update(pipeline_stages)
                .where(and_(pipeline_stages.c.run_id == run_id, pipeline_stages.c.stage_name == stage_name))
                .values(**values)
            )
        else:
            conn.execute(insert(pipeline_stages).values(run_id=run_id, stage_name=stage_name, **values))


def complete_stage(
    run_id: str,
    stage_name: str,
    output_count: int = 0,
    metadata: dict[str, Any] | None = None,
    target: Engine | Connection | None = None,
) -> None:
    _validate_stage(stage_name)
    now = utc_now()
    with _connection(target) as conn:
        if not _stage_exists(conn, run_id, stage_name):
            conn.execute(
                insert(pipeline_stages).values(
                    run_id=run_id,
                    stage_name=stage_name,
                    started_at=now,
                    status="success",
                    output_count=output_count,
                    output_metadata=metadata or {},
                    completed_at=now,
                    error=None,
                )
            )
        else:
            conn.execute(
                update(pipeline_stages)
                .where(and_(pipeline_stages.c.run_id == run_id, pipeline_stages.c.stage_name == stage_name))
                .values(
                    status="success",
                    completed_at=now,
                    output_count=output_count,
                    output_metadata=metadata or {},
                    error=None,
                )
            )
        conn.execute(
            update(pipeline_runs)
            .where(pipeline_runs.c.run_id == run_id)
            .values(status="running", current_stage=stage_name, error=None)
        )


def fail_stage(run_id: str, stage_name: str, error: str, target: Engine | Connection | None = None) -> None:
    _validate_stage(stage_name)
    now = utc_now()
    with _connection(target) as conn:
        if not _stage_exists(conn, run_id, stage_name):
            conn.execute(
                insert(pipeline_stages).values(
                    run_id=run_id,
                    stage_name=stage_name,
                    started_at=now,
                    completed_at=now,
                    status="failed",
                    output_count=0,
                    output_metadata={},
                    error=error,
                )
            )
        else:
            conn.execute(
                update(pipeline_stages)
                .where(and_(pipeline_stages.c.run_id == run_id, pipeline_stages.c.stage_name == stage_name))
                .values(status="failed", completed_at=now, error=error)
            )
        conn.execute(
            update(pipeline_runs)
            .where(pipeline_runs.c.run_id == run_id)
            .values(status="failed", current_stage=stage_name, error=error, completed_at=now)
        )


def skip_stage(
    run_id: str,
    stage_name: str,
    reason: str,
    target: Engine | Connection | None = None,
) -> None:
    """Record a skipped stage, usually after an upstream stage failed."""
    _validate_stage(stage_name)
    now = utc_now()
    with _connection(target) as conn:
        values = {
            "completed_at": now,
            "status": "skipped",
            "output_count": 0,
            "output_metadata": {"skip_reason": reason},
            "error": reason,
        }
        if _stage_exists(conn, run_id, stage_name):
            conn.execute(
                update(pipeline_stages)
                .where(and_(pipeline_stages.c.run_id == run_id, pipeline_stages.c.stage_name == stage_name))
                .values(**values)
            )
        else:
            conn.execute(
                insert(pipeline_stages).values(
                    run_id=run_id,
                    stage_name=stage_name,
                    started_at=None,
                    **values,
                )
            )


def complete_run(
    run_id: str,
    status: str = "success",
    target: Engine | Connection | None = None,
    error: str | None = None,
) -> None:
    if status not in TERMINAL_RUN_STATUSES:
        allowed = ", ".join(sorted(TERMINAL_RUN_STATUSES))
        raise ValueError(f"Unknown terminal run status '{status}'. Expected one of: {allowed}")

    with _connection(target) as conn:
        conn.execute(
            update(pipeline_runs)
            .where(pipeline_runs.c.run_id == run_id)
            .values(status=status, completed_at=utc_now(), current_stage=None, error=error)
        )
