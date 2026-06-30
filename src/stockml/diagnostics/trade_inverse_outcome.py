from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from stockml.common.paths import TRADING_DIR, timestamp
from stockml.diagnostics.common import safe_read_csv

REPORT_COLUMNS = [
    "symbol",
    "actual_side",
    "inverse_side",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "quantity",
    "actual_pnl",
    "inverse_pnl_before_incremental_costs",
    "actual_return_pct",
    "inverse_return_pct_before_incremental_costs",
    "candidate_source",
    "strategy_mode",
    "session_mode",
    "exit_reason",
    "lineage_quality",
    "lineage_warning",
    "inversion_evidence",
]

SUMMARY_COLUMNS = [
    "trade_count",
    "actual_total_pnl",
    "inverse_total_pnl_before_incremental_costs",
    "actual_winners",
    "actual_losers",
    "inverse_winners",
    "inverse_losers",
    "actual_hit_rate",
    "inverse_hit_rate_before_incremental_costs",
    "all_actual_losers",
    "inverse_beats_actual",
    "sample_size_warning",
    "lineage_warning",
    "recommended_action",
]


@dataclass(frozen=True)
class TradeInverseOutcomeResult:
    report: pd.DataFrame
    summary: pd.DataFrame
    report_path: Path | None = None
    summary_path: Path | None = None


def _latest_ledger() -> Path | None:
    files = sorted((TRADING_DIR / "diagnostics").glob("trade_ledger_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _num(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(default)


def _text_series(frame: pd.DataFrame, names: Iterable[str], default: str = "") -> pd.Series:
    for name in names:
        if name in frame.columns:
            return frame[name].fillna(default).astype(str)
    return pd.Series(default, index=frame.index, dtype="object")


def _inverse_side(side: str) -> str:
    clean = str(side or "").strip().lower()
    if clean == "long":
        return "short"
    if clean == "short":
        return "long"
    return ""


def build_trade_inverse_outcome(ledger: pd.DataFrame) -> TradeInverseOutcomeResult:
    if ledger.empty:
        report = pd.DataFrame(columns=REPORT_COLUMNS)
        summary = pd.DataFrame([{**{c: "" for c in SUMMARY_COLUMNS}, "trade_count": 0, "recommended_action": "insufficient_data"}])
        return TradeInverseOutcomeResult(report, summary.reindex(columns=SUMMARY_COLUMNS))

    frame = ledger.copy()
    status = _text_series(frame, ["position_status", "trade_status"]).str.lower()
    closed = frame[status.eq("closed")].copy()
    if closed.empty:
        report = pd.DataFrame(columns=REPORT_COLUMNS)
        summary = pd.DataFrame([{**{c: "" for c in SUMMARY_COLUMNS}, "trade_count": 0, "recommended_action": "insufficient_data_no_closed_trades"}])
        return TradeInverseOutcomeResult(report, summary.reindex(columns=SUMMARY_COLUMNS))

    actual_pnl = _num(closed, "realised_pnl")
    if "realised_pnl" not in closed.columns and "realized_pnl_usd" in closed.columns:
        actual_pnl = _num(closed, "realized_pnl_usd")
    actual_return = _num(closed, "realised_return_pct")
    if "realised_return_pct" not in closed.columns and "return_pct" in closed.columns:
        actual_return = _num(closed, "return_pct")

    report = pd.DataFrame(index=closed.index)
    report["symbol"] = _text_series(closed, ["symbol", "ticker"]).str.upper().str.strip()
    report["actual_side"] = _text_series(closed, ["side"])
    report["inverse_side"] = report["actual_side"].map(_inverse_side)
    report["entry_time"] = _text_series(closed, ["entry_time"])
    report["exit_time"] = _text_series(closed, ["exit_time"])
    report["entry_price"] = _num(closed, "entry_price")
    report["exit_price"] = _num(closed, "exit_price")
    report["quantity"] = _num(closed, "entry_quantity")
    report["actual_pnl"] = actual_pnl.round(4)
    report["inverse_pnl_before_incremental_costs"] = (-actual_pnl).round(4)
    report["actual_return_pct"] = actual_return.round(6)
    report["inverse_return_pct_before_incremental_costs"] = (-actual_return).round(6)
    report["candidate_source"] = _text_series(closed, ["candidate_source"])
    report["strategy_mode"] = _text_series(closed, ["strategy_mode"])
    report["session_mode"] = _text_series(closed, ["actual_submission_session_mode", "event_session_mode", "session_mode"])
    report["exit_reason"] = _text_series(closed, ["exit_reason"])
    report["lineage_quality"] = _text_series(closed, ["lineage_quality"])
    report["lineage_warning"] = _text_series(closed, ["lineage_warnings", "lineage_warning"])
    report["inversion_evidence"] = report["actual_pnl"].map(lambda value: "inverse_would_win" if value < 0 else "inverse_would_lose" if value > 0 else "flat")
    report = report.reindex(columns=REPORT_COLUMNS).reset_index(drop=True)

    actual = pd.to_numeric(report["actual_pnl"], errors="coerce").fillna(0.0)
    inverse = pd.to_numeric(report["inverse_pnl_before_incremental_costs"], errors="coerce").fillna(0.0)
    count = int(len(report))
    sample_warning = "sample_too_small_for_strategy_flip" if count < 30 else ""
    lineage_warning = "low_confidence_lineage_present" if report["lineage_quality"].str.lower().eq("low").any() else ""
    recommended = "investigate_polarity_before_next_strategy_change" if bool(inverse.sum() > actual.sum() and count > 0) else "keep_direction_unchanged"
    if count < 30:
        recommended = f"{recommended}; do_not_auto_reverse_small_sample"
    summary = pd.DataFrame([
        {
            "trade_count": count,
            "actual_total_pnl": round(float(actual.sum()), 4),
            "inverse_total_pnl_before_incremental_costs": round(float(inverse.sum()), 4),
            "actual_winners": int((actual > 0).sum()),
            "actual_losers": int((actual < 0).sum()),
            "inverse_winners": int((inverse > 0).sum()),
            "inverse_losers": int((inverse < 0).sum()),
            "actual_hit_rate": round(float((actual > 0).mean()), 6) if count else 0.0,
            "inverse_hit_rate_before_incremental_costs": round(float((inverse > 0).mean()), 6) if count else 0.0,
            "all_actual_losers": bool(count and (actual < 0).all()),
            "inverse_beats_actual": bool(inverse.sum() > actual.sum()) if count else False,
            "sample_size_warning": sample_warning,
            "lineage_warning": lineage_warning,
            "recommended_action": recommended,
        }
    ], columns=SUMMARY_COLUMNS)
    return TradeInverseOutcomeResult(report, summary)


def build_trade_inverse_outcome_from_latest(ledger_path: Path | None = None) -> TradeInverseOutcomeResult:
    path = ledger_path or _latest_ledger()
    frame = safe_read_csv(path) if path else pd.DataFrame()
    return build_trade_inverse_outcome(frame)


def write_trade_inverse_outcome(result: TradeInverseOutcomeResult, *, out_stamp: str | None = None, output_dir: Path | None = None) -> TradeInverseOutcomeResult:
    stamp = out_stamp or timestamp()
    directory = output_dir or TRADING_DIR / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / f"trade_inverse_outcome_{stamp}.csv"
    summary_path = directory / f"trade_inverse_outcome_summary_{stamp}.csv"
    result.report.to_csv(report_path, index=False)
    result.summary.to_csv(summary_path, index=False)
    return TradeInverseOutcomeResult(result.report, result.summary, report_path, summary_path)
