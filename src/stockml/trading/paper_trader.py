from __future__ import annotations

import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from stockml.safety.paper_only_guard import paper_only_guard
from stockml.decisions.reason_formatter import format_reasons
from stockml.common.paths import MODEL_OUTPUTS_DIR, PORTAL_OUTPUTS_DIR, ensure_data_dirs, latest_file, timestamp
from stockml.services.events import position_id_for_symbol, record_event_once, record_event_safely
from stockml.db.connection import get_engine
from stockml.db.schema import position_events
from sqlalchemy import select
from stockml.trading.activity_journal import enrich_activity_details
from stockml.trading.alpaca_client import AlpacaAPIError, AlpacaPaperClient
from stockml.trading.anti_churn_guard import guard_actions, load_recent_trade_history, write_anti_churn_report
from stockml.trading.autopilot_guard import autopilot_blocks_basket_submission, autopilot_conflicting_symbols, reconcile_autopilot_state_from_tracking
from stockml.trading.config import alpaca_config
from stockml.trading.counterfactual_log import write_counterfactual_candidates
from stockml.trading.execution_owner import LEGACY_BLOCK_REASON, legacy_paper_trader_can_submit
from stockml.trading.config_fingerprint import config_fingerprints
from stockml.trading.lifecycle_ids import LINEAGE_FIELDS, candidate_lineage, fill_lineage, order_lineage
from stockml.trading.order_builder import validate_order_payload
from stockml.trading.order_planner import build_candidate_pool, build_order_plan, build_order_plan_from_candidate_pool, latest_signal_table
from stockml.trading.position_intent_guard import PositionIntentConfig, guard_order_submission, record_position_intent_block, write_position_intent_report
from stockml.trading.shortlist_snapshots import write_shortlist_snapshot
from stockml.candidates.short_side_policy import ShortSidePolicy, load_short_side_policy
from stockml.trading.submission_guards import asset_is_overnight_tradable, load_submission_context, validate_order


LOGGER = logging.getLogger(__name__)


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return default if not value else value in {"1", "true", "yes", "y"}


def warn_if_broker_short_enabled_while_policy_blocks(config, policy: ShortSidePolicy | None = None) -> bool:
    active_policy = policy or load_short_side_policy()
    policy_blocks_shorts = not (active_policy.enabled and active_policy.allow_shorts_in_validation)
    if bool(getattr(config, "allow_short_selling", False)) and policy_blocks_shorts:
        LOGGER.critical(
            "broker_short_selling_enabled_while_short_side_policy_blocks: "
            "set STOCKML_ALLOW_SHORT_SELLING=false or enable/validate short_side_policy before paper submission"
        )
        return True
    return False


def latest_model_freshness(signal_file: Optional[Path] = None) -> tuple[bool, str, str]:
    path = signal_file or latest_file(MODEL_OUTPUTS_DIR, "advanced_model_signal_table_*.csv")
    if path is None or not path.exists():
        return False, "model_signal_table_missing", ""
    modified = datetime.fromtimestamp(path.stat().st_mtime)
    today = datetime.now().date()
    if modified.date() != today:
        return False, f"model_signal_table_stale:{modified.date().isoformat()}", str(path)
    return True, "model_signal_table_fresh", str(path)


def _result_row(order: dict, status: str, order_id: str = "", message: str = "", response: Optional[dict] = None, diagnostics: Optional[dict] = None) -> dict:
    response = response or {}
    diagnostics = diagnostics or {}
    row = {
        "symbol": order.get("symbol", ""),
        "status": status,
        "alpaca_status": response.get("status", ""),
        "order_id": order_id,
        "broker_order_id": order_id,
        "client_order_id": order.get("client_order_id", ""),
        "side": order.get("side", ""),
        "notional": order.get("notional", ""),
        "suggested_quantity": order.get("suggested_quantity", ""),
        "trade_action": order.get("trade_action", ""),
        "filled_qty": response.get("filled_qty", ""),
        "filled_avg_price": response.get("filled_avg_price", ""),
        "submitted_at": response.get("submitted_at", ""),
        "updated_at": response.get("updated_at", ""),
        "message": message,
        "http_status": diagnostics.get("http_status", ""),
        "request_id": diagnostics.get("request_id", ""),
        "api_error": diagnostics.get("api_error", ""),
        "submitted_payload": diagnostics.get("submitted_payload", ""),
    }
    for field in (*LINEAGE_FIELDS, "lineage_warning"):
        if field not in row:
            row[field] = order.get(field, "")
    if order_id:
        lineage = order_lineage({**order, **row}, broker_order_id=order_id)
        row.update({key: value for key, value in lineage.values.items() if value not in (None, "")})
        row["lineage_warning"] = lineage.values.get("lineage_warning", "")
    return row


def _clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _stamp_client_order_ids(plan: pd.DataFrame, stamp: str) -> pd.DataFrame:
    if plan.empty or "client_order_id" not in plan.columns:
        return plan
    out = plan.copy()
    suffix = f"-{stamp.replace('_', '')}"
    max_len = 48

    def stamped(value: object) -> str:
        base = _clean_text(value) or "stockml-order"
        keep = max(1, max_len - len(suffix))
        return f"{base[:keep]}{suffix}"

    out["client_order_id"] = out["client_order_id"].apply(stamped)
    return out


def _boolish(value: object, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return default


def _append_reason(existing: object, reason: str) -> str:
    text = _clean_text(existing)
    if not text:
        return reason
    parts = [part for part in text.split("|") if part]
    return text if reason in parts else "|".join([*parts, reason])


def _filled_event_signature(row: dict) -> tuple[str, str, str, str]:
    return (
        _clean_text(row.get("order_id") or row.get("broker_order_id")),
        _clean_text(row.get("alpaca_status") or row.get("status")).lower(),
        _clean_text(row.get("filled_qty")),
        _clean_text(row.get("filled_avg_price")),
    )


def _filled_event_exists(symbol: str, signature: tuple[str, str, str, str]) -> bool:
    broker_order_id, status, filled_qty, filled_avg_price = signature
    if not broker_order_id:
        return False
    engine = get_engine(required=False)
    if engine is None:
        return False
    with engine.connect() as conn:
        rows = conn.execute(
            select(position_events.c.details)
            .where(
                position_events.c.position_id == position_id_for_symbol(symbol),
                position_events.c.event_type == "filled",
                position_events.c.source == "alpaca_tracking",
            )
            .order_by(position_events.c.event_at.desc())
            .limit(2000)
        ).scalars().all()
    for details in rows:
        if not isinstance(details, dict):
            continue
        existing = (
            _clean_text(details.get("broker_order_id") or details.get("order_id")),
            _clean_text(details.get("status") or details.get("alpaca_status")).lower(),
            _clean_text(details.get("filled_qty")),
            _clean_text(details.get("filled_avg_price")),
        )
        if existing == signature:
            return True
    return False


def record_alpaca_fill_once(tracked: dict, *, tracking_path: Path) -> bool:
    symbol = _clean_text(tracked.get("symbol")).upper()
    signature = _filled_event_signature(tracked)
    broker_order_id, status, filled_qty_text, filled_avg_price_text = signature
    if not symbol or not broker_order_id:
        return False
    if _filled_event_exists(symbol, signature):
        return False
    fill_event_key = f"{broker_order_id}:filled:{status}:{filled_qty_text}:{filled_avg_price_text}"
    side = tracked.get("side", "")
    summary = f"{side} {filled_qty_text} {symbol} filled @ {filled_avg_price_text} · {broker_order_id}"
    fill_payload = {
        "event_key": fill_event_key,
        "details_summary": summary,
        "broker_order_id": broker_order_id,
        "order_id": broker_order_id,
        "client_order_id": tracked.get("client_order_id", ""),
        "symbol": symbol,
        "side": side,
        "qty": tracked.get("suggested_quantity", tracked.get("qty", "")),
        "filled_qty": tracked.get("filled_qty", ""),
        "filled_avg_price": tracked.get("filled_avg_price", ""),
        "order_type": tracked.get("type", tracked.get("order_type", "")),
        "status": tracked.get("alpaca_status", ""),
        "submitted_at": tracked.get("submitted_at", ""),
        "filled_at": tracked.get("filled_at", tracked.get("updated_at", "")),
        "open_or_close": tracked.get("open_or_close", "open"),
        "session_mode": tracked.get("session_mode") or ("overnight_24_5" if _boolish(tracked.get("extended_hours"), False) else "regular_session"),
        "extended_hours": tracked.get("extended_hours", ""),
        "tracking_path": str(tracking_path),
    }
    return record_event_once(
        position_id_for_symbol(symbol),
        "filled",
        "alpaca_tracking",
        enrich_activity_details(fill_payload, fill_lineage({**tracked, **fill_payload})),
        event_key=fill_event_key,
    )


def _mark_overnight_asset_eligibility(candidate_pool: pd.DataFrame, client: AlpacaPaperClient) -> pd.DataFrame:
    if candidate_pool.empty or "extended_hours" not in candidate_pool.columns:
        return candidate_pool
    out = candidate_pool.copy()
    if "overnight_tradable" not in out.columns:
        out["overnight_tradable"] = ""
    if "overnight_eligibility_reason" not in out.columns:
        out["overnight_eligibility_reason"] = ""

    for idx, row in out.iterrows():
        if not _boolish(row.get("extended_hours"), False):
            continue
        symbol = _clean_text(row.get("symbol"))
        if not symbol:
            reason = "missing_symbol"
            overnight_tradable = False
        else:
            try:
                asset = client.get_asset(symbol.upper())
                overnight_tradable = asset_is_overnight_tradable(asset)
                reason = "overnight_tradable" if overnight_tradable else "asset_not_overnight_tradable"
            except Exception as exc:
                overnight_tradable = False
                reason = f"overnight_asset_check_failed: {exc}"

        out.at[idx, "overnight_tradable"] = overnight_tradable
        out.at[idx, "overnight_eligibility_reason"] = reason
        if not overnight_tradable:
            out.at[idx, "trade_quality_status"] = "rejected"
            out.at[idx, "trade_quality_reason"] = _append_reason(row.get("trade_quality_reason", ""), reason)
            out.at[idx, "approved_notional"] = 0.0
            out.at[idx, "suggested_quantity"] = 0
            out.at[idx, "order_eligible"] = False
    return out


def _strategy_version() -> str:
    try:
        return config_fingerprints()["strategy"].digest
    except Exception:
        return ""


def _attach_plan_lineage(plan: pd.DataFrame, *, cycle_id: str, pipeline_run_id: str, model_version: str, strategy_version: str = "") -> pd.DataFrame:
    if plan.empty or "symbol" not in plan.columns:
        return plan
    out = plan.copy()
    for field in (*LINEAGE_FIELDS, "lineage_warning"):
        if field not in out.columns:
            out[field] = ""
    for idx, row in out.iterrows():
        session_mode = _clean_text(row.get("session_mode")) or ("overnight_24_5" if _boolish(row.get("extended_hours"), False) else "regular_session")
        strategy_mode = _clean_text(row.get("strategy_mode")) or _clean_text(row.get("strategy_stream")) or _clean_text(row.get("trading_stream")) or "multi_day_forecast"
        lineage = candidate_lineage(
            symbol=row.get("symbol"),
            cycle_id=cycle_id,
            pipeline_run_id=pipeline_run_id,
            strategy_version=strategy_version,
            candidate_source=row.get("candidate_source") or "paper_order_plan",
            strategy_mode=strategy_mode,
            session_mode=session_mode,
            model_version=model_version,
            side=row.get("side"),
            client_order_id=row.get("client_order_id"),
        )
        for key, value in lineage.values.items():
            out.at[idx, key] = "" if value is None else value
    return out


def _reject_autopilot_conflicts(plan: pd.DataFrame) -> pd.DataFrame:
    if plan.empty or "symbol" not in plan.columns:
        return plan
    symbols = {str(symbol).upper().strip() for symbol in plan["symbol"].dropna().astype(str) if str(symbol).strip()}
    conflicts, reason = autopilot_conflicting_symbols(symbols)
    if not conflicts:
        return plan
    out = plan.copy()
    mask = out["symbol"].astype(str).str.upper().str.strip().isin(conflicts)
    out.loc[mask, "trade_quality_status"] = "rejected"
    out.loc[mask, "trade_quality_reason"] = out.loc[mask, "trade_quality_reason"].apply(lambda value: _append_reason(value, reason))
    out.loc[mask, "approved_notional"] = 0.0
    out.loc[mask, "notional"] = 0.0
    out.loc[mask, "suggested_quantity"] = 0
    out.loc[mask, "order_eligible"] = False
    return out


def _write_tracking_snapshot(results: pd.DataFrame, config, stamp: str) -> tuple[Path, Path]:
    tracking_path = PORTAL_OUTPUTS_DIR / f"08_alpaca_paper_order_tracking_{stamp}.csv"
    positions_path = PORTAL_OUTPUTS_DIR / f"08_alpaca_paper_positions_{stamp}.csv"
    tracking_rows = []
    positions = pd.DataFrame()

    if config.api_key and config.secret_key and not results.empty:
        client = AlpacaPaperClient(config)
        for row in results.to_dict("records"):
            order_id = _clean_text(row.get("order_id"))
            status = _clean_text(row.get("status")).lower()
            if not order_id or status in {"dry_run", "error", "rejected"}:
                tracking_rows.append(row)
                continue
            try:
                live = client.get_order(order_id)
                tracked = {
                    **row,
                    "alpaca_status": live.get("status", row.get("alpaca_status", "")),
                    "filled_qty": live.get("filled_qty", row.get("filled_qty", "")),
                    "filled_avg_price": live.get("filled_avg_price", row.get("filled_avg_price", "")),
                    "submitted_at": live.get("submitted_at", row.get("submitted_at", "")),
                    "updated_at": live.get("updated_at", row.get("updated_at", "")),
                    "message": row.get("message", ""),
                }
                tracking_rows.append(tracked)
                filled_qty = pd.to_numeric(tracked.get("filled_qty", 0), errors="coerce")
                if str(tracked.get("alpaca_status", "")).lower() == "filled" or (not pd.isna(filled_qty) and filled_qty > 0):
                    record_alpaca_fill_once(tracked, tracking_path=tracking_path)
            except Exception as exc:
                tracking_rows.append({**row, "message": f"tracking_error: {exc}"})
        try:
            positions = pd.DataFrame(client.list_positions())
        except Exception:
            positions = pd.DataFrame()
    else:
        tracking_rows = results.to_dict("records")

    pd.DataFrame(tracking_rows).to_csv(tracking_path, index=False)
    positions.to_csv(positions_path, index=False)
    return tracking_path, positions_path


def run_paper_trading(signal_file: Optional[Path] = None, *, plan_only: bool = False) -> dict[str, Path | int | bool]:
    ensure_data_dirs()
    config = alpaca_config()
    warn_if_broker_short_enabled_while_policy_blocks(config)
    paper_only_guard(live_trading_enabled=config.live_trading_enabled)
    model_fresh, model_fresh_reason, model_signal_path = latest_model_freshness(signal_file)
    if not model_fresh and config.submit_orders and not plan_only and not _bool_env("STOCKML_ALLOW_STALE_MODEL_TRADING", False):
        raise RuntimeError(model_fresh_reason)
    signals = latest_signal_table(signal_file)
    candidate_pool = build_candidate_pool(signals, config)
    asset_client = AlpacaPaperClient(config) if config.api_key and config.secret_key and (config.extended_hours or config.overnight_trading_enabled) else None
    if asset_client is not None:
        candidate_pool = _mark_overnight_asset_eligibility(candidate_pool, asset_client)
    pool_plan_columns = {"trade_quality_status", "order_eligible", "suggested_quantity", "trade_action"}
    if pool_plan_columns.issubset(candidate_pool.columns):
        plan = build_order_plan_from_candidate_pool(candidate_pool, config)
    else:
        plan = build_order_plan(signals, config)
    stamp = timestamp()
    plan = _stamp_client_order_ids(plan, stamp)
    pipeline_run_id = Path(model_signal_path).stem if model_signal_path else ""
    model_version = pipeline_run_id
    strategy_version = _strategy_version()
    plan = _attach_plan_lineage(plan, cycle_id=stamp, pipeline_run_id=pipeline_run_id, model_version=model_version, strategy_version=strategy_version)
    plan = _reject_autopilot_conflicts(plan)
    if not plan.empty and "symbol" in plan.columns:
        eligible_plan = plan[
            plan.get("trade_quality_status", pd.Series("", index=plan.index)).astype(str).str.lower().isin({"approved", "reduced"})
            & plan.get("order_eligible", pd.Series(False, index=plan.index)).astype(bool)
        ]
        actions = [
            {"symbol": row.get("symbol"), "action": "open", "side": row.get("side")}
            for row in eligible_plan.to_dict("records")
        ]
        _, anti_churn_report = guard_actions(
            actions,
            trade_history=load_recent_trade_history(),
            now=pd.Timestamp.utcnow().to_pydatetime(),
            cycle_id=f"paper_basket_{stamp}",
        )
        if not anti_churn_report.empty:
            anti_path = write_anti_churn_report(anti_churn_report, stamp=stamp)
            blocked_by_symbol = {str(row.get("symbol") or "").upper(): str(row.get("reason") or "anti_churn_blocked") for row in anti_churn_report.to_dict("records")}
            mask = plan["symbol"].astype(str).str.upper().isin(blocked_by_symbol)
            plan.loc[mask, "trade_quality_status"] = "rejected"
            plan.loc[mask, "trade_quality_reason"] = plan.loc[mask].apply(lambda row: _append_reason(row.get("trade_quality_reason", ""), "anti_churn_" + blocked_by_symbol.get(str(row.get("symbol") or "").upper(), "blocked")), axis=1)
            plan.loc[mask, "approved_notional"] = 0.0
            plan.loc[mask, "notional"] = 0.0
            plan.loc[mask, "suggested_quantity"] = 0
            plan.loc[mask, "order_eligible"] = False
            plan.loc[mask, "anti_churn_report_path"] = str(anti_path)
    candidate_pool_path = PORTAL_OUTPUTS_DIR / f"08_alpaca_paper_candidate_pool_{stamp}.csv"
    plan_path = PORTAL_OUTPUTS_DIR / f"08_alpaca_paper_order_plan_{stamp}.csv"
    result_path = PORTAL_OUTPUTS_DIR / f"08_alpaca_paper_order_results_{stamp}.csv"
    candidate_pool.to_csv(candidate_pool_path, index=False)
    write_shortlist_snapshot(candidate_pool_path.stem, candidate_pool)
    plan.to_csv(plan_path, index=False)
    counterfactual = write_counterfactual_candidates(
        candidate_pool,
        plan=plan,
        cycle_id=stamp,
        pipeline_run_id=pipeline_run_id,
        candidate_source_path=candidate_pool_path,
        order_plan_path=plan_path,
        stamp=stamp,
    )
    for selected in plan.to_dict("records"):
        selected_eligible = bool(selected.get("order_eligible")) and int(selected.get("suggested_quantity", 0) or 0) >= 1
        if str(selected.get("trade_quality_status", "")).lower() in {"approved", "reduced"} and selected_eligible:
            symbol = selected.get("symbol", "")
            cycle_id = stamp
            candidate_source = selected.get("candidate_source", "paper_order_plan")
            selected_event_key = selected.get("event_key") or f"{cycle_id}:{str(symbol).upper()}:{candidate_source}:selected"
            selected_lineage = candidate_lineage(
                symbol=symbol,
                cycle_id=cycle_id,
                pipeline_run_id=selected.get("pipeline_run_id", pipeline_run_id),
                strategy_version=selected.get("strategy_version", strategy_version),
                candidate_source=candidate_source,
                strategy_mode=selected.get("strategy_mode") or selected.get("strategy_stream") or "multi_day_forecast",
                session_mode=selected.get("session_mode") or ("overnight_24_5" if _boolish(selected.get("extended_hours"), False) else "regular_session"),
                model_version=selected.get("model_version", model_version),
                side=selected.get("side", ""),
                client_order_id=selected.get("client_order_id", ""),
            )
            selected_payload = enrich_activity_details(
                {
                    "event_key": selected_event_key,
                    "cycle_id": cycle_id,
                    "symbol": symbol,
                    "side": selected.get("side", ""),
                    "action": "selected",
                    "candidate_source": candidate_source,
                    "trade_action": selected.get("trade_action", ""),
                    "trade_quality_status": selected.get("trade_quality_status", ""),
                    "approved_notional": selected.get("approved_notional", selected.get("notional", "")),
                    "suggested_quantity": selected.get("suggested_quantity", ""),
                    "plan_path": str(plan_path),
                },
                selected_lineage,
            )
            selected_payload["event_key"] = selected_event_key
            record_event_once(
                position_id_for_symbol(symbol),
                "selected",
                "paper_order_plan",
                selected_payload,
                event_key=selected_event_key,
            )

    result_rows = []
    position_intent_rows = []
    owner_allows_submit, owner_block_reason = legacy_paper_trader_can_submit(config)
    can_submit = config.submit_orders and not plan_only and config.paper_trading_enabled and not config.live_trading_enabled and owner_allows_submit
    if config.submit_orders and config.live_trading_enabled:
        raise RuntimeError("Live trading is disabled by policy for this platform")
    if config.submit_orders and not plan_only and config.paper_trading_enabled and not config.live_trading_enabled and not owner_allows_submit:
        for order in plan.to_dict("records"):
            eligible = bool(order.get("order_eligible")) and int(order.get("suggested_quantity", 0) or 0) >= 1
            if str(order.get("trade_quality_status", "")).lower() in {"approved", "reduced"} and eligible:
                result_rows.append(_result_row(order, "dry_run", message=owner_block_reason))
                record_event_safely(
                    position_id_for_symbol(order.get("symbol", "")),
                    "guardrail_blocked",
                    "paper_trader",
                    {"symbol": order.get("symbol", ""), "reason": owner_block_reason, "stage": "execution_owner", "execution_owner": getattr(config, "execution_owner", "")},
                )
            else:
                message = format_reasons(order.get("trade_quality_reason", "trade_quality_rejected"))
                result_rows.append(_result_row(order, "rejected", message=message))
    elif can_submit and not plan.empty:
        client = asset_client or AlpacaPaperClient(config)
        context = load_submission_context(client)
        seen_client_ids: set[str] = set()
        for order in plan.to_dict("records"):
            eligible = bool(order.get("order_eligible")) and int(order.get("suggested_quantity", 0) or 0) >= 1
            if str(order.get("trade_quality_status", "")).lower() not in {"approved", "reduced"} or not eligible:
                message = format_reasons(order.get("trade_quality_reason", "trade_quality_rejected"))
                result_rows.append(_result_row(order, "rejected", message=message))
                record_event_safely(
                    position_id_for_symbol(order.get("symbol", "")),
                    "guardrail_blocked",
                    "paper_trader",
                    {"symbol": order.get("symbol", ""), "reason": message, "stage": "trade_quality_gate"},
                )
                continue
            request = {
                "symbol": order["symbol"],
                "qty": str(int(order.get("suggested_quantity", 0))),
                "side": order["side"],
                "type": order["type"],
                "time_in_force": order["time_in_force"],
                "extended_hours": bool(order.get("extended_hours", False)) and str(order.get("type", "")).lower() == "limit",
                "client_order_id": order["client_order_id"],
            }
            if str(order.get("type", "")).lower() == "limit":
                request["limit_price"] = order.get("limit_price")
            try:
                payload_check = validate_order_payload(request, max_order_notional=config.max_notional_per_order)
                if not payload_check.valid:
                    result_rows.append(_result_row(order, "rejected", message=payload_check.reason, diagnostics={"submitted_payload": str(request)}))
                    record_event_safely(
                        position_id_for_symbol(order.get("symbol", "")),
                        "guardrail_blocked",
                        "paper_trader",
                        {"symbol": order.get("symbol", ""), "reason": payload_check.reason, "stage": "order_payload_validation"},
                    )
                    continue
                allowed, guard_message = validate_order(order, client, context, seen_client_ids)
                if not allowed:
                    result_rows.append(_result_row(order, "rejected", message=guard_message))
                    record_event_safely(
                        position_id_for_symbol(order.get("symbol", "")),
                        "guardrail_blocked",
                        "paper_trader",
                        {"symbol": order.get("symbol", ""), "reason": guard_message, "stage": "submission_preflight"},
                    )
                    continue
                intent_decision, intent_row = guard_order_submission(
                    {**order, **request},
                    client=client,
                    config=PositionIntentConfig(
                        minimum_hold_minutes=30,
                        allow_short_selling=getattr(config, "allow_short_selling", True),
                    ),
                    cycle_id=stamp,
                    order_source="paper_trader",
                )
                if not intent_decision.allowed:
                    position_intent_rows.append(intent_row)
                    report_path = write_position_intent_report(position_intent_rows, stamp=stamp)
                    record_position_intent_block(intent_row, report_path=report_path)
                    result_rows.append(_result_row(order, "rejected", message=intent_decision.block_reason, diagnostics={"submitted_payload": str(request)}))
                    continue
                paper_only_guard(live_trading_enabled=config.live_trading_enabled)
                response = client.submit_order(request)
                result_rows.append(_result_row(order, "submitted", response.get("id", ""), response=response))
                submitted_lineage = order_lineage(order, broker_order_id=response.get("id", ""))
                record_event_safely(
                    position_id_for_symbol(order.get("symbol", "")),
                    "submitted",
                    "paper_trader",
                    enrich_activity_details(
                        {
                            "symbol": order.get("symbol", ""),
                            "order_id": response.get("id", ""),
                            "broker_order_id": response.get("id", ""),
                            "client_order_id": order.get("client_order_id", ""),
                            "side": order.get("side", ""),
                            "qty": order.get("suggested_quantity", ""),
                            "alpaca_status": response.get("status", ""),
                        },
                        submitted_lineage,
                    ),
                )
            except AlpacaAPIError as exc:
                result_rows.append(
                    _result_row(
                        order,
                        "error",
                        message="alpaca_order_submit_failed",
                        diagnostics={**exc.as_dict(), "submitted_payload": str(request)},
                    )
                )
                record_event_safely(
                    position_id_for_symbol(order.get("symbol", "")),
                    "broker_rejected",
                    "paper_trader",
                    {
                        "symbol": order.get("symbol", ""),
                        "reason": "alpaca_order_submit_failed",
                        **exc.as_dict(),
                    },
                )
            except Exception as exc:
                result_rows.append(_result_row(order, "error", message=f"order_submit_exception: {exc}", diagnostics={"submitted_payload": str(request)}))
                record_event_safely(
                    position_id_for_symbol(order.get("symbol", "")),
                    "broker_rejected",
                    "paper_trader",
                    {"symbol": order.get("symbol", ""), "reason": f"order_submit_exception: {exc}"},
                )
    else:
        for order in plan.to_dict("records"):
            eligible = bool(order.get("order_eligible")) and int(order.get("suggested_quantity", 0) or 0) >= 1
            if str(order.get("trade_quality_status", "")).lower() not in {"approved", "reduced"} or not eligible:
                message = format_reasons(order.get("trade_quality_reason", "trade_quality_rejected"))
                result_rows.append(_result_row(order, "rejected", message=message))
                record_event_safely(
                    position_id_for_symbol(order.get("symbol", "")),
                    "guardrail_blocked",
                    "paper_trader",
                    {"symbol": order.get("symbol", ""), "reason": message, "stage": "trade_quality_gate"},
                )
            else:
                message = "plan_only: no broker submission" if plan_only else "STOCKML_ALPACA_SUBMIT_ORDERS is false"
                result_rows.append(_result_row(order, "dry_run", message=message))

    results = pd.DataFrame(result_rows)
    results.to_csv(result_path, index=False)
    tracking_path, positions_path = _write_tracking_snapshot(results, config, stamp)
    return {
        "orders_planned": len(plan),
        "candidate_pool_rows": len(candidate_pool),
        "candidate_pool_size": config.candidate_pool_size,
        "orders_approved": int((plan.get("trade_quality_status", pd.Series(dtype=str)).astype(str).str.lower().isin(["approved", "reduced"])).sum()) if not plan.empty else 0,
        "orders_rejected": int((plan.get("trade_quality_status", pd.Series(dtype=str)).astype(str).str.lower() == "rejected").sum()) if not plan.empty else 0,
        "orders_submitted": sum(1 for row in result_rows if row["status"] == "submitted"),
        "result_submitted": sum(1 for row in result_rows if row["status"] == "submitted"),
        "result_rejected": sum(1 for row in result_rows if row["status"] == "rejected"),
        "result_error": sum(1 for row in result_rows if row["status"] == "error"),
        "result_dry_run": sum(1 for row in result_rows if row["status"] == "dry_run"),
        "dry_run": plan_only or not config.submit_orders or not owner_allows_submit,
        "execution_owner": getattr(config, "execution_owner", ""),
        "execution_owner_block_reason": "" if owner_allows_submit else owner_block_reason,
        "plan_only": plan_only,
        "shorting_enabled": config.allow_short_selling,
        "paper_trading_enabled": config.paper_trading_enabled,
        "live_trading_enabled": config.live_trading_enabled,
        "model_fresh": model_fresh,
        "model_fresh_reason": model_fresh_reason,
        "model_signal_path": model_signal_path,
        "candidate_pool_path": candidate_pool_path,
        "counterfactual_candidate_path": counterfactual.path,
        "plan_path": plan_path,
        "result_path": result_path,
        "tracking_path": tracking_path,
        "positions_path": positions_path,
    }


def refresh_order_tracking(result_file: Optional[Path] = None) -> dict[str, Path | int]:
    ensure_data_dirs()
    config = alpaca_config()
    if result_file is None:
        candidates = sorted(PORTAL_OUTPUTS_DIR.glob("08_alpaca_paper_order_results_*.csv"), key=lambda path: path.stat().st_mtime)
        result_file = candidates[-1] if candidates else None
    results = pd.read_csv(result_file, low_memory=False) if result_file and result_file.exists() else pd.DataFrame()
    stamp = timestamp()
    tracking_path, positions_path = _write_tracking_snapshot(results, config, stamp)
    reconcile_autopilot_state_from_tracking(
        tracking_path=tracking_path,
        positions_path=positions_path,
        orders_tracked=len(results),
    )
    return {
        "orders_tracked": len(results),
        "tracking_path": tracking_path,
        "positions_path": positions_path,
    }
