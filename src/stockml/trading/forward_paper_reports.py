from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.trading.counterfactual_log import write_counterfactual_forward_returns


FORWARD_DIR = PROJECT_ROOT / "data" / "trading" / "forward_paper"


@dataclass(frozen=True)
class ReportOutput:
    path: Path
    rows: int
    summary_path: Path | None = None


def _read_csv(path: Path | str | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    source = Path(path)
    if not source.exists() or not source.is_file() or source.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(source, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _latest(root: Path, pattern: str) -> Path | None:
    files = [path for path in root.glob(pattern) if path.is_file()]
    return max(files, key=lambda item: item.stat().st_mtime) if files else None


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _boolish(value: Any) -> bool:
    if value in [None, ""]:
        return False
    try:
        if pd.isna(value):
            return False
    except Exception:
        pass
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def source_no_decision_reason(row: pd.Series) -> str:
    action = _text(row.get("source_trade_action")).lower()
    if action in {"long", "short"}:
        return "source_approved"
    if not _text(row.get("model_score")) and not _text(row.get("risk_adjusted_score")):
        return "source_signal_not_available"
    if not _text(row.get("meta_label_probability")):
        return "meta_label_missing"
    if _text(row.get("ticker_direction_memory_status")).lower() in {"insufficient_samples", "missing"}:
        return "insufficient_direction_memory"
    if _text(row.get("primary_block_reason")).lower() in {"direction_memory_conflict"}:
        return "direction_memory_conflict"
    if _text(row.get("risk_tier")).lower() == "reject":
        return "risk_gate_failed"
    return "scored_and_abstained"


def build_source_direction_coverage(candidates: pd.DataFrame) -> pd.DataFrame:
    frame = candidates.copy() if candidates is not None else pd.DataFrame()
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "rank", "source_trade_action", "source_no_decision_reason"])
    frame["symbol"] = frame.get("symbol", frame.get("ticker", "")).astype(str).str.upper()
    frame["rank"] = frame.get("raw_rank", frame.get("rank_overall", frame.index + 1))
    frame["source_no_decision_reason"] = [source_no_decision_reason(row) for _, row in frame.iterrows()]
    columns = [
        "symbol",
        "rank",
        "source_trade_action",
        "model_score",
        "rank_overall",
        "directional_strength",
        "confidence_score",
        "risk_adjusted_score",
        "meta_label_probability",
        "ticker_direction_bias",
        "ticker_direction_sample_count",
        "expected_return_scope",
        "validated_expected_return_bps",
        "risk_tier",
        "volatility_tier",
        "liquidity_tier",
        "primary_block_reason",
        "execution_domain",
        "source_no_decision_reason",
    ]
    for column in columns:
        if column not in frame.columns:
            frame[column] = ""
    return frame[columns]


def write_source_direction_coverage(
    candidate_path: Path | str | None = None,
    *,
    root: Path | None = None,
    stamp: str | None = None,
) -> ReportOutput:
    base = root or PROJECT_ROOT
    source = Path(candidate_path) if candidate_path else _latest(base / "data" / "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
    detail = build_source_direction_coverage(_read_csv(source))
    out_dir = base / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    path = out_dir / f"source_direction_coverage_detail_{run_stamp}.csv"
    summary_path = out_dir / f"source_direction_coverage_summary_{run_stamp}.md"
    detail.to_csv(path, index=False)
    counts = detail.get("source_no_decision_reason", pd.Series(dtype=str)).fillna("unknown").astype(str).value_counts()
    lines = [
        "# Source Direction Coverage",
        "",
        f"- created_at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- candidate_path: {source or ''}",
        f"- total_candidates: {len(detail)}",
        "",
        "## Reason Counts",
        *[f"- {reason}: {count}" for reason, count in counts.items()],
    ]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ReportOutput(path, len(detail), summary_path)


def _count_stage(frame: pd.DataFrame, mask: pd.Series) -> int:
    return int(mask.fillna(False).sum()) if not frame.empty else 0


def _dominant_reason(frame: pd.DataFrame, mask: pd.Series) -> str:
    if frame.empty or "primary_block_reason" not in frame.columns:
        return ""
    dropped = frame[~mask.fillna(False)]
    if dropped.empty:
        return ""
    counts = dropped["primary_block_reason"].fillna("unknown").astype(str).replace("", "unknown").value_counts()
    return str(counts.index[0]) if not counts.empty else ""


def build_gate_funnel(candidates: pd.DataFrame, results: pd.DataFrame | None = None) -> pd.DataFrame:
    frame = candidates.copy() if candidates is not None else pd.DataFrame()
    results = results if results is not None else pd.DataFrame()
    stages: list[tuple[str, pd.Series]] = []
    if frame.empty:
        return pd.DataFrame(columns=["stage", "count", "dropped_from_previous", "dominant_drop_reason"])
    raw = pd.Series(True, index=frame.index)
    stages.append(("raw_pool", raw))
    source = frame.get("source_trade_action", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower().isin({"long", "short"})
    stages.append(("source_approved_direction", source))
    reasons = frame.get("all_block_reasons", frame.get("primary_block_reason", pd.Series("", index=frame.index))).fillna("").astype(str).str.lower()
    floor = source & ~reasons.str.contains("price_below_minimum|market_cap_below_minimum|market_cap_missing")
    stages.append(("floor_pass", floor))
    volrisk = floor & ~reasons.str.contains("volatility_extreme|risk_gate_failed")
    stages.append(("vol_risk_pass", volrisk))
    net = pd.to_numeric(frame.get("net_expected_return_bps", frame.get("validated_expected_return_bps", pd.Series(0, index=frame.index))), errors="coerce").fillna(0) > 0
    stages.append(("net_return_positive", volrisk & net))
    session = stages[-1][1] & ~reasons.str.contains("asset_not_overnight_tradable|weekend_closed")
    stages.append(("session_eligible", session))
    executable = frame.get("final_execution_side", pd.Series("", index=frame.index)).fillna("").astype(str).str.upper().isin({"LONG", "SHORT"})
    stages.append(("executable", executable))
    submitted = 0
    filled = 0
    if results is not None and not results.empty:
        status = results.get("status", pd.Series("", index=results.index)).fillna("").astype(str).str.lower()
        alpaca = results.get("alpaca_status", pd.Series("", index=results.index)).fillna("").astype(str).str.lower()
        submitted = int((status.eq("submitted") | alpaca.isin(["new", "accepted", "partially_filled", "filled"])).sum())
        filled = int(alpaca.eq("filled").sum())
    rows = []
    previous = 0
    for name, mask in stages:
        count = _count_stage(frame, mask)
        rows.append({"stage": name, "count": count, "dropped_from_previous": max(previous - count, 0), "dominant_drop_reason": _dominant_reason(frame, mask)})
        previous = count
    rows.append({"stage": "submitted", "count": submitted, "dropped_from_previous": max(previous - submitted, 0), "dominant_drop_reason": ""})
    rows.append({"stage": "filled", "count": filled, "dropped_from_previous": max(submitted - filled, 0), "dominant_drop_reason": ""})
    out = pd.DataFrame(rows)
    executable_count = int(out.loc[out["stage"].eq("executable"), "count"].iloc[0])
    out["submitted_to_executable_ratio"] = (submitted / executable_count) if executable_count else 0.0
    out["filled_to_submitted_ratio"] = (filled / submitted) if submitted else 0.0
    return out


def write_gate_funnel(
    candidate_path: Path | str | None = None,
    result_path: Path | str | None = None,
    *,
    root: Path | None = None,
    run_date: str | None = None,
) -> ReportOutput:
    base = root or PROJECT_ROOT
    candidate = Path(candidate_path) if candidate_path else _latest(base / "data" / "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
    result = Path(result_path) if result_path else _latest(base / "data" / "portal_outputs", "08_alpaca_paper_order_results_*.csv")
    funnel = build_gate_funnel(_read_csv(candidate), _read_csv(result))
    out_dir = base / "data" / "trading" / "forward_paper"
    out_dir.mkdir(parents=True, exist_ok=True)
    date = run_date or datetime.now(timezone.utc).strftime("%Y%m%d")
    path = out_dir / f"gate_funnel_{date}.csv"
    funnel.to_csv(path, index=False)
    summary = base / "docs" / "gate_funnel_summary.md"
    summary.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Gate Funnel Summary", "", f"- updated_at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}", f"- latest_report: {path}", ""]
    if not funnel.empty:
        lines.extend(["## Latest Funnel", *[f"- {row.stage}: {row.count}" for row in funnel.itertuples()]])
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ReportOutput(path, len(funnel), summary)


def terminal_status(row: pd.Series) -> str:
    if _text(row.get("final_execution_side")).upper() in {"LONG", "SHORT"} or _text(row.get("status")).lower() == "executable":
        return "executable"
    reason = (_text(row.get("primary_block_reason")) or _text(row.get("all_block_reasons"))).lower()
    if "planner_derived" in reason or _text(row.get("source_trade_action")).lower() in {"no decision", "no_decision"}:
        return "planner_only_blocked"
    if "price_below" in reason or "market_cap" in reason:
        return "floor_blocked"
    if "volatility" in reason:
        return "vol_blocked"
    if "overnight" in reason or "session" in reason:
        return "session_blocked"
    if _text(row.get("source_trade_action")).lower() in {"long", "short"}:
        return "source_approved_blocked"
    return "blocked_other"


def build_counterfactual_status_report(counterfactual_with_returns: pd.DataFrame) -> pd.DataFrame:
    frame = counterfactual_with_returns.copy() if counterfactual_with_returns is not None else pd.DataFrame()
    if frame.empty:
        return pd.DataFrame(columns=["terminal_status", "n", "mean_gross_5d_bps", "median_gross_5d_bps", "mean_net_5d_bps", "verdict"])
    frame["terminal_status"] = [terminal_status(row) for _, row in frame.iterrows()]
    gross = pd.to_numeric(frame.get("directional_forward_5d_bps"), errors="coerce")
    cost = pd.to_numeric(frame.get("estimated_execution_cost_bps"), errors="coerce").fillna(0)
    frame["net_5d_bps"] = gross - cost
    grouped = []
    for status, group in frame.groupby("terminal_status", dropna=False):
        g = pd.to_numeric(group.get("directional_forward_5d_bps"), errors="coerce")
        n = int(g.notna().sum())
        grouped.append(
            {
                "terminal_status": status,
                "n": n,
                "mean_gross_5d_bps": g.mean() if n else pd.NA,
                "median_gross_5d_bps": g.median() if n else pd.NA,
                "mean_net_5d_bps": pd.to_numeric(group["net_5d_bps"], errors="coerce").mean() if n else pd.NA,
                "verdict": "INSUFFICIENT DATA" if n < 30 else "READY_FOR_REVIEW",
            }
        )
    return pd.DataFrame(grouped)


def write_counterfactual_status_report(
    counterfactual_path: Path | str | None = None,
    *,
    root: Path | None = None,
    stamp: str | None = None,
) -> ReportOutput:
    base = root or PROJECT_ROOT
    forward = write_counterfactual_forward_returns(counterfactual_path, output_dir=base / "data" / "trading" / "forward_paper", stamp=stamp)
    detail = _read_csv(forward.path)
    report = build_counterfactual_status_report(detail)
    out_dir = base / "data" / "trading" / "forward_paper"
    run_stamp = stamp or timestamp()
    path = out_dir / f"counterfactual_status_returns_{run_stamp}.csv"
    summary = out_dir / f"counterfactual_status_returns_{run_stamp}.md"
    report.to_csv(path, index=False)
    lines = ["# Counterfactual Forward Returns By Status", "", f"- source: {forward.path}", f"- rows: {len(report)}", ""]
    if not report.empty:
        lines.extend(["## Buckets", *[f"- {row.terminal_status}: n={row.n}, verdict={row.verdict}" for row in report.itertuples()]])
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ReportOutput(path, len(report), summary)
