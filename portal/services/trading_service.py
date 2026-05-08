from __future__ import annotations

from pathlib import Path

import pandas as pd

from portal.services.latest_file_reader import file_status, latest_file, safe_read_csv
from stockml.trading.config import alpaca_config


def _records(frame, limit: int = 50) -> list[dict]:
    if frame.empty:
        return []
    return _sort_by_confidence(frame).head(limit).fillna("").to_dict("records")


def _sort_by_confidence(frame):
    if frame.empty:
        return frame
    out = frame.copy()
    sort_columns = []
    ascending = []
    for column in ["side_probability", "probability_edge", "risk_adjusted_score"]:
        if column in out.columns:
            values = pd.to_numeric(out[column], errors="coerce")
            if column == "probability_edge":
                values = values.abs()
            sort_key = f"__sort_{column}"
            out[sort_key] = values.fillna(float("-inf"))
            sort_columns.append(sort_key)
            ascending.append(False)
    if not sort_columns:
        return out
    return out.sort_values(sort_columns, ascending=ascending).drop(columns=sort_columns)


def _side_counts(plan) -> dict[str, int]:
    if plan.empty or "side" not in plan.columns:
        return {}
    return {str(key): int(value) for key, value in plan["side"].value_counts().to_dict().items()}


def _status_counts(results, column: str = "status") -> dict[str, int]:
    if results.empty or column not in results.columns:
        return {}
    return {str(key): int(value) for key, value in results[column].fillna("").value_counts().to_dict().items() if str(key)}


def _total_notional(plan) -> float:
    if plan.empty or "notional" not in plan.columns:
        return 0.0
    return float(pd.to_numeric(plan["notional"], errors="coerce").fillna(0).sum())


def trading_context(root: Path) -> dict:
    config = alpaca_config()
    plan_file = latest_file(root, "portal_outputs", "08_alpaca_paper_order_plan_*.csv")
    result_file = latest_file(root, "portal_outputs", "08_alpaca_paper_order_results_*.csv")
    tracking_file = latest_file(root, "portal_outputs", "08_alpaca_paper_order_tracking_*.csv")
    positions_file = latest_file(root, "portal_outputs", "08_alpaca_paper_positions_*.csv")
    plan = safe_read_csv(plan_file, nrows=500)
    results = safe_read_csv(result_file, nrows=500)
    tracking = safe_read_csv(tracking_file, nrows=500)
    positions = safe_read_csv(positions_file, nrows=500)
    status_counts = _status_counts(results)
    tracking_status_counts = _status_counts(tracking, "alpaca_status")
    dry_run = not config.submit_orders or bool(status_counts.get("dry_run", 0))

    guardrails = [
        {"label": "Submit orders", "value": "Disabled" if not config.submit_orders else "Enabled", "status": "safe" if not config.submit_orders else "warning"},
        {"label": "Max orders", "value": config.max_orders, "status": "safe"},
        {"label": "Max order notional", "value": config.max_notional_per_order, "status": "safe"},
        {"label": "Max basket notional", "value": config.max_total_notional, "status": "safe"},
        {"label": "Minimum trade price", "value": config.min_trade_price, "status": "safe"},
        {"label": "Max sector fraction", "value": config.max_sector_fraction, "status": "safe"},
        {"label": "Minimum side probability", "value": config.min_side_probability, "status": "safe"},
        {"label": "Minimum probability edge", "value": config.min_abs_probability_edge, "status": "safe"},
    ]

    return {
        "data_source": "CSV artifacts",
        "dry_run": dry_run,
        "orders_planned": len(plan),
        "orders_submitted": int(status_counts.get("submitted", 0)),
        "orders_rejected": int(status_counts.get("error", 0) + status_counts.get("rejected", 0)),
        "orders_tracked": len(tracking),
        "open_orders": int(tracking_status_counts.get("new", 0) + tracking_status_counts.get("accepted", 0) + tracking_status_counts.get("pending_new", 0)),
        "filled_orders": int(tracking_status_counts.get("filled", 0)),
        "total_notional": _total_notional(plan),
        "side_counts": _side_counts(plan),
        "status_counts": status_counts,
        "tracking_status_counts": tracking_status_counts,
        "guardrails": guardrails,
        "plan_rows": _records(plan),
        "result_rows": _records(results),
        "tracking_rows": _records(tracking),
        "position_rows": _records(positions),
        "plan_columns": [
            "symbol", "trade_quality_status", "trade_quality_reason", "side", "notional", "approved_notional",
            "suggested_quantity", "current_price", "stop_loss_price", "take_profit_price", "risk_tier",
            "volatility_tier", "liquidity_tier", "side_probability", "probability_edge", "risk_adjusted_score",
        ],
        "result_columns": ["symbol", "status", "alpaca_status", "order_id", "client_order_id", "side", "notional", "filled_qty", "filled_avg_price", "message"],
        "tracking_columns": ["symbol", "status", "alpaca_status", "order_id", "client_order_id", "side", "notional", "filled_qty", "filled_avg_price", "updated_at", "message"],
        "position_columns": ["symbol", "qty", "market_value", "cost_basis", "unrealized_pl", "unrealized_plpc", "current_price"],
        "files": [
            file_status(plan_file, "Alpaca order plan"),
            file_status(result_file, "Alpaca order results"),
            file_status(tracking_file, "Alpaca order tracking"),
            file_status(positions_file, "Alpaca positions"),
        ],
    }
