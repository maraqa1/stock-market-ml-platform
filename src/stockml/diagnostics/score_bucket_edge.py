from __future__ import annotations

from pathlib import Path

from stockml.common.paths import MODEL_OUTPUTS_DIR
from stockml.diagnostics.common import (
    add_gain_columns,
    add_rank_decile,
    aggregate_edge,
    attach_forward_returns,
    gold_outcome_slice,
    has_forward_outcomes,
    latest_gold,
    latest_signal_history,
    missing_frame,
    safe_read_csv,
    write_report,
)


def build_score_bucket_edge_report(stamp: str, *, signal_file: Path | None = None, gold_file: Path | None = None) -> object:
    signal_path = signal_file or latest_signal_history()
    gold_path = gold_file or latest_gold()
    missing = []
    signals = safe_read_csv(signal_path)
    gold = gold_outcome_slice(gold_path, signals)
    if signals.empty:
        missing.append("walk_forward_predictions_or_signal_table")
    if not has_forward_outcomes(signals) and gold.empty:
        missing.append("gold_forward_outcomes")
    output = MODEL_OUTPUTS_DIR / f"diagnostics_score_bucket_edge_{stamp}.csv"
    if missing:
        return write_report("score_bucket_edge", missing_frame("score_bucket_edge", missing), output, missing)
    frame = attach_forward_returns(signals, gold)
    frame = add_gain_columns(add_rank_decile(frame))
    report = aggregate_edge(frame, ["date", "diagnostic_side", "score_bucket"])
    return write_report("score_bucket_edge", report, output)
