from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, latest_file
from stockml.strategy.gate_registry import gates_from_reasons
from stockml.strategy.strategy_lanes import assign_lane


DIAGNOSTICS_DIR = PROJECT_ROOT / "data" / "trading" / "diagnostics"


def _read(path: Path | None, **kwargs) -> pd.DataFrame:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False, **kwargs)
    except Exception:
        return pd.DataFrame()


def _latest_candidates() -> Path | None:
    return latest_file(PROJECT_ROOT / "data" / "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")


def _latest_plan() -> Path | None:
    return latest_file(PROJECT_ROOT / "data" / "portal_outputs", "08_alpaca_paper_order_plan_*.csv")


def _latest_results() -> Path | None:
    return latest_file(PROJECT_ROOT / "data" / "portal_outputs", "08_alpaca_paper_order_results_*.csv")


def _latest_positions() -> Path | None:
    return latest_file(PROJECT_ROOT / "data" / "portal_outputs", "08_alpaca_paper_positions_*.csv")


def _stage(stage_name: str, input_count: int, passed_count: int, reasons: list[str] | None = None, notes: str = "") -> dict[str, Any]:
    failed = max(0, input_count - passed_count)
    reasons = reasons or []
    top = ",".join(pd.Series(reasons).value_counts().head(5).index.tolist()) if reasons else ""
    return {
        "stage_name": stage_name,
        "input_count": int(input_count),
        "passed_count": int(passed_count),
        "failed_count": int(failed),
        "top_failure_reasons": top,
        "pass_rate": round(passed_count / input_count, 6) if input_count else 0.0,
        "failure_rate": round(failed / input_count, 6) if input_count else 0.0,
        "cumulative_remaining": int(passed_count),
        "notes": notes,
    }


def build_strategy_funnel(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    csv_path = DIAGNOSTICS_DIR / f"strategy_funnel_{stamp}.csv"
    md_path = DIAGNOSTICS_DIR / f"strategy_funnel_summary_{stamp}.md"

    candidates = _read(_latest_candidates())
    plan = _read(_latest_plan())
    results = _read(_latest_results())
    positions = _read(_latest_positions())
    rows: list[dict[str, Any]] = []

    raw_count = len(candidates)
    rows.append(_stage("raw_candidates", raw_count, raw_count, notes="latest paper candidate pool"))
    if not candidates.empty:
        lane_counts = candidates.fillna("").apply(lambda row: assign_lane(row.to_dict()), axis=1).value_counts()
        rows.append(_stage("assigned_to_lane", raw_count, int(lane_counts.sum()), notes="; ".join(f"{k}={v}" for k, v in lane_counts.items())))
        reasons: list[str] = []
        for col in ["all_block_reasons", "trade_quality_reason", "primary_block_reason"]:
            if col in candidates.columns:
                for value in candidates[col].dropna().tolist():
                    reasons.extend(gates_from_reasons(value))
        passed_must = int(candidates.get("trade_quality_status", pd.Series("", index=candidates.index)).astype(str).str.lower().isin({"approved", "reduced", "executable"}).sum())
        rows.append(_stage("passed_must_have_gates", raw_count, passed_must, reasons=reasons))
        rows.append(_stage("passed_strategy_quality_gates", passed_must, passed_must, notes="requires gate-level forward attribution for precise split"))
        rows.append(_stage("passed_execution_quality_gates", passed_must, passed_must, notes="requires session/spread/quote fields for precise split"))
        execution_ranked = int(pd.to_numeric(candidates.get("execution_rank", pd.Series(index=candidates.index)), errors="coerce").notna().sum())
        rows.append(_stage("execution_ranked", passed_must, execution_ranked))
    else:
        rows.extend([
            _stage("assigned_to_lane", 0, 0, notes="candidate pool missing"),
            _stage("passed_must_have_gates", 0, 0, notes="candidate pool missing"),
            _stage("passed_strategy_quality_gates", 0, 0, notes="candidate pool missing"),
            _stage("passed_execution_quality_gates", 0, 0, notes="candidate pool missing"),
            _stage("execution_ranked", 0, 0, notes="candidate pool missing"),
        ])

    rows.append(_stage("paper_order_planned", len(plan), len(plan), notes="latest order plan"))
    submitted = int(len(results[results.get("status", pd.Series(index=results.index)).astype(str).str.lower().eq("submitted")])) if not results.empty else 0
    rows.append(_stage("submitted", len(results), submitted, notes="latest result file"))
    filled = int(len(results[results.get("alpaca_status", pd.Series(index=results.index)).astype(str).str.lower().eq("filled")])) if not results.empty else 0
    rows.append(_stage("filled", len(results), filled, notes="latest result file"))
    rows.append(_stage("open_positions", len(positions), len(positions), notes="latest broker paper positions snapshot"))
    closed = _read(latest_file(PROJECT_ROOT / "data" / "trading" / "diagnostics", "closed_trades_attribution_*.csv"))
    rows.append(_stage("closed_trades", len(closed), len(closed), notes="latest closed trade attribution if available"))
    if not closed.empty and "realized_pnl" in closed.columns:
        pnl = pd.to_numeric(closed["realized_pnl"], errors="coerce").fillna(0)
        rows.append(_stage("profitable_trades", len(closed), int((pnl > 0).sum())))
        rows.append(_stage("losing_trades", len(closed), int((pnl < 0).sum())))
    else:
        rows.append(_stage("profitable_trades", 0, 0, notes="closed P&L unavailable"))
        rows.append(_stage("losing_trades", 0, 0, notes="closed P&L unavailable"))

    frame = pd.DataFrame(rows)
    frame.to_csv(csv_path, index=False)
    md_path.write_text(
        "# Strategy Funnel Summary\n\n"
        f"Generated: {now.isoformat()}\n\n"
        f"Candidate rows: {raw_count}\n"
        f"Order plan rows: {len(plan)}\n"
        f"Open positions: {len(positions)}\n",
        encoding="utf-8",
    )
    return {"csv_path": csv_path, "markdown_path": md_path, "rows": len(frame), "frame": frame}
