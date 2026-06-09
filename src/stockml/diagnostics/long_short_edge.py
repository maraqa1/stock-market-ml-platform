from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.common.paths import MODEL_OUTPUTS_DIR
from stockml.diagnostics.common import add_gain_columns, aggregate_edge, attach_forward_returns, gold_outcome_slice, has_forward_outcomes, latest_gold, latest_signal_history, missing_frame, safe_read_csv, write_report


def _drawdown_estimate_bps(group: pd.DataFrame) -> float:
    gains = pd.to_numeric(group.get("gain_after_cost_bps"), errors="coerce").fillna(0.0)
    curve = gains.cumsum()
    return float((curve - curve.cummax()).min()) if not curve.empty else float("nan")


def build_long_short_edge_report(stamp: str, *, signal_file: Path | None = None, gold_file: Path | None = None) -> object:
    signal_path = signal_file or latest_signal_history()
    gold_path = gold_file or latest_gold()
    missing = []
    signals = safe_read_csv(signal_path)
    gold = gold_outcome_slice(gold_path, signals)
    if signals.empty:
        missing.append("walk_forward_predictions_or_signal_table")
    if not has_forward_outcomes(signals) and gold.empty:
        missing.append("gold_forward_outcomes")
    output = MODEL_OUTPUTS_DIR / f"diagnostics_long_short_edge_{stamp}.csv"
    if missing:
        return write_report("long_short_edge", missing_frame("long_short_edge", missing), output, missing)
    frame = add_gain_columns(attach_forward_returns(signals, gold))
    report = aggregate_edge(frame, ["diagnostic_side"])
    sectors = frame.groupby("diagnostic_side")["sector"].agg(lambda values: values.value_counts(dropna=False).head(5).to_dict() if "sector" in frame else {})
    drawdown = frame.groupby("diagnostic_side").apply(_drawdown_estimate_bps)
    if not report.empty:
        report["worst_drawdown_estimate_bps"] = report["diagnostic_side"].map(drawdown)
        report["top_sector_concentration"] = report["diagnostic_side"].map(sectors).astype(str)
    return write_report("long_short_edge", report, output)
