from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import func, select

from portal.services.latest_file_reader import count_rows, latest_file, readable_reason, safe_read_csv
from portal.services.trading_service import _position_summary, _status_counts
from stockml.db.connection import get_engine
from stockml.db.schema import PIPELINE_STAGE_NAMES, pipeline_runs, pipeline_stages, position_events
from stockml.services.events import position_id_for_symbol


MONITOR_EVENT_TYPES = {"monitor_safe", "monitor_watch", "monitor_close", "monitor_rotate"}
STATE_CHANGE_EVENT_TYPES = {
    "selected",
    "submitted",
    "filled",
    "partial",
    "monitor_watch",
    "monitor_close",
    "monitor_rotate",
    "operator_keep",
    "operator_close",
    "operator_override",
    "broker_rejected",
    "guardrail_blocked",
}


def _engine():
    return get_engine(required=False)


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, (dict, list)):
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _record(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_value(value) for key, value in row.items()}


def _records(frame: pd.DataFrame, limit: int = 500) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return [_record(row) for row in frame.head(limit).fillna("").to_dict("records")]


def _csv_timestamp(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")


def _rows_from_db(statement, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    engine = _engine()
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            return [_record(dict(row)) for row in conn.execute(statement, params or {}).mappings().all()]
    except Exception:
        return []


def _row_from_db(statement, params: dict[str, Any] | None = None) -> dict[str, Any]:
    engine = _engine()
    if engine is None:
        return {}
    try:
        with engine.connect() as conn:
            row = conn.execute(statement, params or {}).mappings().first()
        return _record(dict(row)) if row else {}
    except Exception:
        return {}


def _empty_stage(stage_name: str) -> dict[str, Any]:
    return {
        "stage_name": stage_name,
        "status": "missing",
        "started_at": None,
        "completed_at": None,
        "output_count": 0,
        "output_metadata": {},
        "error": "",
    }


def _artifact_stage(root: Path, stage_name: str, key: str, pattern: str, detail: str) -> dict[str, Any]:
    path = latest_file(root, key, pattern)
    if path is None:
        return {**_empty_stage(stage_name), "detail": detail, "artifact": ""}
    timestamp = _csv_timestamp(path)
    return {
        "stage_name": stage_name,
        "status": "success",
        "started_at": None,
        "completed_at": timestamp,
        "output_count": count_rows(path),
        "output_metadata": {"artifact": path.name, "detail": detail},
        "error": "",
        "detail": detail,
        "artifact": path.name,
    }


def _artifact_pipeline_context(root: Path) -> dict[str, Any]:
    stages = [
        _artifact_stage(root, "yahoo", "raw", "03_us_price_history_store*.csv", "price history store"),
        _artifact_stage(root, "gold", "gold", "06_us_gold_ml_dataset_*.csv", "gold dataset"),
        _artifact_stage(root, "model", "model_outputs", "advanced_model_signal_table_*.csv", "signal table"),
        _artifact_stage(root, "candidates", "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv", "candidate pool"),
        _artifact_stage(root, "selection", "portal_outputs", "08_alpaca_paper_order_plan_*.csv", "order plan"),
        _artifact_stage(root, "submitted", "portal_outputs", "08_alpaca_paper_order_results_*.csv", "order results"),
    ]
    completed = [stage.get("completed_at") for stage in stages if stage.get("completed_at")]
    last_update = max(completed) if completed else None
    run = {
        "run_id": "artifact-latest" if last_update else "",
        "started_at": last_update,
        "completed_at": last_update,
        "status": "success" if last_update else "missing",
        "current_stage": "",
        "error": "",
        "triggered_by": "artifact_fallback",
    }
    return {"source": "csv_artifacts", "run": run, "stage_names": list(PIPELINE_STAGE_NAMES), "stages": stages}


def _latest_pipeline_run() -> dict[str, Any]:
    return _row_from_db(
        pipeline_runs.select().order_by(pipeline_runs.c.started_at.desc(), pipeline_runs.c.run_id.desc()).limit(1)
    )


def pipeline_current_context(root: Path) -> dict[str, Any]:
    run = _latest_pipeline_run()
    if not run:
        return _artifact_pipeline_context(root)
    rows = _rows_from_db(
        pipeline_stages.select()
        .where(pipeline_stages.c.run_id == run["run_id"])
        .order_by(pipeline_stages.c.stage_name.asc())
    )
    by_name = {row["stage_name"]: row for row in rows}
    stages = []
    for name in PIPELINE_STAGE_NAMES:
        stage = {**_empty_stage(name), **by_name.get(name, {})}
        stage["output_metadata"] = stage.get("output_metadata") or {}
        stage["output_count"] = int(stage.get("output_count") or 0)
        stages.append(stage)
    return {"source": "database", "run": run, "stage_names": list(PIPELINE_STAGE_NAMES), "stages": stages}


def pipeline_history_context(root: Path, days: int = 14) -> dict[str, Any]:
    limit = max(1, min(int(days or 14), 90))
    runs = _rows_from_db(
        pipeline_runs.select().order_by(pipeline_runs.c.started_at.desc(), pipeline_runs.c.run_id.desc()).limit(limit)
    )
    run_ids = [row["run_id"] for row in runs]
    stages_by_run: dict[str, list[dict[str, Any]]] = {run_id: [] for run_id in run_ids}
    if run_ids:
        rows = _rows_from_db(
            pipeline_stages.select()
            .where(pipeline_stages.c.run_id.in_(run_ids))
            .order_by(pipeline_stages.c.run_id.desc(), pipeline_stages.c.stage_name.asc())
        )
        for row in rows:
            stages_by_run.setdefault(row["run_id"], []).append(row)
    for row in runs:
        stages = stages_by_run.get(row["run_id"], [])
        row["stages"] = stages
        row.update(_pipeline_history_summary(row, stages))
    return {"source": "database" if runs else "empty", "days": limit, "stage_names": list(PIPELINE_STAGE_NAMES), "runs": runs}


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _duration_label(seconds: float | None) -> str:
    if seconds is None:
        return "Not available"
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining = minutes % 60
    return f"{hours}h {remaining}m"


def _stage_output_count(stages_by_name: dict[str, dict[str, Any]], stage_name: str) -> int:
    stage = stages_by_name.get(stage_name) or {}
    try:
        return int(stage.get("output_count") or 0)
    except Exception:
        return 0


def _pipeline_history_summary(run: dict[str, Any], stages: list[dict[str, Any]]) -> dict[str, Any]:
    started = _parse_datetime(run.get("started_at"))
    completed = _parse_datetime(run.get("completed_at"))
    duration_seconds = (completed - started).total_seconds() if started and completed else None
    stages_by_name = {str(stage.get("stage_name")): stage for stage in stages}
    status = str(run.get("status") or "missing")
    error = str(run.get("error") or "")
    failed_stage = next((stage for stage in stages if str(stage.get("status") or "").lower() == "failed"), None)
    status_note = error or (f"{failed_stage.get('stage_name')} failed" if failed_stage else status.replace("_", " "))
    return {
        "stage_statuses": {name: str((stages_by_name.get(name) or {}).get("status") or "missing") for name in PIPELINE_STAGE_NAMES},
        "duration_seconds": duration_seconds,
        "duration_label": _duration_label(duration_seconds),
        "candidate_count": _stage_output_count(stages_by_name, "candidates"),
        "selected_count": _stage_output_count(stages_by_name, "selection"),
        "status_note": status_note,
    }


def positions_context(root: Path) -> dict[str, Any]:
    positions_file = latest_file(root, "portal_outputs", "08_alpaca_paper_positions_*.csv")
    positions = safe_read_csv(positions_file, nrows=1000)
    rows = _records(positions)
    position_ids: list[str] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        row["position_id"] = position_id_for_symbol(symbol) if symbol else ""
        if row["position_id"]:
            position_ids.append(row["position_id"])
        row["status"] = "open"
        qty = _float(row.get("qty"))
        cost_basis = _float(row.get("cost_basis"))
        row["entry_price"] = _float(row.get("avg_entry_price")) or (cost_basis / qty if qty else None)
    lineage_counts = _position_event_counts(position_ids)
    for row in rows:
        row["lineage_event_count"] = lineage_counts.get(str(row.get("position_id") or ""), 0)
    return {
        "source": "csv_artifacts",
        "refreshed_at": _csv_timestamp(positions_file),
        "summary": _position_summary(positions),
        "positions": rows,
    }


def _float(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        number = float(value)
        if pd.isna(number):
            return None
        return number
    except Exception:
        return None


def _position_events(position_id: str) -> list[dict[str, Any]]:
    if not position_id:
        return []
    return _rows_from_db(
        position_events.select()
        .where(position_events.c.position_id == position_id)
        .order_by(position_events.c.event_at.asc(), position_events.c.id.asc())
    )


def _position_event_counts(position_ids: list[str]) -> dict[str, int]:
    clean_ids = sorted({str(position_id) for position_id in position_ids if position_id})
    if not clean_ids:
        return {}
    rows = _rows_from_db(
        select(position_events.c.position_id, func.count(position_events.c.id).label("event_count"))
        .where(position_events.c.position_id.in_(clean_ids))
        .group_by(position_events.c.position_id)
    )
    counts: dict[str, int] = {}
    for row in rows:
        position_id = str(row.get("position_id") or "")
        if position_id:
            counts[position_id] = int(row.get("event_count") or 0)
    return counts


def position_lineage_context(root: Path, position_id: str) -> dict[str, Any]:
    events = _position_events(position_id)
    state_changes = [event for event in events if event.get("event_type") in STATE_CHANGE_EVENT_TYPES]
    return {
        "source": "database" if events else "empty",
        "position_id": position_id,
        "events": events,
        "summary": {"event_count": len(events), "state_change_count": len(state_changes)},
    }


def basket_today_context(root: Path) -> dict[str, Any]:
    plan_file = latest_file(root, "portal_outputs", "08_alpaca_paper_order_plan_*.csv")
    result_file = latest_file(root, "portal_outputs", "08_alpaca_paper_order_results_*.csv")
    tracking_file = latest_file(root, "portal_outputs", "08_alpaca_paper_order_tracking_*.csv")
    plan = safe_read_csv(plan_file, nrows=1000)
    results = safe_read_csv(result_file, nrows=1000)
    tracking = safe_read_csv(tracking_file, nrows=1000)
    rows = _merge_basket_rows(plan, results, tracking)
    return {
        "source": "csv_artifacts",
        "run_id": _run_id_from_path(plan_file),
        "generated_at": _csv_timestamp(plan_file),
        "rows": rows,
        "counts": {
            "planned": int(len(plan)),
            "submitted": int((results.get("status", pd.Series(dtype=str)).fillna("") == "submitted").sum()) if not results.empty else 0,
            "filled": int((tracking.get("alpaca_status", pd.Series(dtype=str)).fillna("") == "filled").sum()) if not tracking.empty else 0,
            "rejected": int((results.get("status", pd.Series(dtype=str)).fillna("").isin(["rejected", "error"])).sum()) if not results.empty else 0,
        },
    }


def _merge_basket_rows(plan: pd.DataFrame, results: pd.DataFrame, tracking: pd.DataFrame) -> list[dict[str, Any]]:
    if plan.empty and results.empty and tracking.empty:
        return []
    frames = []
    for source, frame in [("planned", plan), ("result", results), ("tracking", tracking)]:
        if not frame.empty:
            out = frame.copy()
            out["__source"] = source
            frames.append(out)
    combined = pd.concat(frames, ignore_index=True, sort=False)
    key = "symbol" if "symbol" in combined.columns else "ticker"
    combined[key] = combined[key].fillna("").astype(str).str.upper()
    rows = []
    for symbol, group in combined.groupby(key, dropna=False):
        if not symbol:
            continue
        planned = group[group["__source"] == "planned"].tail(1)
        result = group[group["__source"] == "result"].tail(1)
        tracked = group[group["__source"] == "tracking"].tail(1)
        base = planned.iloc[0].to_dict() if not planned.empty else {}
        res = result.iloc[0].to_dict() if not result.empty else {}
        trk = tracked.iloc[0].to_dict() if not tracked.empty else {}
        reason = res.get("message") or base.get("trade_quality_reason") or ""
        rows.append(
            _record(
                {
                    "symbol": symbol,
                    "side": base.get("side") or res.get("side") or trk.get("side") or "",
                    "planned_notional": base.get("approved_notional") or base.get("notional") or 0,
                    "sent_notional": res.get("notional") or trk.get("notional") or 0,
                    "filled_qty": trk.get("filled_qty") or res.get("filled_qty") or 0,
                    "filled_avg_price": trk.get("filled_avg_price") or res.get("filled_avg_price") or "",
                    "status": trk.get("alpaca_status") or res.get("status") or base.get("trade_quality_status") or "planned",
                    "reason": readable_reason(reason),
                    "order_id": trk.get("order_id") or res.get("order_id") or "",
                    "client_order_id": trk.get("client_order_id") or res.get("client_order_id") or base.get("client_order_id") or "",
                    "position_id": position_id_for_symbol(symbol),
                }
            )
        )
    return rows


def basket_integrity_context(root: Path) -> dict[str, Any]:
    basket = basket_today_context(root)
    events = _rows_from_db(position_events.select().order_by(position_events.c.event_at.desc()).limit(1000))
    closed = [event for event in events if event.get("event_type") == "operator_close" or event.get("event_type") == "monitor_close"]
    monitor_changes = [event for event in events if event.get("event_type") in {"monitor_watch", "monitor_close", "monitor_rotate"}]
    diffs = [
        {
            "position_id": event.get("position_id"),
            "event_type": event.get("event_type"),
            "event_at": event.get("event_at"),
            "details": event.get("details") or {},
        }
        for event in monitor_changes[:50]
    ]
    return {
        "source": "database+csv_artifacts" if events else "csv_artifacts",
        "run_id": basket["run_id"],
        "selected": int(basket["counts"]["planned"]),
        "submitted": int(basket["counts"]["submitted"]),
        "filled": int(basket["counts"]["filled"]),
        "closed_since": len(closed),
        "monitor_changes_since": len(monitor_changes),
        "diffs": diffs,
    }


def monitor_today_context(root: Path) -> dict[str, Any]:
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    events = _rows_from_db(
        position_events.select()
        .where(position_events.c.event_at >= today_start)
        .where(position_events.c.event_type.in_(tuple(MONITOR_EVENT_TYPES)))
        .order_by(position_events.c.event_at.asc(), position_events.c.id.asc())
    )
    state_changes = [event for event in events if event.get("event_type") in {"monitor_watch", "monitor_close", "monitor_rotate"}]
    return {
        "source": "database" if events else "empty",
        "checks": events,
        "state_changes": state_changes,
        "counts": {"monitor_checks": len(events), "state_changes": len(state_changes)},
    }


def action_queue_context(root: Path) -> dict[str, Any]:
    decisions_file = latest_file(root, "agent_decisions", "position_decisions_*.csv")
    decisions = safe_read_csv(decisions_file, nrows=1000)
    if decisions.empty or "decision" not in decisions.columns:
        items: list[dict[str, Any]] = []
    else:
        actionable = decisions[decisions["decision"].fillna("").isin(["watch", "close", "rotate"])].copy()
        order = {"close": 0, "rotate": 1, "watch": 2}
        actionable["__order"] = actionable["decision"].map(order).fillna(9)
        if "unrealized_plpc" not in actionable.columns:
            actionable["unrealized_plpc"] = 0
        actionable = actionable.sort_values(["__order", "unrealized_plpc"], ascending=[True, True], na_position="last")
        actionable = actionable.drop(columns="__order")
        items = _records(actionable)
        for index, item in enumerate(items):
            item["event_id"] = item.get("event_id") or f"queue-{index + 1}"
            item["position_id"] = position_id_for_symbol(str(item.get("symbol") or ""))
    counts = _status_counts(pd.DataFrame(items), "decision")
    return {"source": "csv_artifacts", "generated_at": _csv_timestamp(decisions_file), "items": items, "counts": {"total": len(items), **counts}}


def _run_id_from_path(path: Path | None) -> str:
    if path is None:
        return ""
    stem = path.stem
    parts = stem.split("_")
    if len(parts) >= 2:
        return "_".join(parts[-2:])
    return stem
