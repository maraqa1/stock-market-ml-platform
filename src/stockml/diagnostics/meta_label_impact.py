from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.common.paths import MODEL_OUTPUTS_DIR
from stockml.diagnostics.common import add_gain_columns, aggregate_edge, attach_forward_returns, gold_outcome_slice, has_forward_outcomes, latest_gold, latest_signal_history, missing_frame, safe_read_csv, write_report


def build_meta_label_impact_report(stamp: str, *, signal_file: Path | None = None, gold_file: Path | None = None) -> object:
    signal_path = signal_file or latest_signal_history()
    gold_path = gold_file or latest_gold()
    missing = []
    signals = safe_read_csv(signal_path)
    gold = gold_outcome_slice(gold_path, signals)
    if signals.empty:
        missing.append("walk_forward_predictions_or_signal_table")
    if not has_forward_outcomes(signals) and gold.empty:
        missing.append("gold_forward_outcomes")
    if not signals.empty and "meta_label_decision" not in signals.columns:
        missing.append("meta_label_decision")
    output = MODEL_OUTPUTS_DIR / f"diagnostics_meta_label_impact_{stamp}.csv"
    if missing:
        return write_report("meta_label_impact", missing_frame("meta_label_impact", missing), output, missing)
    frame = add_gain_columns(attach_forward_returns(signals, gold))
    decision = frame["meta_label_decision"].fillna("Missing").astype(str).str.strip().str.lower()
    frame["meta_label_group"] = "skipped_or_rejected"
    frame.loc[decision.eq("take trade"), "meta_label_group"] = "accepted"
    reason = frame["meta_label_reason"] if "meta_label_reason" in frame.columns else pd.Series("", index=frame.index)
    frame.loc[reason.astype(str).str.lower().eq("live_signal_mode_meta_label_skipped"), "meta_label_group"] = "live_signal_mode_skipped"
    report = aggregate_edge(frame, ["diagnostic_side", "meta_label_group"])
    return write_report("meta_label_impact", report, output)
