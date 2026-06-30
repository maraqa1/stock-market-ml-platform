from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from stockml.common.paths import TRADING_DIR, latest_file, timestamp
from stockml.diagnostics.trade_ledger_builder import TradeLedgerResult, build_trade_ledger, request_from_args, write_trade_ledger

ATTRIBUTION_COLUMNS = [
    "dimension", "bucket", "trades", "open_trades", "closed_trades", "long_trades", "short_trades",
    "realised_pnl", "unrealised_pnl", "total_pnl", "avg_realised_pnl", "avg_unrealised_pnl",
    "win_rate", "avg_realised_return_pct", "avg_unrealised_return_pct", "low_confidence_trades",
    "insufficient_data_trades", "lineage_quality_pct", "fit_for_attribution",
]
DIMENSIONS = ["side", "candidate_source", "strategy_mode", "event_session_mode", "actual_submission_session_mode", "position_status", "exit_reason", "model_score_bucket"]

@dataclass(frozen=True)
class ProfitabilityAttributionResult:
    attribution: pd.DataFrame
    summary: dict[str, Any]
    attribution_path: Path | None = None
    summary_path: Path | None = None
    ledger_path: Path | None = None


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _bucket_model_score(frame: pd.DataFrame) -> pd.Series:
    if "model_score" not in frame or frame.empty:
        return pd.Series("missing", index=frame.index)
    scores = _num(frame["model_score"])
    out = pd.Series("missing", index=frame.index, dtype="object")
    valid = scores.dropna()
    if valid.empty:
        return out
    try:
        ranked = pd.qcut(valid.rank(method="first"), q=min(10, len(valid)), labels=False) + 1
        out.loc[ranked.index] = ranked.apply(lambda value: f"decile_{int(value):02d}")
    except Exception:
        out.loc[valid.index] = "scored"
    return out


def _safe_sum(frame: pd.DataFrame, column: str) -> float:
    return round(float(_num(frame.get(column, pd.Series(dtype=float))).fillna(0).sum()), 4)


def _safe_mean(frame: pd.DataFrame, column: str) -> float | str:
    values = _num(frame.get(column, pd.Series(dtype=float))).dropna()
    return round(float(values.mean()), 6) if not values.empty else ""


def _win_rate(frame: pd.DataFrame) -> float | str:
    pnl = _num(frame.get("realised_pnl", pd.Series(dtype=float))).dropna()
    return round(float((pnl > 0).mean()), 6) if not pnl.empty else ""


def _row(frame: pd.DataFrame, dimension: str, bucket: str) -> dict[str, Any]:
    trades = len(frame)
    realised = _safe_sum(frame, "realised_pnl")
    unrealised = _safe_sum(frame, "unrealised_pnl")
    reliable = int(frame.get("lineage_quality", pd.Series("", index=frame.index)).isin(["high", "medium"]).sum()) if trades else 0
    return {
        "dimension": dimension,
        "bucket": bucket or "missing",
        "trades": trades,
        "open_trades": int(frame.get("position_status", pd.Series("", index=frame.index)).eq("open").sum()),
        "closed_trades": int(frame.get("position_status", pd.Series("", index=frame.index)).eq("closed").sum()),
        "long_trades": int(frame.get("side", pd.Series("", index=frame.index)).eq("long").sum()),
        "short_trades": int(frame.get("side", pd.Series("", index=frame.index)).eq("short").sum()),
        "realised_pnl": realised,
        "unrealised_pnl": unrealised,
        "total_pnl": round(realised + unrealised, 4),
        "avg_realised_pnl": _safe_mean(frame, "realised_pnl"),
        "avg_unrealised_pnl": _safe_mean(frame, "unrealised_pnl"),
        "win_rate": _win_rate(frame),
        "avg_realised_return_pct": _safe_mean(frame, "realised_return_pct"),
        "avg_unrealised_return_pct": _safe_mean(frame, "unrealised_return_pct"),
        "low_confidence_trades": int(frame.get("lineage_quality", pd.Series("", index=frame.index)).eq("low").sum()),
        "insufficient_data_trades": int(frame.get("position_status", pd.Series("", index=frame.index)).eq("insufficient_data").sum()),
        "lineage_quality_pct": round((reliable / trades) * 100, 2) if trades else 0.0,
        "fit_for_attribution": "yes" if trades and reliable else "no",
    }


def build_profitability_attribution(ledger: pd.DataFrame) -> ProfitabilityAttributionResult:
    frame = ledger.copy()
    if frame.empty:
        attribution = pd.DataFrame(columns=ATTRIBUTION_COLUMNS)
        summary = summarize_profitability_attribution(frame, attribution)
        return ProfitabilityAttributionResult(attribution, summary)
    frame["model_score_bucket"] = _bucket_model_score(frame)
    rows: list[dict[str, Any]] = [_row(frame, "ALL", "ALL")]
    for dimension in DIMENSIONS:
        if dimension not in frame:
            continue
        values = frame[dimension].fillna("").astype(str).replace("", "missing")
        for bucket, group in frame.groupby(values, dropna=False):
            rows.append(_row(group, dimension, str(bucket)))
    attribution = pd.DataFrame(rows, columns=ATTRIBUTION_COLUMNS)
    summary = summarize_profitability_attribution(frame, attribution)
    return ProfitabilityAttributionResult(attribution, summary)


def summarize_profitability_attribution(ledger: pd.DataFrame, attribution: pd.DataFrame) -> dict[str, Any]:
    trades = len(ledger)
    closed = int(ledger.get("position_status", pd.Series(dtype=str)).eq("closed").sum()) if trades else 0
    open_trades = int(ledger.get("position_status", pd.Series(dtype=str)).eq("open").sum()) if trades else 0
    insufficient = int(ledger.get("position_status", pd.Series(dtype=str)).eq("insufficient_data").sum()) if trades else 0
    low = int(ledger.get("lineage_quality", pd.Series(dtype=str)).eq("low").sum()) if trades else 0
    realised = _safe_sum(ledger, "realised_pnl") if trades else 0.0
    unrealised = _safe_sum(ledger, "unrealised_pnl") if trades else 0.0
    if trades == 0:
        decision = "NOT_ENOUGH_TRADES"
    elif insufficient >= trades:
        decision = "NOT_FIT_INSUFFICIENT_DATA"
    elif closed == 0 and open_trades > 0:
        decision = "OPEN_TRADES_ONLY"
    elif low > 0:
        decision = "PARTIAL_ATTRIBUTION_ONLY"
    else:
        decision = "FIT_FOR_ATTRIBUTION"
    return {
        "trades": int(trades),
        "closed_trades": int(closed),
        "open_trades": int(open_trades),
        "insufficient_data_trades": int(insufficient),
        "low_confidence_trades": int(low),
        "realised_pnl": realised,
        "unrealised_pnl": unrealised,
        "total_pnl": round(realised + unrealised, 4),
        "attribution_rows": int(len(attribution)),
        "attribution_decision": decision,
    }


def load_ledger(path: Path | str | None = None) -> tuple[pd.DataFrame, Path | None]:
    ledger_path = Path(path) if path else latest_file(TRADING_DIR / "diagnostics", "trade_ledger_*.csv")
    if ledger_path is None or not ledger_path.exists():
        return pd.DataFrame(), None
    return pd.read_csv(ledger_path, low_memory=False), ledger_path


def build_from_ledger_path(path: Path | str | None = None) -> ProfitabilityAttributionResult:
    ledger, ledger_path = load_ledger(path)
    result = build_profitability_attribution(ledger)
    return ProfitabilityAttributionResult(result.attribution, result.summary, ledger_path=ledger_path)


def build_for_request(*, date_value: str = "", start: str = "", end: str = "") -> ProfitabilityAttributionResult:
    ledger_result = write_trade_ledger(build_trade_ledger(request_from_args(date_value=date_value, start=start, end=end)))
    result = build_profitability_attribution(ledger_result.ledger)
    return ProfitabilityAttributionResult(result.attribution, result.summary, ledger_path=ledger_result.ledger_path)


def write_profitability_attribution(result: ProfitabilityAttributionResult, output_dir: Path | str = TRADING_DIR / "diagnostics") -> ProfitabilityAttributionResult:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    attribution_path = out / f"profitability_attribution_{stamp}.csv"
    summary_path = out / f"profitability_attribution_summary_{stamp}.md"
    result.attribution.to_csv(attribution_path, index=False)
    lines = ["# Profitability Attribution Summary", ""]
    if result.ledger_path:
        lines.append(f"- ledger_path: {result.ledger_path}")
    for key, value in result.summary.items():
        lines.append(f"- {key}: {value}")
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ProfitabilityAttributionResult(result.attribution, result.summary, attribution_path, summary_path, result.ledger_path)
