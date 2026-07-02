from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.diagnostics.short_candidate_outcome import (
    build_inverse_long_comparison,
    build_short_candidate_outcomes,
    summarize_short_bucket_performance,
)
from stockml.diagnostics.short_side_performance_guard import evaluate_short_side_performance
from stockml.diagnostics.short_squeeze_risk import build_short_squeeze_risk


DIAGNOSTIC_DIR = PROJECT_ROOT / "data" / "trading" / "diagnostics"


@dataclass(frozen=True)
class ShortSignalValidationOutputs:
    validation_path: Path
    bucket_path: Path
    inverse_path: Path
    squeeze_path: Path
    summary_path: Path
    summary: dict[str, Any]


def _latest(patterns: list[str], base: Path) -> Path | None:
    files: list[Path] = []
    for pattern in patterns:
        files.extend(path for path in base.glob(pattern) if path.is_file())
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _read(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


def load_short_validation_inputs(root: Path | str | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    base = Path(root) if root else PROJECT_ROOT
    model_path = _latest(["advanced_model_signal_table_*.csv", "walk_forward_predictions_*.csv"], base / "data" / "model_outputs")
    candidate_path = _latest(["execution_ranked_candidates_*.csv", "08_alpaca_paper_candidate_pool_*.csv", "08_alpaca_paper_order_plan_*.csv"], base / "data" / "portal_outputs")
    closed_path = _latest(["closed_trades_attribution_*.csv"], base / "data" / "trading")
    candidates = _read(candidate_path)
    model = _read(model_path)
    source = candidates if not candidates.empty else model
    closed = _read(closed_path)
    return source, closed, {
        "model_path": str(model_path or ""),
        "candidate_path": str(candidate_path or ""),
        "closed_path": str(closed_path or ""),
    }


def _summary(outcomes: pd.DataFrame, inverse: pd.DataFrame, squeeze: pd.DataFrame, closed: pd.DataFrame) -> dict[str, Any]:
    short_count = int(len(outcomes))
    returns = pd.to_numeric(outcomes.get("net_short_return_bps", pd.Series(dtype=float)), errors="coerce").dropna()
    wins = returns[returns.gt(0)]
    losses = returns[returns.lt(0)]
    gross_profit = float(wins.sum())
    gross_loss = abs(float(losses.sum()))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    win_rate = float(returns.gt(0).mean()) if len(returns) else 0.0
    net_return = float(returns.sum()) if len(returns) else 0.0
    inverse_rate = float(pd.to_numeric(inverse.get("inverse_outperforms", pd.Series(dtype=bool)), errors="coerce").fillna(False).mean()) if len(inverse) else 0.0
    high_squeeze = int(squeeze.get("short_squeeze_risk_tier", pd.Series(dtype=str)).astype(str).str.lower().eq("high").sum()) if len(squeeze) else 0
    guard = evaluate_short_side_performance(closed)
    closed_short_trades = int(guard.iloc[0]["closed_short_trades"]) if not guard.empty else 0
    warnings = []
    if short_count < 50:
        warnings.append("insufficient_data")
    if closed_short_trades < 50:
        warnings.append("insufficient_closed_trade_data")
    if profit_factor < 1.10 or win_rate < 0.45:
        warnings.append("short_disabled_negative_edge")
    if inverse_rate > 0.50:
        warnings.append("inverse_direction_warning")
    if high_squeeze:
        warnings.append("short_disabled_squeeze_risk")
    recommendation = "short_research_only"
    if "short_disabled_negative_edge" in warnings:
        recommendation = "short_disabled_negative_edge"
    elif "insufficient_data" in warnings or "insufficient_closed_trade_data" in warnings:
        recommendation = "short_disabled_insufficient_data"
    elif "short_disabled_squeeze_risk" in warnings:
        recommendation = "short_disabled_squeeze_risk"
    return {
        "short_candidates": short_count,
        "closed_short_trades": closed_short_trades,
        "short_win_rate": round(win_rate, 6),
        "short_profit_factor": round(profit_factor, 6),
        "short_net_return_bps": round(net_return, 4),
        "inverse_outperform_rate": round(inverse_rate, 6),
        "high_squeeze_count": high_squeeze,
        "short_policy_recommendation": recommendation,
        "warnings": "|".join(warnings),
    }


def run_short_signal_validation(
    candidates: pd.DataFrame,
    closed_trades: pd.DataFrame | None = None,
    *,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
) -> ShortSignalValidationOutputs:
    out_dir = Path(output_dir) if output_dir else DIAGNOSTIC_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    outcomes = build_short_candidate_outcomes(candidates)
    squeeze = build_short_squeeze_risk(candidates)
    if not outcomes.empty and not squeeze.empty:
        outcomes = outcomes.merge(squeeze, on="symbol", how="left")
        high = outcomes["short_squeeze_risk_tier"].fillna("").astype(str).str.lower().eq("high")
        outcomes["short_validation_status"] = "short_research_only"
        outcomes.loc[high, "short_validation_status"] = "short_disabled_squeeze_risk"
    else:
        outcomes["short_validation_status"] = "short_research_only" if not outcomes.empty else pd.Series(dtype=str)
    bucket = summarize_short_bucket_performance(outcomes)
    inverse = build_inverse_long_comparison(outcomes)
    summary = _summary(outcomes, inverse, squeeze, closed_trades if closed_trades is not None else pd.DataFrame())

    validation_path = out_dir / f"short_signal_validation_{run_stamp}.csv"
    bucket_path = out_dir / f"short_bucket_performance_{run_stamp}.csv"
    inverse_path = out_dir / f"short_inverse_comparison_{run_stamp}.csv"
    squeeze_path = out_dir / f"short_squeeze_risk_{run_stamp}.csv"
    summary_path = out_dir / f"short_signal_validation_summary_{run_stamp}.md"
    outcomes.to_csv(validation_path, index=False)
    bucket.to_csv(bucket_path, index=False)
    inverse.to_csv(inverse_path, index=False)
    squeeze.to_csv(squeeze_path, index=False)
    summary_path.write_text(
        "\n".join(
            [
                "# Dedicated Short Signal Validation",
                "",
                f"- short_candidates: {summary['short_candidates']}",
                f"- closed_short_trades: {summary['closed_short_trades']}",
                f"- short_win_rate: {summary['short_win_rate']}",
                f"- short_profit_factor: {summary['short_profit_factor']}",
                f"- short_net_return_bps: {summary['short_net_return_bps']}",
                f"- inverse_outperform_rate: {summary['inverse_outperform_rate']}",
                f"- high_squeeze_count: {summary['high_squeeze_count']}",
                f"- recommendation: {summary['short_policy_recommendation']}",
                f"- warnings: {summary['warnings']}",
                "",
                "Short execution remains disabled by default. This report is diagnostic only.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return ShortSignalValidationOutputs(validation_path, bucket_path, inverse_path, squeeze_path, summary_path, summary)
