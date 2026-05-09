from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from stockml.decisions.reason_formatter import format_reasons
from stockml.common.paths import PORTAL_OUTPUTS_DIR, ensure_data_dirs, timestamp
from stockml.services.events import position_id_for_symbol, record_event_safely
from stockml.trading.alpaca_client import AlpacaAPIError, AlpacaPaperClient
from stockml.trading.config import alpaca_config
from stockml.trading.order_builder import validate_order_payload
from stockml.trading.order_planner import build_candidate_pool, build_order_plan, latest_signal_table
from stockml.trading.submission_guards import load_submission_context, validate_order


def _result_row(order: dict, status: str, order_id: str = "", message: str = "", response: Optional[dict] = None, diagnostics: Optional[dict] = None) -> dict:
    response = response or {}
    diagnostics = diagnostics or {}
    return {
        "symbol": order.get("symbol", ""),
        "status": status,
        "alpaca_status": response.get("status", ""),
        "order_id": order_id,
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


def _clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


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
                    record_event_safely(
                        position_id_for_symbol(tracked.get("symbol", "")),
                        "filled",
                        "alpaca_tracking",
                        {
                            "symbol": tracked.get("symbol", ""),
                            "order_id": tracked.get("order_id", ""),
                            "client_order_id": tracked.get("client_order_id", ""),
                            "filled_qty": tracked.get("filled_qty", ""),
                            "filled_avg_price": tracked.get("filled_avg_price", ""),
                            "alpaca_status": tracked.get("alpaca_status", ""),
                            "tracking_path": str(tracking_path),
                        },
                    )
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


def run_paper_trading(signal_file: Optional[Path] = None) -> dict[str, Path | int | bool]:
    ensure_data_dirs()
    config = alpaca_config()
    signals = latest_signal_table(signal_file)
    candidate_pool = build_candidate_pool(signals, config)
    plan = build_order_plan(signals, config)
    stamp = timestamp()
    candidate_pool_path = PORTAL_OUTPUTS_DIR / f"08_alpaca_paper_candidate_pool_{stamp}.csv"
    plan_path = PORTAL_OUTPUTS_DIR / f"08_alpaca_paper_order_plan_{stamp}.csv"
    result_path = PORTAL_OUTPUTS_DIR / f"08_alpaca_paper_order_results_{stamp}.csv"
    candidate_pool.to_csv(candidate_pool_path, index=False)
    plan.to_csv(plan_path, index=False)
    for selected in plan.to_dict("records"):
        selected_eligible = bool(selected.get("order_eligible")) and int(selected.get("suggested_quantity", 0) or 0) >= 1
        if str(selected.get("trade_quality_status", "")).lower() in {"approved", "reduced"} and selected_eligible:
            record_event_safely(
                position_id_for_symbol(selected.get("symbol", "")),
                "selected",
                "paper_order_plan",
                {
                    "symbol": selected.get("symbol", ""),
                    "side": selected.get("side", ""),
                    "trade_action": selected.get("trade_action", ""),
                    "trade_quality_status": selected.get("trade_quality_status", ""),
                    "approved_notional": selected.get("approved_notional", selected.get("notional", "")),
                    "suggested_quantity": selected.get("suggested_quantity", ""),
                    "plan_path": str(plan_path),
                },
            )

    result_rows = []
    can_submit = config.submit_orders and config.paper_trading_enabled and not config.live_trading_enabled
    if config.submit_orders and config.live_trading_enabled:
        raise RuntimeError("Live trading is disabled by policy for this platform")
    if can_submit and not plan.empty:
        client = AlpacaPaperClient(config)
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
                response = client.submit_order(request)
                result_rows.append(_result_row(order, "submitted", response.get("id", ""), response=response))
                record_event_safely(
                    position_id_for_symbol(order.get("symbol", "")),
                    "submitted",
                    "paper_trader",
                    {
                        "symbol": order.get("symbol", ""),
                        "order_id": response.get("id", ""),
                        "client_order_id": order.get("client_order_id", ""),
                        "side": order.get("side", ""),
                        "qty": order.get("suggested_quantity", ""),
                        "alpaca_status": response.get("status", ""),
                    },
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
                result_rows.append(_result_row(order, "dry_run", message="STOCKML_ALPACA_SUBMIT_ORDERS is false"))

    results = pd.DataFrame(result_rows)
    results.to_csv(result_path, index=False)
    tracking_path, positions_path = _write_tracking_snapshot(results, config, stamp)
    return {
        "orders_planned": len(plan),
        "candidate_pool_rows": len(candidate_pool),
        "orders_approved": int((plan.get("trade_quality_status", pd.Series(dtype=str)).astype(str).str.lower().isin(["approved", "reduced"])).sum()) if not plan.empty else 0,
        "orders_rejected": int((plan.get("trade_quality_status", pd.Series(dtype=str)).astype(str).str.lower() == "rejected").sum()) if not plan.empty else 0,
        "orders_submitted": sum(1 for row in result_rows if row["status"] == "submitted"),
        "dry_run": not config.submit_orders,
        "shorting_enabled": config.allow_short_selling,
        "paper_trading_enabled": config.paper_trading_enabled,
        "live_trading_enabled": config.live_trading_enabled,
        "candidate_pool_path": candidate_pool_path,
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
    return {
        "orders_tracked": len(results),
        "tracking_path": tracking_path,
        "positions_path": positions_path,
    }
