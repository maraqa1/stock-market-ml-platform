from __future__ import annotations

from pathlib import Path

import pandas as pd

from portal.services.latest_file_reader import file_status, latest_file, safe_read_csv
from stockml.decisions.reason_formatter import format_reasons
from stockml.services.events import position_id_for_symbol
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


def _records_by_rank(frame, limit: int = 50) -> list[dict]:
    if frame.empty:
        return []
    out = frame.copy()
    if "candidate_rank" in out.columns:
        out["__rank"] = pd.to_numeric(out["candidate_rank"], errors="coerce").fillna(999999)
        out = out.sort_values("__rank").drop(columns="__rank")
    else:
        out = _sort_by_confidence(out)
    out = out.head(limit).fillna("")
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


def _action_counts(frame) -> dict[str, int]:
    if frame.empty or "trade_action" not in frame.columns:
        return {}
    return {str(key): int(value) for key, value in frame["trade_action"].fillna("").value_counts().to_dict().items() if str(key)}


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


def _numeric_value(value, default: float = 0.0) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return float(default if pd.isna(parsed) else parsed)


def _text_value(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _status_from_basket_row(row: pd.Series) -> str:
    alpaca_status = str(row.get("alpaca_status", "") or "").strip().lower()
    result_status = str(row.get("status", "") or "").strip().lower()
    quality_status = str(row.get("trade_quality_status", "") or "").strip().lower()
    if alpaca_status == "filled":
        return "filled"
    if alpaca_status in {"partially_filled", "partial"}:
        return "partial"
    if result_status == "submitted":
        return "submitted"
    if result_status == "error":
        return "failed"
    if result_status == "rejected" or quality_status == "rejected":
        return "rejected"
    if quality_status == "reduced":
        return "trimmed"
    if quality_status == "approved":
        return "approved"
    return result_status or quality_status or "pending"


def _build_basket_rows(plan: pd.DataFrame, results: pd.DataFrame) -> list[dict]:
    merged = _merged_plan_results(plan, results)
    if merged.empty:
        return []
    rows = []
    for _, row in merged.iterrows():
        symbol = str(row.get("symbol") or row.get("symbol_result") or "").upper()
        side = row.get("side") or row.get("side_result") or ""
        trade_action = row.get("trade_action") or row.get("trade_action_result") or ""
        result_status = row.get("status", "")
        message = row.get("message", "")
        quality_reason = row.get("trade_quality_reason", "")
        note = format_reasons(message or quality_reason or "")
        planned_qty = int(_numeric_value(row.get("suggested_quantity", 0), 0))
        filled_qty = _numeric_value(row.get("filled_qty", 0), 0)
        avg_fill = _numeric_value(row.get("filled_avg_price", 0), 0)
        submitted = str(result_status or "").lower() == "submitted"
        rows.append(
            {
                "symbol": symbol,
                "side": side,
                "trade_action": trade_action,
                "planned_quantity": planned_qty,
                "sent_quantity": planned_qty if submitted else "",
                "filled_quantity": "" if filled_qty == 0 else filled_qty,
                "avg_fill": "" if avg_fill == 0 else avg_fill,
                "basket_status": _status_from_basket_row(row),
                "reason_note": note,
                "order_id": row.get("order_id", "") or "",
                "client_order_id": row.get("client_order_id", "") or "",
                "position_id": position_id_for_symbol(symbol) if symbol else "",
            }
        )
    return rows


def _merged_plan_results(plan: pd.DataFrame, results: pd.DataFrame) -> pd.DataFrame:
    if plan.empty and results.empty:
        return pd.DataFrame()
    plan_frame = plan.copy()
    result_frame = results.copy()
    if "client_order_id" not in plan_frame.columns:
        plan_frame["client_order_id"] = ""
    if "client_order_id" not in result_frame.columns:
        result_frame["client_order_id"] = ""
    return plan_frame.merge(
        result_frame,
        on="client_order_id",
        how="outer",
        suffixes=("", "_result"),
    )


def _rejected_trimmed_source(row: pd.Series) -> str:
    result_status = _text_value(row.get("status", "")).lower()
    if _status_from_basket_row(row) == "trimmed":
        return "Guardrail"
    if result_status == "error" or _text_value(row.get("api_error", "")) or _numeric_value(row.get("http_status", 0), 0) >= 400:
        return "Broker"
    if _text_value(row.get("trade_quality_reason", "")) or _text_value(row.get("message", "")):
        return "Guardrail"
    return "Pipeline"


def _build_rejected_trimmed_rows(plan: pd.DataFrame, results: pd.DataFrame) -> list[dict]:
    merged = _merged_plan_results(plan, results)
    if merged.empty:
        return []
    rows = []
    for _, row in merged.iterrows():
        basket_status = _status_from_basket_row(row)
        if basket_status not in {"rejected", "trimmed", "failed"}:
            continue
        symbol = str(row.get("symbol") or row.get("symbol_result") or "").upper()
        side = row.get("side") or row.get("side_result") or ""
        trade_action = row.get("trade_action") or row.get("trade_action_result") or ""
        message = row.get("message", "")
        quality_reason = row.get("trade_quality_reason", "")
        planned_qty = int(_numeric_value(row.get("suggested_quantity", 0), 0))
        rows.append(
            {
                "symbol": symbol,
                "side": side,
                "trade_action": trade_action,
                "planned_quantity": planned_qty,
                "status": basket_status,
                "source": _rejected_trimmed_source(row),
                "reason": format_reasons(message or quality_reason or ""),
                "time": row.get("updated_at", "") or row.get("submitted_at", "") or "",
                "client_order_id": row.get("client_order_id", "") or "",
                "position_id": position_id_for_symbol(symbol) if symbol else "",
            }
        )
    return rows


def _execution_quality(results: pd.DataFrame, tracking: pd.DataFrame) -> list[dict]:
    status_counts = _status_counts(results)
    tracking_counts = _status_counts(tracking, "alpaca_status")
    submitted = int(status_counts.get("submitted", 0))
    filled = int(tracking_counts.get("filled", 0))
    partial = int(tracking_counts.get("partially_filled", 0) + tracking_counts.get("partial", 0))
    rejected = int(status_counts.get("rejected", 0) + status_counts.get("error", 0))
    fill_ratio = f"{(filled / submitted * 100):.1f}%" if submitted else "Not available"
    return [
        {"label": "Submitted", "value": submitted, "status": "submitted" if submitted else "pending"},
        {"label": "Filled", "value": filled, "status": "filled" if filled else "pending"},
        {"label": "Partial fills", "value": partial, "status": "partial" if partial else "safe"},
        {"label": "Rejected / Error", "value": rejected, "status": "rejected" if rejected else "safe"},
        {"label": "Fill ratio", "value": fill_ratio, "status": "safe" if filled and submitted else "pending"},
        {"label": "Slippage", "value": "Not available", "status": "pending"},
        {"label": "Latency", "value": "Not available", "status": "pending"},
    ]


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
    basket_rows = _build_basket_rows(plan, results)
    rejected_trimmed_rows = _build_rejected_trimmed_rows(plan, results)
    basket_symbols = []
    if not plan.empty and "symbol" in plan.columns:
        basket_symbols = sorted({str(symbol).upper() for symbol in plan["symbol"].dropna()})
    candidate_pool_sectors: list[str] = []
    if not candidate_pool.empty and "sector" in candidate_pool.columns:
        candidate_pool_sectors = sorted({str(sector) for sector in candidate_pool["sector"].dropna() if str(sector).strip()})

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
        "candidate_pool_status_counts": _status_counts(candidate_pool, "trade_quality_status"),
        "candidate_pool_action_counts": _action_counts(candidate_pool),
        "candidate_pool_sectors": candidate_pool_sectors,
        "basket_symbols": basket_symbols,
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
        "execution_quality": _execution_quality(results, tracking),
        "plan_rows": _records(plan),
        "result_rows": _records(results),
        "tracking_rows": _records(tracking),
        "position_rows": _records(positions),
        "operator_action_rows": _records(actions, limit=10),
        "candidate_pool_rows": _records_by_rank(candidate_pool, limit=100),
        "basket_rows": basket_rows,
        "rejected_trimmed_rows": rejected_trimmed_rows,
        "rejected_trimmed_count": len(rejected_trimmed_rows),
        "plan_columns": [
            "symbol", "trade_quality_status", "trade_quality_reason", "side", "notional", "approved_notional",
            "suggested_quantity", "current_price", "stop_loss_price", "take_profit_price", "risk_tier",
            "volatility_tier", "liquidity_tier", "market_cap", "avg_dollar_volume_20d", "volatility_20d",
            "confidence_score", "side_probability", "probability_edge", "risk_adjusted_score",
            "order_eligible", "no_decision_reason",
        ],
        "candidate_pool_columns": [
            "candidate_rank", "symbol", "trade_action", "side", "trade_quality_status", "approved_notional",
            "suggested_quantity", "current_price", "risk_tier", "volatility_tier", "liquidity_tier",
            "confidence_score", "risk_adjusted_score", "trade_quality_reason",
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
