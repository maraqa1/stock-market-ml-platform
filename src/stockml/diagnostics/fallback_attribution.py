from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.common.paths import TRADING_DIR
from stockml.diagnostics.common import add_gain_columns, aggregate_edge, attach_forward_returns, gold_outcome_slice, latest_gold, latest_model, missing_frame, safe_read_csv, write_report


FALLBACK_PATTERNS = {
    "per-symbol forecast fallback": "per_symbol_forecast_fallback",
    "near-miss fallback": "near_miss_fallback",
    "plan fallback": "plan_fallback",
    "flat account fallback": "flat_account_fallback",
}


def build_fallback_attribution_report(stamp: str, *, signal_file: Path | None = None, gold_file: Path | None = None) -> object:
    signal_path = signal_file or latest_model("advanced_model_signal_table_*.csv")
    gold_path = gold_file or latest_gold()
    signals = safe_read_csv(signal_path)
    gold = gold_outcome_slice(gold_path, signals)
    missing = []
    if signals.empty:
        missing.append("advanced_model_signal_table")
    if gold.empty:
        missing.append("gold_training_panel")
    output = TRADING_DIR / f"diagnostics_fallback_attribution_{stamp}.csv"
    if missing:
        return write_report("fallback_attribution", missing_frame("fallback_attribution", missing), output, missing)
    frame = add_gain_columns(attach_forward_returns(signals, gold))
    text = (
        (frame["signal_reason"] if "signal_reason" in frame.columns else pd.Series("", index=frame.index)).astype(str)
        + "|"
        + (frame["trade_quality_reason"] if "trade_quality_reason" in frame.columns else pd.Series("", index=frame.index)).astype(str)
        + "|"
        + (frame["position_sizing_reason"] if "position_sizing_reason" in frame.columns else pd.Series("", index=frame.index)).astype(str)
    ).str.lower()
    frame["source_group"] = "main_model"
    for marker, group in FALLBACK_PATTERNS.items():
        frame.loc[text.str.contains(marker, na=False), "source_group"] = group
    report = aggregate_edge(frame, ["diagnostic_side", "source_group"])
    return write_report("fallback_attribution", report, output)
