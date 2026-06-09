from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.common.paths import MODEL_OUTPUTS_DIR
from stockml.diagnostics.common import add_gain_columns, aggregate_edge, attach_forward_returns, gold_outcome_slice, latest_gold, latest_model, missing_frame, safe_read_csv, write_report


def build_intraday_promotion_ablation_report(stamp: str, *, signal_file: Path | None = None, gold_file: Path | None = None) -> object:
    signal_path = signal_file or latest_model("advanced_model_signal_table_*.csv")
    gold_path = gold_file or latest_gold()
    missing = []
    signals = safe_read_csv(signal_path)
    gold = gold_outcome_slice(gold_path, signals)
    if signals.empty:
        missing.append("advanced_model_signal_table")
    if gold.empty:
        missing.append("gold_training_panel")
    if not signals.empty and not {"directional_action", "directional_strength"}.intersection(signals.columns):
        missing.append("directional_or_intraday_adjustment_fields")
    output = MODEL_OUTPUTS_DIR / f"diagnostics_intraday_promotion_ablation_{stamp}.csv"
    if missing:
        return write_report("intraday_promotion_ablation", missing_frame("intraday_promotion_ablation", missing), output, missing)
    frame = add_gain_columns(attach_forward_returns(signals, gold))
    action = (frame["trade_action"] if "trade_action" in frame.columns else pd.Series("", index=frame.index)).astype(str).str.lower()
    directional = (frame["directional_action"] if "directional_action" in frame.columns else pd.Series("", index=frame.index)).astype(str).str.lower()
    reason = (frame["signal_reason"] if "signal_reason" in frame.columns else pd.Series("", index=frame.index)).astype(str).str.lower()
    frame["intraday_group"] = "nightly_only"
    frame.loc[directional.ne("") & directional.ne(action), "intraday_group"] = "intraday_adjusted"
    frame.loc[reason.str.contains("promot|intraday|vwap|volume|range", na=False), "intraday_group"] = "intraday_promoted"
    report = aggregate_edge(frame, ["diagnostic_side", "intraday_group"])
    return write_report("intraday_promotion_ablation", report, output)
