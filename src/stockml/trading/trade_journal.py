from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.common.paths import PAPER_TRADE_JOURNAL_DIR, ensure_data_dirs, timestamp
from stockml.decisions.reason_formatter import format_reasons


def lifecycle_state(row: pd.Series) -> str:
    quality = str(row.get("trade_quality_status", "") or "").lower()
    status = str(row.get("status", "") or "").lower()
    alpaca_status = str(row.get("alpaca_status", "") or "").lower()
    filled_qty = pd.to_numeric(row.get("filled_qty", 0), errors="coerce")
    if quality == "rejected" or status == "rejected":
        return "risk_rejected"
    if status == "dry_run":
        return "order_planned"
    if status == "submitted":
        if alpaca_status == "filled" or (not pd.isna(filled_qty) and filled_qty > 0):
            return "order_filled"
        return "order_submitted"
    if quality in {"approved", "reduced"}:
        return "order_planned"
    return "signal_generated"


def build_trade_journal(plan: pd.DataFrame, results: pd.DataFrame | None = None) -> pd.DataFrame:
    if plan.empty:
        return pd.DataFrame()
    result_cols = ["symbol", "status", "alpaca_status", "order_id", "filled_qty", "filled_avg_price", "message"]
    results = results if results is not None else pd.DataFrame()
    if results.empty:
        merged = plan.copy()
        for col in result_cols:
            if col != "symbol" and col not in merged.columns:
                merged[col] = ""
    else:
        keep = [col for col in result_cols if col in results.columns]
        merged = plan.merge(results[keep], on="symbol", how="left", suffixes=("", "_result"))
    merged["lifecycle_state"] = merged.apply(lifecycle_state, axis=1)
    if "trade_quality_reason" not in merged.columns:
        merged["trade_quality_reason"] = ""
    merged["readable_reason"] = merged["trade_quality_reason"].apply(format_reasons)
    columns = [
        "symbol",
        "company",
        "sector",
        "trade_action",
        "side",
        "lifecycle_state",
        "trade_quality_status",
        "readable_reason",
        "approved_notional",
        "suggested_quantity",
        "current_price",
        "stop_loss_price",
        "take_profit_price",
        "max_holding_days",
        "status",
        "alpaca_status",
        "order_id",
        "filled_qty",
        "filled_avg_price",
        "message",
        "pipeline_run_id",
        "cycle_id",
        "signal_id",
        "candidate_id",
        "event_key",
        "client_order_id",
        "broker_order_id",
        "position_id",
        "trade_id",
        "exit_decision_id",
        "order_intent",
        "strategy_mode",
        "session_mode",
        "candidate_source",
        "model_version",
        "lineage_warning",
    ]
    for col in columns:
        if col not in merged.columns:
            merged[col] = ""
    return merged[columns]


def write_trade_journal(journal: pd.DataFrame, stamp: str | None = None) -> Path:
    ensure_data_dirs()
    path = PAPER_TRADE_JOURNAL_DIR / f"paper_trade_journal_{stamp or timestamp()}.csv"
    journal.to_csv(path, index=False)
    return path
