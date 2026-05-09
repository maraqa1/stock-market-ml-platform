from __future__ import annotations

from pathlib import Path

import pandas as pd

from portal.services.latest_file_reader import file_status, latest_file, safe_read_csv
from stockml.decisions.reason_formatter import format_reasons
from stockml.trading.config import alpaca_config
from stockml.trading.manual_position_actions import apply_manual_position_action
from stockml.trading.paper_trader import refresh_order_tracking
from stockml.trading.pnl_tracker import position_pnl_summary, write_pnl_summary
from stockml.trading.trade_journal import build_trade_journal, write_trade_journal


def _records(frame, limit: int = 50) -> list[dict]:
    if frame.empty:
        return []
    out = _sort_by_confidence(frame).head(limit).fillna("")
    for column in ["trade_quality_reason", "message"]:
        if column in out.columns:
            out[column] = out[column].apply(format_reasons)
    return out.to_dict("records")


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


def _sum_column(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())


def _position_summary(positions: pd.DataFrame) -> dict[str, float | int]:
    market_value = _sum_column(positions, "market_value")
    cost_basis = _sum_column(positions, "cost_basis")
    unrealized_pl = _sum_column(positions, "unrealized_pl")
    unrealized_plpc = unrealized_pl / cost_basis if cost_basis else 0.0
    return {
        "position_count": int(len(positions)),
        "position_market_value": market_value,
        "position_cost_basis": cost_basis,
        "position_unrealized_pl": unrealized_pl,
        "position_unrealized_plpc": unrealized_plpc,
        "position_pnl_class": "positive" if unrealized_pl > 0 else "negative" if unrealized_pl < 0 else "flat",
    }


def position_action(root: Path, symbol: str, action: str) -> dict:
    result = apply_manual_position_action(symbol, action)
    refresh_trading_artifacts(root)
    return result


def refresh_trading_artifacts(root: Path) -> dict[str, str | int]:
    refreshed = refresh_order_tracking()
    plan_file = latest_file(root, "portal_outputs", "08_alpaca_paper_order_plan_*.csv")
    candidate_pool_file = latest_file(root, "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
    result_file = latest_file(root, "portal_outputs", "08_alpaca_paper_order_results_*.csv")
    plan = safe_read_csv(plan_file, nrows=1000)
    results = safe_read_csv(result_file, nrows=1000)
    positions = safe_read_csv(Path(refreshed["positions_path"]), nrows=1000)
    journal = build_trade_journal(plan, results)
    pnl = position_pnl_summary(positions)
    journal_path = write_trade_journal(journal)
    pnl_path = write_pnl_summary(pnl)
    return {
        "orders_tracked": int(refreshed["orders_tracked"]),
        "tracking_path": str(refreshed["tracking_path"]),
        "positions_path": str(refreshed["positions_path"]),
        "journal_path": str(journal_path),
        "pnl_path": str(pnl_path),
    }


def lifecycle_context(root: Path) -> dict:
    journal_file = latest_file(root, "paper_trade_journal", "paper_trade_journal_*.csv")
    pnl_file = latest_file(root, "paper_pnl", "paper_pnl_*.csv")
    decisions_file = latest_file(root, "agent_decisions", "position_decisions_*.csv")
    journal = safe_read_csv(journal_file, nrows=1000)
    pnl = safe_read_csv(pnl_file, nrows=1000)
    decisions = safe_read_csv(decisions_file, nrows=1000)
    state_counts = _status_counts(journal, "lifecycle_state")
    quality_counts = _status_counts(journal, "trade_quality_status")
    decision_counts = _status_counts(decisions, "decision")
    return {
        "data_source": "CSV artifacts",
        "journal_rows_count": len(journal),
        "risk_rejected_count": int(state_counts.get("risk_rejected", 0)),
        "order_planned_count": int(state_counts.get("order_planned", 0)),
        "order_submitted_count": int(state_counts.get("order_submitted", 0)),
        "order_filled_count": int(state_counts.get("order_filled", 0)),
        "position_count": len(pnl),
        "market_value": _sum_column(pnl, "market_value"),
        "unrealized_pl": _sum_column(pnl, "unrealized_pl"),
        "state_counts": state_counts,
        "quality_counts": quality_counts,
        "decision_counts": decision_counts,
        "journal_rows": _records(journal, limit=200),
        "pnl_rows": _records(pnl, limit=200),
        "decision_rows": _records(decisions, limit=200),
        "journal_columns": [
            "symbol", "lifecycle_state", "trade_quality_status", "readable_reason", "approved_notional",
            "suggested_quantity", "current_price", "stop_loss_price", "take_profit_price", "status",
            "alpaca_status", "filled_qty", "filled_avg_price",
        ],
        "pnl_columns": ["symbol", "qty", "market_value", "cost_basis", "unrealized_pl", "unrealized_plpc"],
        "decision_columns": [
            "symbol", "decision", "recommended_action", "decision_reason", "latest_signal", "signal_age_minutes",
            "replacement_symbol", "replacement_side", "replacement_rank", "current_price", "stop_loss_price",
            "take_profit_price", "unrealized_pl", "unrealized_plpc",
        ],
        "files": [
            file_status(journal_file, "Paper trade journal"),
            file_status(pnl_file, "Paper P&L"),
            file_status(decisions_file, "Position decisions"),
        ],
    }


def trading_context(root: Path) -> dict:
    config = alpaca_config()
    plan_file = latest_file(root, "portal_outputs", "08_alpaca_paper_order_plan_*.csv")
    candidate_pool_file = latest_file(root, "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
    result_file = latest_file(root, "portal_outputs", "08_alpaca_paper_order_results_*.csv")
    tracking_file = latest_file(root, "portal_outputs", "08_alpaca_paper_order_tracking_*.csv")
    positions_file = latest_file(root, "portal_outputs", "08_alpaca_paper_positions_*.csv")
    actions_file = latest_file(root, "operator_actions", "operator_position_actions_*.csv")
    plan = safe_read_csv(plan_file, nrows=500)
    candidate_pool = safe_read_csv(candidate_pool_file, nrows=500)
    results = safe_read_csv(result_file, nrows=500)
    tracking = safe_read_csv(tracking_file, nrows=500)
    positions = safe_read_csv(positions_file, nrows=500)
    actions = safe_read_csv(actions_file, nrows=100)
    status_counts = _status_counts(results)
    tracking_status_counts = _status_counts(tracking, "alpaca_status")
    dry_run = not config.submit_orders or bool(status_counts.get("dry_run", 0))
    position_summary = _position_summary(positions)

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
        "candidate_pool_count": len(candidate_pool),
        "orders_submitted": int(status_counts.get("submitted", 0)),
        "orders_rejected": int(status_counts.get("error", 0) + status_counts.get("rejected", 0)),
        "orders_tracked": len(tracking),
        "open_orders": int(tracking_status_counts.get("new", 0) + tracking_status_counts.get("accepted", 0) + tracking_status_counts.get("pending_new", 0)),
        "filled_orders": int(tracking_status_counts.get("filled", 0)),
        "total_notional": _total_notional(plan),
        **position_summary,
        "side_counts": _side_counts(plan),
        "status_counts": status_counts,
        "tracking_status_counts": tracking_status_counts,
        "guardrails": guardrails,
        "plan_rows": _records(plan),
        "result_rows": _records(results),
        "tracking_rows": _records(tracking),
        "position_rows": _records(positions),
        "operator_action_rows": _records(actions, limit=10),
        "candidate_pool_rows": _records(candidate_pool, limit=100),
        "plan_columns": [
            "symbol", "trade_quality_status", "trade_quality_reason", "side", "notional", "approved_notional",
            "suggested_quantity", "current_price", "stop_loss_price", "take_profit_price", "risk_tier",
            "volatility_tier", "liquidity_tier", "market_cap", "avg_dollar_volume_20d", "volatility_20d",
            "confidence_score", "side_probability", "probability_edge", "risk_adjusted_score",
            "order_eligible", "no_decision_reason",
        ],
        "candidate_pool_columns": [
            "candidate_rank", "symbol", "trade_action", "trade_quality_status", "trade_quality_reason", "side",
            "approved_notional", "suggested_quantity", "current_price", "stop_loss_price", "take_profit_price",
            "risk_tier", "volatility_tier", "liquidity_tier", "confidence_score", "risk_adjusted_score",
            "order_eligible",
        ],
        "result_columns": [
            "symbol", "status", "alpaca_status", "order_id", "client_order_id", "side", "notional",
            "suggested_quantity", "filled_qty", "filled_avg_price", "message", "http_status", "request_id", "api_error",
        ],
        "tracking_columns": [
            "symbol", "status", "alpaca_status", "order_id", "client_order_id", "side", "notional",
            "suggested_quantity", "filled_qty", "filled_avg_price", "updated_at", "message", "http_status", "request_id",
        ],
        "position_columns": ["symbol", "qty", "market_value", "cost_basis", "unrealized_pl", "unrealized_plpc", "current_price"],
        "operator_action_columns": ["timestamp", "symbol", "operator_action", "status", "message", "order_id", "alpaca_status"],
        "files": [
            file_status(plan_file, "Alpaca order plan"),
            file_status(candidate_pool_file, "Alpaca candidate pool"),
            file_status(result_file, "Alpaca order results"),
            file_status(tracking_file, "Alpaca order tracking"),
            file_status(positions_file, "Alpaca positions"),
            file_status(actions_file, "Manual position actions"),
        ],
    }
