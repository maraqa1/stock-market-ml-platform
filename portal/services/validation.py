from __future__ import annotations

import csv
import io
import math
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.engine import Connection, Engine

from stockml.db.connection import get_engine
from stockml.db.schema import (
    model_feature_importance,
    model_folds,
    model_runs,
    output_outcome,
    output_prediction,
    pipeline_runs,
)


BUCKETS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]


def _engine(target: Engine | Connection | None = None) -> Engine | Connection | None:
    return target or get_engine(required=False)


def _connect(target: Engine | Connection | None):
    if isinstance(target, Connection):
        return target, None
    engine = _engine(target)
    if engine is None:
        return None, None
    context = engine.connect()
    return context.__enter__(), context


def _begin(target: Engine | Connection | None):
    if isinstance(target, Connection):
        return target, None
    engine = _engine(target)
    if engine is None:
        return None, None
    context = engine.begin()
    return context.__enter__(), context


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        parsed = float(value)
        if math.isnan(parsed):
            return default
        return parsed
    except Exception:
        return default


def _parse_date(value: str | date | None, default: date) -> date:
    if isinstance(value, date):
        return value
    if value:
        try:
            return datetime.fromisoformat(str(value)).date()
        except Exception:
            return default
    return default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _model_versions(conn: Connection) -> list[str]:
    rows = conn.execute(select(model_runs.c.model_version).order_by(model_runs.c.trained_at.desc()).limit(50)).scalars().all()
    if rows:
        return [str(row) for row in rows]
    fallback = conn.execute(select(output_outcome.c.model_version).distinct().limit(50)).scalars().all()
    return [str(row) for row in fallback]


def current_model_version(target: Engine | Connection | None = None, requested: str | None = None) -> str:
    if requested:
        return requested
    conn, context = _connect(target)
    if conn is None:
        return ""
    try:
        promoted = conn.execute(select(model_runs.c.model_version).where(model_runs.c.promoted.is_(True)).order_by(model_runs.c.trained_at.desc()).limit(1)).scalar()
        if promoted:
            return str(promoted)
        versions = _model_versions(conn)
        return versions[0] if versions else ""
    except Exception:
        return ""
    finally:
        if context is not None:
            context.__exit__(None, None, None)


def latest_pipeline_model_version(target: Engine | Connection | None = None) -> str:
    conn, context = _connect(target)
    if conn is None:
        return ""
    try:
        row = conn.execute(select(pipeline_runs.c.run_id).order_by(pipeline_runs.c.started_at.desc()).limit(1)).scalar()
        return str(row or "")
    except Exception:
        return ""
    finally:
        if context is not None:
            context.__exit__(None, None, None)


def headline_metrics(model_version: str, from_date: date, to_date: date, target: Engine | Connection | None = None) -> dict[str, Any]:
    conn, context = _connect(target)
    if conn is None or not model_version:
        return {"hit_rate": None, "excess_ret": None, "calib_err": None, "sharpe": None, "prediction_count": 0, "last_computed": _now_iso()}
    try:
        rows = conn.execute(
            select(
                output_outcome.c.evaluation_date,
                output_outcome.c.predicted_excess_return,
                output_outcome.c.actual_excess_return,
                output_outcome.c.outperformed,
            )
            .where(output_outcome.c.model_version == model_version)
            .where(output_outcome.c.evaluation_date >= from_date)
            .where(output_outcome.c.evaluation_date <= to_date)
        ).mappings().all()
    except Exception:
        rows = []
    finally:
        if context is not None:
            context.__exit__(None, None, None)
    if not rows:
        return {"hit_rate": None, "excess_ret": None, "calib_err": None, "sharpe": None, "prediction_count": 0, "last_computed": _now_iso()}

    actuals = [_float(row["actual_excess_return"]) for row in rows]
    hits = [1.0 if row["outperformed"] else 0.0 for row in rows]
    calib = [abs(_float(row["predicted_excess_return"]) - _float(row["actual_excess_return"])) for row in rows]
    by_day: dict[date, list[float]] = {}
    for row in rows:
        by_day.setdefault(row["evaluation_date"], []).append(_float(row["actual_excess_return"]))
    daily = [sum(values) / len(values) for values in by_day.values() if values]
    if len(daily) > 1:
        mean = sum(daily) / len(daily)
        variance = sum((value - mean) ** 2 for value in daily) / (len(daily) - 1)
        stdev = math.sqrt(variance)
        sharpe = (mean / stdev) * math.sqrt(252) if stdev else None
    else:
        sharpe = None
    return {
        "hit_rate": sum(hits) / len(hits),
        "excess_ret": sum(actuals) / len(actuals),
        "calib_err": sum(calib) / len(calib),
        "sharpe": sharpe,
        "prediction_count": len(rows),
        "last_computed": _now_iso(),
    }


def walk_forward_folds(model_version: str, target: Engine | Connection | None = None) -> list[dict[str, Any]]:
    conn, context = _connect(target)
    if conn is None or not model_version:
        return []
    try:
        rows = conn.execute(select(model_folds).where(model_folds.c.model_version == model_version).order_by(model_folds.c.period)).mappings().all()
        return [dict(row) for row in rows]
    except Exception:
        return []
    finally:
        if context is not None:
            context.__exit__(None, None, None)


def confidence_buckets(model_version: str, from_date: date, to_date: date, target: Engine | Connection | None = None) -> list[dict[str, Any]]:
    conn, context = _connect(target)
    if conn is None or not model_version:
        return []
    try:
        rows = conn.execute(
            select(output_prediction.c.outperform_probability, output_outcome.c.outperformed)
            .select_from(
                output_prediction.join(
                    output_outcome,
                    (output_prediction.c.symbol == output_outcome.c.symbol)
                    & (output_prediction.c.prediction_date == output_outcome.c.prediction_date)
                    & (output_prediction.c.model_version == output_outcome.c.model_version),
                )
            )
            .where(output_prediction.c.model_version == model_version)
            .where(output_outcome.c.evaluation_date >= from_date)
            .where(output_outcome.c.evaluation_date <= to_date)
        ).mappings().all()
    except Exception:
        rows = []
    finally:
        if context is not None:
            context.__exit__(None, None, None)
    output = []
    for low, high in BUCKETS:
        selected = [
            row
            for row in rows
            if _float(row["outperform_probability"], -1.0) >= low
            and (_float(row["outperform_probability"], -1.0) < high or (high == 1.0 and _float(row["outperform_probability"], -1.0) <= high))
        ]
        predictions = len(selected)
        realized_hit = (sum(1 for row in selected if row["outperformed"]) / predictions) if predictions else None
        expected_hit = (low + high) / 2
        output.append(
            {
                "bucket": f"{low:.1f}-{high:.1f}",
                "predictions": predictions,
                "realized_hit": realized_hit,
                "expected_hit": expected_hit,
                "delta": (realized_hit - expected_hit) if realized_hit is not None else None,
            }
        )
    return output


def top_features(model_version: str, top_n: int = 20, target: Engine | Connection | None = None) -> list[dict[str, Any]]:
    conn, context = _connect(target)
    if conn is None or not model_version:
        return []
    try:
        rows = conn.execute(
            select(model_feature_importance.c.feature_name, model_feature_importance.c.importance)
            .where(model_feature_importance.c.model_version == model_version)
            .order_by(model_feature_importance.c.importance.desc())
            .limit(top_n)
        ).mappings().all()
        max_importance = max([_float(row["importance"]) for row in rows] or [0.0])
        return [
            {
                "feature_name": row["feature_name"],
                "importance": _float(row["importance"]),
                "bar_pct": (_float(row["importance"]) / max_importance * 100) if max_importance else 0,
            }
            for row in rows
        ]
    except Exception:
        return []
    finally:
        if context is not None:
            context.__exit__(None, None, None)


def leaderboard(target: Engine | Connection | None = None) -> list[dict[str, Any]]:
    conn, context = _connect(target)
    if conn is None:
        return []
    try:
        rows = conn.execute(select(model_runs).order_by(model_runs.c.trained_at.desc()).limit(20)).mappings().all()
        return [dict(row) for row in rows]
    except Exception:
        return []
    finally:
        if context is not None:
            context.__exit__(None, None, None)


def validation_context(
    root=None,
    *,
    model_version: str | None = None,
    from_value: str | date | None = None,
    to_value: str | date | None = None,
    target: Engine | Connection | None = None,
) -> dict[str, Any]:
    today = date.today()
    from_date = _parse_date(from_value, today - timedelta(days=90))
    to_date = _parse_date(to_value, today)
    selected_model = current_model_version(target, model_version)
    board = leaderboard(target)
    versions = [row["model_version"] for row in board]
    if selected_model and selected_model not in versions:
        versions.insert(0, selected_model)
    latest_model_run = latest_pipeline_model_version(target)
    return {
        "model_version": selected_model,
        "model_versions": versions,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "headline": headline_metrics(selected_model, from_date, to_date, target),
        "folds": walk_forward_folds(selected_model, target),
        "buckets": confidence_buckets(selected_model, from_date, to_date, target),
        "features": top_features(selected_model, 20, target),
        "leaderboard": board,
        "latest_pipeline_model": latest_model_run,
        "last_computed": _now_iso(),
    }


def record_training_results(
    model_version: str,
    *,
    trained_at: datetime,
    oos_hit_pct: float | None = None,
    oos_excess_pct: float | None = None,
    promoted: bool = False,
    notes: str | None = None,
    folds: list[dict[str, Any]] | None = None,
    feature_importance: list[dict[str, Any]] | None = None,
    target: Engine | Connection | None = None,
) -> bool:
    conn, context = _begin(target)
    if conn is None:
        return False
    exc_info = (None, None, None)
    try:
        dialect = conn.dialect.name
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        elif dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        else:
            dialect_insert = insert

        run_stmt = dialect_insert(model_runs).values(
            model_version=model_version,
            trained_at=trained_at,
            oos_hit_pct=oos_hit_pct,
            oos_excess_pct=oos_excess_pct,
            promoted=promoted,
            notes=notes,
        )
        if hasattr(run_stmt, "on_conflict_do_update"):
            run_stmt = run_stmt.on_conflict_do_update(
                index_elements=[model_runs.c.model_version],
                set_={
                    "trained_at": run_stmt.excluded.trained_at,
                    "oos_hit_pct": run_stmt.excluded.oos_hit_pct,
                    "oos_excess_pct": run_stmt.excluded.oos_excess_pct,
                    "promoted": run_stmt.excluded.promoted,
                    "notes": run_stmt.excluded.notes,
                },
            )
        conn.execute(run_stmt)

        conn.execute(delete(model_folds).where(model_folds.c.model_version == model_version))
        conn.execute(delete(model_feature_importance).where(model_feature_importance.c.model_version == model_version))
        if folds:
            conn.execute(
                insert(model_folds),
                [
                    {
                        "model_version": model_version,
                        "period": row["period"],
                        "train_rows": int(row.get("train_rows") or 0),
                        "test_rows": int(row.get("test_rows") or 0),
                        "hit_pct": row.get("hit_pct"),
                        "excess_pct": row.get("excess_pct"),
                        "notes": row.get("notes"),
                    }
                    for row in folds
                ],
            )
        if feature_importance:
            conn.execute(
                insert(model_feature_importance),
                [
                    {
                        "model_version": model_version,
                        "feature_name": row["feature_name"],
                        "importance": _float(row.get("importance")),
                    }
                    for row in feature_importance
                ],
            )
        return True
    except Exception:
        exc_info = sys.exc_info()
        return False
    finally:
        if context is not None:
            context.__exit__(*exc_info)


def table_csv(section: str, context: dict[str, Any]) -> str:
    mapping = {
        "folds": context.get("folds", []),
        "buckets": context.get("buckets", []),
        "features": context.get("features", []),
        "leaderboard": context.get("leaderboard", []),
    }
    rows = mapping.get(section, [])
    output = io.StringIO()
    if not rows:
        return ""
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()
