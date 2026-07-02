from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.experiments.raw_candidate_experiment_policy import experiment_dir, project_root, trades_ledger_path


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(0.0, index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def _group(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["group", "trades", "winners", "losers", "realized_pnl", "unrealized_pnl"])
    label = frame[column].fillna("").astype(str).replace("", "not_available") if column in frame.columns else pd.Series("not_available", index=frame.index)
    tmp = frame.copy()
    tmp["__group"] = label
    tmp["__realized"] = _num(tmp, "realized_pnl")
    tmp["__unrealized"] = _num(tmp, "unrealized_pnl")
    rows = []
    for name, group in tmp.groupby("__group", dropna=False):
        pnl = group["__realized"] + group["__unrealized"]
        rows.append(
            {
                "group": name,
                "trades": len(group),
                "winners": int((pnl > 0).sum()),
                "losers": int((pnl < 0).sum()),
                "realized_pnl": round(float(group["__realized"].sum()), 2),
                "unrealized_pnl": round(float(group["__unrealized"].sum()), 2),
            }
        )
    return pd.DataFrame(rows).sort_values(["realized_pnl", "unrealized_pnl"], ascending=[False, False])


def build_raw_candidate_experiment_attribution(
    *,
    root: Path | None = None,
    trades_file: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = project_root(root)
    now = now or datetime.now(timezone.utc)
    trades_file = trades_file or trades_ledger_path(root, now.date())
    out_dir = experiment_dir(root)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"raw_candidate_experiment_attribution_{stamp}.csv"
    md_path = out_dir / f"raw_candidate_experiment_summary_{stamp}.md"
    if trades_file is None or not trades_file.exists() or trades_file.stat().st_size == 0:
        frame = pd.DataFrame([{"section": "missing_data", "group": "trades_ledger_missing", "trades": 0}])
        frame.to_csv(csv_path, index=False)
        md_path.write_text("# Raw Candidate Experiment Summary\n\nStatus: insufficient_data\nReason: trades ledger missing.\n", encoding="utf-8")
        return {"status": "insufficient_data", "csv_path": csv_path, "markdown_path": md_path, "rows": 1}
    trades = pd.read_csv(trades_file, low_memory=False)
    if trades.empty:
        frame = pd.DataFrame([{"section": "missing_data", "group": "trades_ledger_empty", "trades": 0}])
        frame.to_csv(csv_path, index=False)
        md_path.write_text("# Raw Candidate Experiment Summary\n\nStatus: insufficient_data\nReason: trades ledger empty.\n", encoding="utf-8")
        return {"status": "insufficient_data", "csv_path": csv_path, "markdown_path": md_path, "rows": 1}

    sections = []
    for section, column in [
        ("by_original_block_reason", "original_block_reasons"),
        ("by_trade_action", "trade_action"),
        ("by_directional_action", "directional_action"),
        ("by_no_decision", "no_decision_experiment"),
        ("by_original_status", "original_status"),
        ("by_side", "side"),
        ("by_normal_gate_pass", "would_have_passed_normal_gates"),
    ]:
        grouped = _group(trades, column)
        grouped.insert(0, "section", section)
        sections.append(grouped)
    report = pd.concat(sections, ignore_index=True) if sections else pd.DataFrame()
    report.to_csv(csv_path, index=False)
    pnl = _num(trades, "realized_pnl") + _num(trades, "unrealized_pnl")
    winners = int((pnl > 0).sum())
    losers = int((pnl < 0).sum())
    md_path.write_text(
        "\n".join(
            [
                "# Raw Candidate Experiment Summary",
                "",
                f"Status: ok",
                f"Total experiment trades: {len(trades)}",
                f"Winners: {winners}",
                f"Losers: {losers}",
                f"Total P&L: {round(float(pnl.sum()), 2)}",
                "",
                "Normal gated strategy is not modified by this report.",
                "Use this only for attribution and gate-learning analysis.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {"status": "ok", "csv_path": csv_path, "markdown_path": md_path, "rows": len(report)}
