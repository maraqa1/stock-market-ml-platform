from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from stockml.common.paths import PORTAL_OUTPUTS_DIR, ensure_data_dirs, timestamp
from stockml.trading.alpaca_client import AlpacaPaperClient
from stockml.trading.config import alpaca_config
from stockml.trading.order_planner import build_order_plan, latest_signal_table


def _result_row(order: dict, status: str, order_id: str = "", message: str = "", response: Optional[dict] = None) -> dict:
    response = response or {}
    return {
        "symbol": order.get("symbol", ""),
        "status": status,
        "alpaca_status": response.get("status", ""),
        "order_id": order_id,
        "client_order_id": order.get("client_order_id", ""),
        "side": order.get("side", ""),
        "notional": order.get("notional", ""),
        "trade_action": order.get("trade_action", ""),
        "filled_qty": response.get("filled_qty", ""),
        "filled_avg_price": response.get("filled_avg_price", ""),
        "submitted_at": response.get("submitted_at", ""),
        "updated_at": response.get("updated_at", ""),
        "message": message,
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
            if not order_id or status in {"dry_run", "error"}:
                tracking_rows.append(row)
                continue
            try:
                live = client.get_order(order_id)
                tracking_rows.append(
                    {
                        **row,
                        "alpaca_status": live.get("status", row.get("alpaca_status", "")),
                        "filled_qty": live.get("filled_qty", row.get("filled_qty", "")),
                        "filled_avg_price": live.get("filled_avg_price", row.get("filled_avg_price", "")),
                        "submitted_at": live.get("submitted_at", row.get("submitted_at", "")),
                        "updated_at": live.get("updated_at", row.get("updated_at", "")),
                        "message": row.get("message", ""),
                    }
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
    plan = build_order_plan(signals, config)
    stamp = timestamp()
    plan_path = PORTAL_OUTPUTS_DIR / f"08_alpaca_paper_order_plan_{stamp}.csv"
    result_path = PORTAL_OUTPUTS_DIR / f"08_alpaca_paper_order_results_{stamp}.csv"
    plan.to_csv(plan_path, index=False)

    result_rows = []
    if config.submit_orders and not plan.empty:
        client = AlpacaPaperClient(config)
        for order in plan.to_dict("records"):
            request = {key: order[key] for key in ["symbol", "notional", "side", "type", "time_in_force", "extended_hours", "client_order_id"]}
            try:
                response = client.submit_order(request)
                result_rows.append(_result_row(order, "submitted", response.get("id", ""), response=response))
            except Exception as exc:
                result_rows.append(_result_row(order, "error", message=str(exc)))
    else:
        for order in plan.to_dict("records"):
            result_rows.append(_result_row(order, "dry_run", message="STOCKML_ALPACA_SUBMIT_ORDERS is false"))

    results = pd.DataFrame(result_rows)
    results.to_csv(result_path, index=False)
    tracking_path, positions_path = _write_tracking_snapshot(results, config, stamp)
    return {
        "orders_planned": len(plan),
        "orders_submitted": sum(1 for row in result_rows if row["status"] == "submitted"),
        "dry_run": not config.submit_orders,
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
