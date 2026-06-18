from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.common.paths import MODEL_OUTPUTS_DIR
from stockml.diagnostics.common import attach_forward_returns, gold_outcome_slice, latest_gold, latest_signal_history, numeric, safe_read_csv, write_report, DiagnosticOutput

POLARITY_COLUMNS = ["strategy", "count", "hit_rate", "average_return", "median_return", "total_pnl", "profit_factor", "max_drawdown_estimate", "average_cost", "net_after_cost"]


def _side_sign(side: str) -> float:
    return 1.0 if side == "Long" else -1.0 if side == "Short" else 0.0


def _profit_factor(values: pd.Series) -> float:
    gains = values[values > 0].sum()
    losses = -values[values < 0].sum()
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def _max_drawdown(values: pd.Series) -> float:
    curve = values.fillna(0).cumsum()
    if curve.empty:
        return 0.0
    return float((curve - curve.cummax()).min())


def _summary(name: str, returns: pd.Series, *, cost: float) -> dict[str, object]:
    vals = pd.to_numeric(returns, errors="coerce").dropna()
    net = vals - cost
    return {
        "strategy": name,
        "count": int(len(vals)),
        "hit_rate": float((net > 0).mean()) if len(vals) else 0.0,
        "average_return": float(vals.mean()) if len(vals) else 0.0,
        "median_return": float(vals.median()) if len(vals) else 0.0,
        "total_pnl": float(vals.sum()) if len(vals) else 0.0,
        "profit_factor": _profit_factor(net),
        "max_drawdown_estimate": _max_drawdown(net),
        "average_cost": cost,
        "net_after_cost": float(net.sum()) if len(vals) else 0.0,
    }


def build_ranking_polarity(frame: pd.DataFrame, *, cost: float = 0.001) -> pd.DataFrame:
    if frame.empty or "forward_5d_return" not in frame.columns:
        return pd.DataFrame(columns=POLARITY_COLUMNS)
    out = frame.copy()
    rank = numeric(out, "rank_overall", default=float("nan"))
    score = numeric(out, "model_score", default=float("nan"))
    predicted_pct = numeric(out, "predicted_rank_pct_by_date", default=float("nan"))
    if rank.notna().any():
        top = rank <= rank.quantile(0.2)
        bottom = rank >= rank.quantile(0.8)
        ascending_signal = True
    elif score.notna().any():
        top = score >= score.quantile(0.8)
        bottom = score <= score.quantile(0.2)
        ascending_signal = False
    elif predicted_pct.notna().any():
        top = predicted_pct <= predicted_pct.quantile(0.2)
        bottom = predicted_pct >= predicted_pct.quantile(0.8)
        ascending_signal = True
    else:
        top = pd.Series(False, index=out.index)
        bottom = pd.Series(False, index=out.index)
        ascending_signal = True
    ret = pd.to_numeric(out["forward_5d_return"], errors="coerce")
    rows = [
        _summary("current_top_long_bottom_short", ret.where(top, -ret.where(bottom)), cost=cost),
        _summary("inverse_top_short_bottom_long", (-ret).where(top, ret.where(bottom)), cost=cost),
        _summary("long_only_top_ranked", ret.where(top), cost=cost),
        _summary("long_only_bottom_ranked", ret.where(bottom), cost=cost),
        _summary("short_only_top_ranked", -ret.where(top), cost=cost),
        _summary("short_only_bottom_ranked", -ret.where(bottom), cost=cost),
    ]
    result = pd.DataFrame(rows, columns=POLARITY_COLUMNS)
    result["rank_interpretation"] = "ascending_rank_best" if ascending_signal else "descending_score_best"
    top_mean = ret[top].mean() if top.any() else 0.0
    bottom_mean = ret[bottom].mean() if bottom.any() else 0.0
    result["top_minus_bottom_return"] = float((top_mean or 0.0) - (bottom_mean or 0.0))
    result["polarity_bug_likely"] = bool(bottom_mean > top_mean) if top.any() and bottom.any() else False
    return result


def build_ranking_polarity_report(stamp: str, *, signal_file: Path | None = None, gold_file: Path | None = None) -> DiagnosticOutput:
    signals = safe_read_csv(signal_file or latest_signal_history())
    if signals.empty:
        frame = pd.DataFrame([{"strategy": "missing_data", "count": 0}])
        return write_report("ranking_polarity_diagnostic", frame, MODEL_OUTPUTS_DIR / "diagnostics" / f"ranking_polarity_diagnostic_{stamp}.csv", missing_inputs=("signal_history",))
    gold = gold_outcome_slice(gold_file or latest_gold(), signals)
    data = attach_forward_returns(signals, gold)
    if "forward_5d_return" not in data.columns or pd.to_numeric(data["forward_5d_return"], errors="coerce").notna().sum() == 0:
        frame = pd.DataFrame([{"strategy": "missing_data", "count": 0, "missing_inputs": "forward_outcomes"}])
        return write_report("ranking_polarity_diagnostic", frame, MODEL_OUTPUTS_DIR / "diagnostics" / f"ranking_polarity_diagnostic_{stamp}.csv", missing_inputs=("forward_outcomes",))
    return write_report("ranking_polarity_diagnostic", build_ranking_polarity(data), MODEL_OUTPUTS_DIR / "diagnostics" / f"ranking_polarity_diagnostic_{stamp}.csv")
