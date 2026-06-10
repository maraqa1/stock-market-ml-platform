from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import func, select

from portal.services.latest_file_reader import count_rows, latest_file, readable_reason, safe_read_csv
from portal.services.trading_service import _position_summary, _status_counts
from stockml.db.connection import get_engine
from stockml.db.schema import PIPELINE_STAGE_NAMES, intraday_candidate_snapshots, intraday_promotion_log, pipeline_runs, pipeline_stages, position_events, rotation_recommendation_log
from stockml.autopilot.basket_risk import evaluate_basket_risk, load_basket_risk_config
from stockml.autopilot.action_queue_policy import classify_action_queue_item
from stockml.autopilot.position_health import PositionHealthRules, classify_position_health
from stockml.services.events import position_id_for_symbol
from stockml.autopilot.open import load_auto_open_config
from stockml.autopilot.rotate import load_rotation_config
from stockml.marketdata.providers.factory import configured_provider_name
from stockml.trading.paper_autopilot import load_state as load_autopilot_state
from stockml.trading.position_intelligence import enrich_positions


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

TRADING_STAGE_ARTIFACTS = {
    "candidates": ("portal_outputs", "08_alpaca_paper_candidate_pool_*.csv", "08_alpaca_paper_candidate_pool_{stamp}.csv", "candidate pool"),
    "selection": ("portal_outputs", "08_alpaca_paper_order_plan_*.csv", "08_alpaca_paper_order_plan_{stamp}.csv", "order plan"),
    "submitted": ("portal_outputs", "08_alpaca_paper_order_results_*.csv", "08_alpaca_paper_order_results_{stamp}.csv", "order results"),
}

HELD_OVERNIGHT_BANNER_RE = re.compile(r"^Held overnight:\s+\d+\s+positions did not flatten\.$", re.IGNORECASE)


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


def _reconciled_eod_banner(autopilot_state: dict[str, Any], current_position_count: int) -> str:
    banner = str(autopilot_state.get("eod_banner") or "").strip()
    if not banner or not HELD_OVERNIGHT_BANNER_RE.match(banner):
        return banner
    if current_position_count <= 0:
        return ""
    return f"Held overnight: {current_position_count} positions did not flatten."


def _csv_timestamp(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")


def _latest_model_signal_file(root: Path) -> Path | None:
    latest_pointer = latest_file(root, "model_outputs", "model_predictions_latest.csv")
    if latest_pointer is not None and latest_pointer.exists():
        return latest_pointer
    return latest_file(root, "model_outputs", "advanced_model_signal_table_*.csv")


def _latest_holding_review_by_symbol(root: Path) -> dict[str, dict[str, Any]]:
    review_file = latest_file(root, "holding_period", "holding_review_*.csv")
    review = safe_read_csv(review_file, nrows=5000)
    if review.empty or "symbol" not in review.columns:
        return {}
    wanted = [
        "trading_stream",
        "recommended_holding_days",
        "review_after_days",
        "max_holding_days",
        "holding_quality",
        "holding_gate_pass",
        "holding_gate_reason",
        "recommended_action",
        "median_directional_return_bps",
        "hit_rate",
        "sample_count",
    ]
    out: dict[str, dict[str, Any]] = {}
    for row in _records(review):
        symbol = str(row.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        out[symbol] = {key: row.get(key, "") for key in wanted}
    return out


def _attach_holding_review_to_records(rows: list[dict[str, Any]], root: Path) -> list[dict[str, Any]]:
    reviews = _latest_holding_review_by_symbol(root)
    for row in rows:
        symbol = str(row.get("symbol") or "").upper().strip()
        review = reviews.get(symbol, {})
        row["holding_review_status"] = "available" if review else "missing"
        for key in (
            "recommended_holding_days",
            "trading_stream",
            "review_after_days",
            "max_holding_days",
            "holding_quality",
            "holding_gate_pass",
            "holding_gate_reason",
            "recommended_action",
            "median_directional_return_bps",
            "hit_rate",
            "sample_count",
        ):
            row[key] = review.get(key, "")
    return rows


def _signal_symbol(row: dict[str, Any]) -> str:
    return str(row.get("symbol") or row.get("ticker") or "").strip().upper()


def _signal_direction(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", " ")
    if text in {"long", "buy", "bullish"}:
        return "long"
    if text in {"short", "sell", "bearish"}:
        return "short"
    if text in {"hold", "no decision", "no trade", "skip trade", "skip"}:
        return "hold"
    return ""


def _latest_model_signal_map(root: Path) -> dict[str, dict[str, Any]]:
    signals = safe_read_csv(_latest_model_signal_file(root), nrows=20000)
    if signals.empty:
        return {}
    records: dict[str, dict[str, Any]] = {}
    for row in _records(signals, limit=20000):
        symbol = _signal_symbol(row)
        if not symbol:
            continue
        action = row.get("trade_action") or row.get("latest_signal") or row.get("signal") or row.get("side")
        signal_label = row.get("signal") or action
        direction = _signal_direction(signal_label or action)
        model_status = str(row.get("model_status") or row.get("decision_grade") or "").strip()
        if not model_status:
            model_status = "decision_grade" if direction else "no_decision"
        records[symbol] = {
            "latest_signal_status": "fresh",
            "latest_signal": str(signal_label or "").strip(),
            "latest_signal_direction": direction,
            "model_status": model_status,
            "model_score": row.get("model_score") or row.get("risk_adjusted_score") or row.get("side_probability") or "",
        }
    return records


def _fresh_signal_reason(reason: Any) -> str:
    parts = [part.strip() for part in re.split(r"[|;]", str(reason or "")) if part.strip()]
    parts = [part for part in parts if part not in {"latest_signal_unknown", "signal_stale"}]
    if "latest_signal_fresh" not in parts:
        parts.append("latest_signal_fresh")
    return "|".join(parts)


def _missing_signal_reason(reason: Any) -> str:
    parts = [part.strip() for part in re.split(r"[|;]", str(reason or "")) if part.strip()]
    parts = [part for part in parts if part not in {"latest_signal_unknown", "signal_stale"}]
    if "latest_model_signal_missing" not in parts:
        parts.append("latest_model_signal_missing")
    return "|".join(parts)


def _attach_latest_model_signals_to_records(records: list[dict[str, Any]], root: Path) -> list[dict[str, Any]]:
    signal_file = _latest_model_signal_file(root)
    signals = _latest_model_signal_map(root)
    if not records or (signal_file is None and not signals):
        return records
    enriched: list[dict[str, Any]] = []
    for row in records:
        item = dict(row)
        symbol = _signal_symbol(item)
        signal = signals.get(symbol)
        if signal:
            item.update(signal)
            item["decision_reason"] = _fresh_signal_reason(item.get("decision_reason"))
        elif signal_file is not None and symbol:
            item.update(
                {
                    "latest_signal_status": "missing",
                    "latest_signal": "Missing from latest model output",
                    "latest_signal_direction": "missing",
                    "model_status": "not_in_latest_model_output",
                }
            )
            item["decision_reason"] = _missing_signal_reason(item.get("decision_reason"))
        enriched.append(item)
    return enriched


def _attach_latest_model_signals(frame: pd.DataFrame, root: Path) -> pd.DataFrame:
    if frame.empty or "symbol" not in frame.columns:
        return frame
    return pd.DataFrame(_attach_latest_model_signals_to_records(_records(frame, limit=len(frame)), root))


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


def _stamp_from_artifact(path: Path | None) -> str:
    if path is None:
        return ""
    match = re.search(r"_(\d{8}_\d{6})$", path.stem)
    return match.group(1) if match else ""


def _artifact_stage_for_path(stage_name: str, path: Path | None, detail: str) -> dict[str, Any]:
    if path is None:
        return {**_empty_stage(stage_name), "detail": f"{detail} missing for selected trading run", "artifact": ""}
    timestamp = _csv_timestamp(path)
    return {
        "stage_name": stage_name,
        "status": "success",
        "started_at": None,
        "completed_at": timestamp,
        "output_count": count_rows(path),
        "output_metadata": {"artifact": path.name, "detail": detail, "artifact_stamp": _stamp_from_artifact(path)},
        "error": "",
        "detail": detail,
        "artifact": path.name,
    }


def _trading_artifact_stages(root: Path) -> tuple[list[dict[str, Any]], str]:
    candidate_path = latest_file(root, "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
    stamp = _stamp_from_artifact(candidate_path)
    stages: list[dict[str, Any]] = []
    for stage_name, (key, latest_pattern, stamp_pattern, detail) in TRADING_STAGE_ARTIFACTS.items():
        path = None
        if stamp:
            exact = root / "data" / "portal_outputs" / stamp_pattern.format(stamp=stamp)
            path = exact if exact.exists() else None
        if stage_name == "candidates":
            path = path or candidate_path
        elif not stamp:
            path = latest_file(root, key, latest_pattern)
        stages.append(_artifact_stage_for_path(stage_name, path, detail))
    return stages, stamp


def _artifact_pipeline_context(root: Path) -> dict[str, Any]:
    trading_stages, trading_stamp = _trading_artifact_stages(root)
    trading_by_name = {stage["stage_name"]: stage for stage in trading_stages}
    marketdata_provider = configured_provider_name()
    marketdata_label = marketdata_provider.upper() if marketdata_provider == "eodhd" else marketdata_provider.replace("_", " ").title()
    stages = [
        {
            **_artifact_stage(root, "yahoo", "raw", "03_us_price_history_store*.csv", f"{marketdata_label} price history store"),
            "display_name": marketdata_label,
        },
        _artifact_stage(root, "gold", "gold", "06_us_gold_ml_dataset_*.csv", "gold dataset"),
        _artifact_stage(root, "model", "model_outputs", "advanced_model_signal_table_*.csv", "signal table"),
        trading_by_name["candidates"],
        trading_by_name["selection"],
        trading_by_name["submitted"],
    ]
    completed = [stage.get("completed_at") for stage in stages if stage.get("completed_at")]
    last_update = max(completed) if completed else None
    run = {
        "run_id": f"latest-artifacts-{trading_stamp}" if trading_stamp else ("latest-artifacts" if last_update else ""),
        "display_label": "Latest Artifacts",
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


def _manifest_exists_for_run(root: Path, run: dict[str, Any]) -> bool:
    run_id = str(run.get("run_id") or "").strip()
    if not run_id:
        return False
    return (root / "data" / "pipeline_runs" / run_id / "manifest.json").exists()


def pipeline_current_context(root: Path) -> dict[str, Any]:
    run = _latest_pipeline_run()
    if not run:
        return _artifact_pipeline_context(root)
    if not _manifest_exists_for_run(root, run):
        return _artifact_pipeline_context(root)
    artifact_fallback = _artifact_pipeline_context(root)
    artifact_by_name = {stage["stage_name"]: stage for stage in artifact_fallback["stages"]}
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
        fallback = artifact_by_name.get(name) or {}
        if fallback.get("display_name") and not stage.get("display_name"):
            stage["display_name"] = fallback["display_name"]
        if stage.get("status") == "missing" and fallback.get("status") == "success":
            stage = {
                **stage,
                **fallback,
                "output_metadata": {
                    **(fallback.get("output_metadata") or {}),
                    "recorder_status": "missing_stage_row",
                },
                "detail": f"{fallback.get('detail')}; recorder stage row missing",
            }
        stages.append(stage)
    source = "database" if all(stage["stage_name"] in by_name for stage in stages) else "database+csv_artifacts"
    if source != "database" and not run.get("display_label"):
        run = {**run, "display_label": "Latest Artifacts"}
    return {"source": source, "run": run, "stage_names": list(PIPELINE_STAGE_NAMES), "stages": stages}


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
    actions_file = latest_file(root, "operator_actions", "operator_position_actions_*.csv")
    tracking_file = latest_file(root, "portal_outputs", "08_alpaca_paper_order_tracking_*.csv")
    decisions_file = latest_file(root, "agent_decisions", "position_decisions_*.csv")
    positions = safe_read_csv(positions_file, nrows=1000)
    actions = safe_read_csv(actions_file, nrows=1000)
    tracking = safe_read_csv(tracking_file, nrows=1000)
    decisions = safe_read_csv(decisions_file, nrows=1000)
    rows = _records(positions)
    summary = _position_summary(positions)
    autopilot_state = load_autopilot_state(root)
    decisions = _attach_latest_model_signals(decisions, root)
    rows = enrich_positions(rows, decisions=_records(decisions), autopilot_state=autopilot_state)
    rows = _attach_latest_model_signals_to_records(rows, root)
    rows = _attach_holding_review_to_records(rows, root)
    auto_open_config = load_auto_open_config(root=root)
    health_rules = PositionHealthRules(
        max_position_loss_pct=float(auto_open_config.max_position_loss_pct),
        hard_stop_loss_pct=4.0,
    )
    for row in rows:
        intel = row.get("position_intelligence") or {}
        health = classify_position_health(
            {
                **row,
                "signal_state": intel.get("signal_state"),
                "decision_reason": intel.get("decision_reason"),
            },
            health_rules,
        )
        row.update(health)
        if isinstance(intel, dict):
            intel.update(health)
            row["position_intelligence"] = intel
    basket = evaluate_basket_risk(rows, config=load_basket_risk_config(root), previous_state=str(autopilot_state.get("basket_state") or ""))
    open_position_symbols = {str(row.get("symbol") or "").upper() for row in rows if row.get("symbol")}
    pending_close_orders = _latest_close_orders_by_symbol(actions, tracking, open_position_symbols)
    position_ids: list[str] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        row["position_id"] = position_id_for_symbol(symbol) if symbol else ""
        if row["position_id"]:
            position_ids.append(row["position_id"])
        row["status"] = "open"
        row["broker_order"] = pending_close_orders.get(symbol)
        if row["broker_order"]:
            row["status"] = row["broker_order"]["status_key"]
        qty = _float(row.get("qty"))
        cost_basis = _float(row.get("cost_basis"))
        row["entry_price"] = _float(row.get("avg_entry_price")) or (cost_basis / qty if qty else None)
    lineage_counts = _position_event_counts(position_ids)
    for row in rows:
        row["lineage_event_count"] = lineage_counts.get(str(row.get("position_id") or ""), 0)
    return {
        "source": "csv_artifacts",
        "refreshed_at": _csv_timestamp(positions_file),
        "summary": summary,
        "basket_state": basket.basket_state,
        "red_position_pct": basket.red_position_pct,
        "basket_return": summary.get("position_unrealized_plpc", basket.basket_return),
        "new_entries_paused": basket.new_entries_paused,
        "basket_risk_reason": basket.reason,
        "basket_risk_reason_text": basket.reason_text,
        "pending_close_order_count": len(pending_close_orders),
        "eod_state": autopilot_state.get("eod_state") or "inactive",
        "eod_banner": _reconciled_eod_banner(autopilot_state, len(rows)),
        "positions": rows,
    }


def _latest_close_orders_by_symbol(
    actions: pd.DataFrame,
    tracking: pd.DataFrame | None = None,
    open_position_symbols: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    if actions.empty or "symbol" not in actions.columns:
        return {}
    frame = actions.copy()
    action_col = frame.get("operator_action", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    frame = frame[action_col == "close"].copy()
    if frame.empty:
        return {}
    if "timestamp" in frame.columns:
        frame = frame.sort_values("timestamp")

    pending_statuses = {"accepted", "new", "pending_new", "pending_replace", "submitted"}
    partial_statuses = {"partially_filled", "partial"}
    frame = _confirmed_close_order_rows(frame, tracking, open_position_symbols)
    if frame.empty:
        return {}

    out: dict[str, dict[str, Any]] = {}
    for row in _records(frame):
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        alpaca_status = str(row.get("alpaca_status") or "").strip().lower()
        status = str(row.get("status") or "").strip().lower()
        effective_status = alpaca_status or status
        order_id = str(row.get("order_id") or "").strip()
        if effective_status in partial_statuses:
            out[symbol] = {
                "status_key": "partial",
                "label": "Close partially filled",
                "detail": "Broker received the close order; partial fill reported.",
                "order_id": order_id,
                "client_order_id": row.get("client_order_id") or "",
                "submitted_at": row.get("timestamp") or "",
            }
        elif effective_status in pending_statuses and order_id:
            out[symbol] = {
                "status_key": "submitted",
                "label": "Close order accepted",
                "detail": "Waiting for broker fill.",
                "order_id": order_id,
                "client_order_id": row.get("client_order_id") or "",
                "submitted_at": row.get("timestamp") or "",
            }
    return out


def _confirmed_close_order_rows(
    actions: pd.DataFrame,
    tracking: pd.DataFrame | None,
    open_position_symbols: set[str] | None,
) -> pd.DataFrame:
    if tracking is None or tracking.empty or "symbol" not in tracking.columns:
        return actions

    symbols = set(open_position_symbols or set())
    if not symbols:
        return actions.iloc[0:0].copy()

    close_actions = actions.copy()
    close_actions["__symbol"] = close_actions["symbol"].fillna("").astype(str).str.upper()
    close_actions = close_actions[close_actions["__symbol"].isin(symbols)].copy()
    if close_actions.empty:
        return close_actions.drop(columns=["__symbol"], errors="ignore")

    action_order_ids = {str(value).strip() for value in close_actions.get("order_id", pd.Series(dtype=str)).dropna() if str(value).strip()}
    action_client_ids = {str(value).strip() for value in close_actions.get("client_order_id", pd.Series(dtype=str)).dropna() if str(value).strip()}

    tracked = tracking.copy()
    tracked["__symbol"] = tracked["symbol"].fillna("").astype(str).str.upper()
    tracked = tracked[tracked["__symbol"].isin(symbols)].copy()
    if action_order_ids and "order_id" in tracked.columns:
        tracked = tracked[tracked["order_id"].fillna("").astype(str).isin(action_order_ids)].copy()
    elif action_client_ids and "client_order_id" in tracked.columns:
        tracked = tracked[tracked["client_order_id"].fillna("").astype(str).isin(action_client_ids)].copy()
    else:
        return close_actions.drop(columns=["__symbol"], errors="ignore")
    if tracked.empty:
        return close_actions.drop(columns=["__symbol"], errors="ignore")
    sort_column = "updated_at" if "updated_at" in tracked.columns else "submitted_at" if "submitted_at" in tracked.columns else None
    if sort_column:
        tracked = tracked.sort_values(sort_column)
    return tracked.drop(columns=["__symbol"], errors="ignore")


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
    evaluations_file = latest_file(root, "candidate_evaluations", "candidate_evaluation_*.csv")
    positions_file = latest_file(root, "portal_outputs", "08_alpaca_paper_positions_*.csv")
    decisions = safe_read_csv(decisions_file, nrows=1000)
    evaluations = safe_read_csv(evaluations_file, nrows=1000)
    position_symbols = _open_symbols_from_positions_file(positions_file)
    if decisions.empty or "decision" not in decisions.columns:
        items: list[dict[str, Any]] = []
    else:
        actionable = decisions[decisions["decision"].fillna("").isin(["watch", "close", "rotate", "replace"])].copy()
        if position_symbols is not None and "symbol" in actionable.columns:
            actionable = actionable[actionable["symbol"].fillna("").astype(str).str.upper().isin(position_symbols)].copy()
        order = {"close": 0, "rotate": 1, "replace": 1, "watch": 2}
        actionable["__order"] = actionable["decision"].map(order).fillna(9)
        if "unrealized_plpc" not in actionable.columns:
            actionable["unrealized_plpc"] = 0
        actionable = actionable.sort_values(["__order", "unrealized_plpc"], ascending=[True, True], na_position="last")
        actionable = actionable.drop(columns="__order")
        actionable = _attach_latest_model_signals(actionable, root)
        items = _records(actionable)
        held_symbols = position_symbols if position_symbols is not None else {str(symbol).upper() for symbol in decisions.get("symbol", pd.Series(dtype=str)).dropna()}
        auto_config = load_auto_open_config(root=root)
        rules = PositionHealthRules(max_position_loss_pct=float(auto_config.max_position_loss_pct), hard_stop_loss_pct=4.0)
        for index, item in enumerate(items):
            item["event_id"] = item.get("event_id") or f"queue-{index + 1}"
            item["position_id"] = position_id_for_symbol(str(item.get("symbol") or ""))
            item.update(_operator_call_for_queue_item(item, held_symbols))
            item.update(classify_action_queue_item(item, held_symbols=held_symbols, rules=rules, close_automation_mode=auto_config.close_automation_mode))
    open_order_symbols = _open_order_symbols_from_tracking(root)
    items.extend(_candidate_queue_items(evaluations, len(items), held_symbols=position_symbols or set(), root=root))
    items.extend(_rotation_queue_items(len(items), held_symbols=position_symbols, open_order_symbols=open_order_symbols))
    counts = _status_counts(pd.DataFrame(items), "decision")
    counts["close"] = int(counts.get("close", 0)) + int(counts.get("close_candidate", 0)) + int(counts.get("close_now", 0))
    counts["action_required"] = sum(1 for item in items if _action_queue_item_requires_attention(item))
    generated_at = max([value for value in [_csv_timestamp(decisions_file), _csv_timestamp(evaluations_file), _csv_timestamp(positions_file)] if value] or [""])
    return {"source": "csv_artifacts", "generated_at": generated_at, "items": items, "counts": {"total": len(items), **counts}}


def _action_queue_item_requires_attention(item: dict[str, Any]) -> bool:
    decision = str(item.get("decision") or "").strip().lower()
    label = str(item.get("operator_call_label") or "").strip().lower()
    call = str(item.get("operator_call") or "").strip().lower()
    if label in {"watch only", "hold winner"}:
        return False
    if decision in {"watch", "watch_loss", "healthy_hold"}:
        return False
    if bool(item.get("operator_apply_enabled")):
        return True
    return call in {"close", "warning"} or decision in {"close", "close_now", "close_candidate", "replace", "rotate", "open_candidate", "replace_candidate"}


def _open_symbols_from_positions_file(positions_file: Path | None) -> set[str] | None:
    if positions_file is None or not positions_file.exists():
        return None
    positions = safe_read_csv(positions_file, nrows=1000)
    if positions.empty or "symbol" not in positions.columns:
        return set()
    return {str(symbol).upper() for symbol in positions["symbol"].dropna() if str(symbol).strip()}


def _open_order_symbols_from_tracking(root: Path) -> set[str]:
    tracking_file = latest_file(root, "portal_outputs", "08_alpaca_paper_order_tracking_*.csv")
    tracking = safe_read_csv(tracking_file, nrows=1000)
    if tracking.empty or "symbol" not in tracking.columns:
        return set()
    status = tracking.get("alpaca_status", tracking.get("status", pd.Series("", index=tracking.index))).fillna("").astype(str).str.lower()
    open_states = {"accepted", "new", "pending_new", "pending_replace", "submitted", "partially_filled", "partial"}
    return {str(symbol).upper() for symbol in tracking.loc[status.isin(open_states), "symbol"].dropna() if str(symbol).strip()}


def _rotation_queue_items(offset: int, *, held_symbols: set[str] | None = None, open_order_symbols: set[str] | None = None) -> list[dict[str, Any]]:
    rotation_config = load_rotation_config()
    automatic_rotation = bool(rotation_config.enabled and not rotation_config.require_operator_confirm)
    rows = _rows_from_db(
        rotation_recommendation_log.select()
        .where(rotation_recommendation_log.c.verdict == "proposed")
        .order_by(rotation_recommendation_log.c.logged_at.desc())
        .limit(10)
    )
    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=offset + 1):
        details = row.get("details") or {}
        details = details if isinstance(details, dict) else {}
        replace_symbol = str(row.get("replace_symbol") or "").upper()
        with_symbol = str(row.get("with_symbol") or "").upper()
        if held_symbols is not None and replace_symbol not in held_symbols:
            continue
        if held_symbols is not None and with_symbol in held_symbols:
            continue
        if open_order_symbols and ({replace_symbol, with_symbol} & open_order_symbols):
            continue
        operator_label = "Apply rotation"
        operator_reason = f"Paper Assist proposes {replace_symbol} -> {with_symbol}. Operator confirmation required."
        operator_call = "warning"
        operator_apply_enabled = True
        if automatic_rotation:
            operator_label = "Auto rotation"
            operator_reason = f"Paper Autopilot will rotate {replace_symbol} -> {with_symbol} automatically when no broker orders are pending."
            operator_call = "info"
            operator_apply_enabled = False
        items.append(
            {
                **row,
                "event_id": f"rotation-{row.get('id')}",
                "symbol": f"{replace_symbol} -> {with_symbol}",
                "side": "long",
                "unrealized_pl": "",
                "unrealized_plpc": row.get("score_delta") or 0,
                "signal_age_minutes": "",
                "decision": "rotate",
                "recommended_action": "apply_rotation",
                "decision_reason": details.get("reason_text") or row.get("reason") or "HIGHER_PROMOTION_SCORE",
                "replacement_symbol": with_symbol,
                "position_id": row.get("replace_position_id") or position_id_for_symbol(replace_symbol),
                "operator_call": operator_call,
                "operator_call_label": operator_label,
                "operator_call_reason": operator_reason,
                "operator_apply_enabled": operator_apply_enabled,
                "generated_at": row.get("logged_at"),
            }
        )
    return items


def intraday_promotion_context(root: Path, *, limit: int | None = 20) -> dict[str, Any]:
    engine = _engine()
    if engine is None:
        return {"source": "empty", "latest_tick": "", "rows": [], "counts": {}}
    try:
        with engine.connect() as conn:
            latest_tick = conn.execute(select(func.max(intraday_candidate_snapshots.c.snapshot_at))).scalar()
            if latest_tick is None:
                return {"source": "database", "latest_tick": "", "rows": [], "counts": {}}
            joined = intraday_promotion_log.join(
                intraday_candidate_snapshots,
                intraday_promotion_log.c.snapshot_id == intraday_candidate_snapshots.c.id,
            )
            query = (
                select(
                    intraday_promotion_log.c.symbol,
                    intraday_candidate_snapshots.c.is_held,
                    intraday_candidate_snapshots.c.nightly_bias,
                    intraday_promotion_log.c.nightly_score,
                    intraday_promotion_log.c.intraday_adjustment,
                    intraday_promotion_log.c.promotion_score,
                    intraday_promotion_log.c.verdict,
                    intraday_promotion_log.c.block_reason,
                    intraday_promotion_log.c.contributing,
                    intraday_candidate_snapshots.c.snapshot_at,
                )
                .select_from(joined)
                .where(intraday_candidate_snapshots.c.snapshot_at == latest_tick)
                .order_by(intraday_promotion_log.c.promotion_score.desc(), intraday_promotion_log.c.symbol.asc())
            )
            if limit is not None:
                query = query.limit(max(1, int(limit)))
            rows = conn.execute(query).mappings().all()
        records = [_record(dict(row)) for row in rows]
        counts = _status_counts(pd.DataFrame(records), "verdict")
        return {
            "source": "database",
            "latest_tick": latest_tick.isoformat(timespec="seconds") if hasattr(latest_tick, "isoformat") else str(latest_tick),
            "rows": records,
            "counts": {"total": len(records), **counts},
        }
    except Exception:
        return {"source": "empty", "latest_tick": "", "rows": [], "counts": {}}


def _candidate_queue_items(evaluations: pd.DataFrame, offset: int, *, held_symbols: set[str] | None = None, root: Path | None = None) -> list[dict[str, Any]]:
    if evaluations.empty or "decision" not in evaluations.columns:
        return []
    actionable = evaluations[evaluations["decision"].fillna("").isin(["open_candidate", "replace_candidate"])].copy()
    if held_symbols and "symbol" in actionable.columns:
        actionable = actionable[~actionable["symbol"].fillna("").astype(str).str.upper().isin(held_symbols)].copy()
    if actionable.empty:
        return []
    actionable = actionable.sort_values(["decision", "candidate_rank"], ascending=[True, True]).head(10)
    items: list[dict[str, Any]] = []
    auto_config = load_auto_open_config(root=root) if root is not None else None
    rules = PositionHealthRules(max_position_loss_pct=float(auto_config.max_position_loss_pct), hard_stop_loss_pct=4.0) if auto_config is not None else PositionHealthRules()
    close_automation_mode = auto_config.close_automation_mode if auto_config is not None else "automatic"
    for index, row in enumerate(_records(actionable), start=offset + 1):
        decision = str(row.get("decision") or "")
        label = "Review open" if decision == "open_candidate" else "Review candidate"
        reason = str(row.get("operator_call_text") or "Candidate evaluation requires operator review.")
        item = {
                **row,
                "event_id": f"candidate-{index}",
                "position_id": "",
                "unrealized_pl": "",
                "unrealized_plpc": 0,
                "signal_age_minutes": "",
                "replacement_symbol": row.get("held_symbol_to_compare") or "",
                "operator_call": "info",
                "operator_call_label": label,
                "operator_call_reason": reason,
                "operator_apply_enabled": False,
            }
        items.append(classify_action_queue_item(item, held_symbols=held_symbols or set(), rules=rules, close_automation_mode=close_automation_mode))
    return items


def _operator_call_for_queue_item(item: dict[str, Any], held_symbols: set[str]) -> dict[str, Any]:
    decision = str(item.get("decision") or "").strip().lower()
    reason = str(item.get("decision_reason") or "").strip().lower()
    replacement_symbol = str(item.get("replacement_symbol") or "").strip().upper()
    current_symbol = str(item.get("symbol") or "").strip().upper()
    replacement_already_held = bool(replacement_symbol and replacement_symbol in held_symbols and replacement_symbol != current_symbol)
    pnl = _float_value(item.get("unrealized_pl"))
    pnl_pct = _float_value(item.get("unrealized_plpc"))

    if decision == "watch":
        return {
            "operator_call": "watch",
            "operator_call_label": "Watch only",
            "operator_call_reason": "No trade. Monitor flagged stale signal; wait for fresh rescore.",
            "operator_apply_enabled": False,
            "action_button_label": "Acknowledge",
        }
    if decision == "close":
        return {
            "operator_call": "close",
            "operator_call_label": "Review close",
            "operator_call_reason": "Close signal needs operator confirmation before paper order submission.",
            "operator_apply_enabled": True,
            "action_button_label": "Review close",
        }
    if decision in {"rotate", "replace"}:
        if "take_profit" in reason and pnl <= 0:
            return {
                "operator_call": "warning",
                "operator_call_label": "Hold - logic check",
                "operator_call_reason": "Take-profit reason conflicts with current negative P&L; do not apply until reviewed.",
                "operator_apply_enabled": False,
            }
        if replacement_already_held:
            return {
                "operator_call": "warning",
                "operator_call_label": "Review concentration",
                "operator_call_reason": f"Replacement {replacement_symbol} is already held; avoid increasing concentration without review.",
                "operator_apply_enabled": False,
            }
        if "stop_loss" in reason:
            return {
                "operator_call": "close",
                "operator_call_label": "Review stop-loss",
                "operator_call_reason": "Stop-loss was flagged; confirm broker state and replacement before applying.",
                "operator_apply_enabled": True,
            }
        if pnl > 0 and "replacement_rank_improvement" in reason:
            return {
                "operator_call": "watch",
                "operator_call_label": "Hold winner",
                "operator_call_reason": "Position is profitable; do not rotate on rank improvement alone.",
                "operator_apply_enabled": False,
            }
        return {
            "operator_call": "warning",
            "operator_call_label": "Manual review",
            "operator_call_reason": "Rotation needs confirmation before paper order submission.",
            "operator_apply_enabled": False,
        }
    return {
        "operator_call": "info",
        "operator_call_label": "Review",
        "operator_call_reason": "Monitor output needs review.",
        "operator_apply_enabled": False,
    }


def _float_value(value: Any) -> float:
    try:
        number = float(value)
        if pd.isna(number):
            return 0.0
        return number
    except Exception:
        return 0.0


def _run_id_from_path(path: Path | None) -> str:
    if path is None:
        return ""
    stem = path.stem
    parts = stem.split("_")
    if len(parts) >= 2:
        return "_".join(parts[-2:])
    return stem
