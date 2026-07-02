from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, latest_file
from stockml.strategy.gate_registry import gate_records_for, gates_from_reasons


DIAGNOSTICS_DIR = PROJECT_ROOT / "data" / "trading" / "diagnostics"
MISSING_FIELDS = ["risk_tier", "trade_quality_status", "candidate_rank", "validated_expected_return_bps", "trade_quality_reason"]


def _read(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "") or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _latest_positions() -> Path | None:
    return latest_file(PROJECT_ROOT / "data" / "portal_outputs", "08_alpaca_paper_positions_*.csv")


def _latest_plan() -> Path | None:
    return latest_file(PROJECT_ROOT / "data" / "portal_outputs", "08_alpaca_paper_order_plan_*.csv")


def _candidate_context_by_symbol() -> pd.DataFrame:
    positions = _read(_latest_positions())
    plan = _read(_latest_plan())
    if positions.empty:
        return positions
    positions = positions.copy()
    positions["symbol"] = positions["symbol"].astype(str).str.upper()
    if not plan.empty and "symbol" in plan.columns:
        plan = plan.copy()
        plan["symbol"] = plan["symbol"].astype(str).str.upper()
        keep = [c for c in [
            "symbol", "risk_tier", "trade_quality_status", "trade_quality_reason", "candidate_rank",
            "risk_adjusted_score", "validated_expected_return_bps", "validated_hit_rate", "session_mode",
            "primary_block_reason", "all_block_reasons",
        ] if c in plan.columns]
        plan = plan[keep].drop_duplicates("symbol", keep="last")
        positions = positions.merge(plan, on="symbol", how="left")
    return positions


def classify_position(row: pd.Series, *, portfolio_warning: bool = False) -> dict[str, Any]:
    pnl_pct = _num(row.get("unrealized_plpc")) * 100
    if abs(pnl_pct) < 0.0001:
        pnl_pct = _num(row.get("pnl_pct"))
    status = str(row.get("trade_quality_status") or "").strip().lower()
    reason_text = row.get("trade_quality_reason") or row.get("all_block_reasons") or row.get("primary_block_reason") or ""
    gates = gates_from_reasons(reason_text)
    records = gate_records_for(reason_text)
    must = [g.gate_name for g in records if g.gate_class == "must_have_safety"]
    strategy = [g.gate_name for g in records if g.gate_class == "strategy_quality"]
    execution = [g.gate_name for g in records if g.gate_class == "execution_quality"]
    missing = [field for field in MISSING_FIELDS if str(row.get(field, "") or "").strip() == ""]
    most_severe = records[0].gate_name if records else ""
    trigger = any(g.position_management_trigger for g in records) or bool(missing)
    action = "hold"
    severity = "low"
    primary = "no_degradation_detected"
    supporting: list[str] = []

    if pnl_pct <= -8:
        action, severity, primary = "urgent_close_review", "critical", "severe_loss_threshold_breached"
    elif pnl_pct <= -4:
        action, severity, primary = "close_candidate", "high", "hard_stop_threshold_breached"
    elif pnl_pct <= -2 and status == "rejected":
        action, severity, primary = "close_candidate", "high", "losing_position_now_rejected"
    elif "risk_gate_failed" in gates and pnl_pct < 0:
        action = "close_candidate" if pnl_pct <= -2 else "reduce_candidate"
        severity, primary = "high", "risk_gate_failed_after_entry"
    elif "source_trade_action_not_executable" in gates:
        if pnl_pct < 0:
            action = "close_candidate" if pnl_pct <= -2 else "reduce_candidate"
            severity = "high" if pnl_pct <= -2 else "medium"
        else:
            action, severity = "manual_review", "medium"
        primary = "source_trade_action_no_longer_supports_position"
    elif any(gate in gates for gate in ["price_below_minimum", "volatility_extreme", "market_cap_below_minimum"]) and pnl_pct < 0:
        action, severity, primary = "close_candidate", "high", "current_tradability_degraded"
    elif str(row.get("session_mode") or "").lower() == "overnight_24_5" and "asset_not_overnight_tradable" in gates:
        action, severity, primary = "close_candidate", "high", "invalid_overnight_hold"
    elif missing:
        action, severity, primary = "manual_review", "medium", "missing_position_quality_evidence"
    elif status == "approved" and pnl_pct <= -3:
        action, severity, primary = "close_candidate", "high", "approved_position_breached_loss_threshold"

    if portfolio_warning:
        supporting.append("all_positions_red_or_drawdown_active")

    return {
        "failed_current_gates": "|".join(gates),
        "failed_must_have_gates": "|".join(must),
        "failed_strategy_quality_gates": "|".join(strategy),
        "failed_execution_quality_gates": "|".join(execution),
        "gate_degradation_score": len(must) * 3 + len(execution) * 2 + len(strategy) + (2 if status == "rejected" else 0),
        "most_severe_failed_gate": most_severe,
        "position_management_trigger": bool(trigger),
        "suggested_position_action": action,
        "action_severity": severity,
        "primary_reason": primary,
        "supporting_reasons": "|".join(supporting),
        "missing_evidence_fields": "|".join(missing),
        "should_block_new_entries": bool(portfolio_warning),
        "diagnostics_only": True,
    }


def build_position_gate_degradation(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    csv_path = DIAGNOSTICS_DIR / f"position_gate_degradation_{stamp}.csv"
    md_path = DIAGNOSTICS_DIR / f"position_gate_degradation_summary_{stamp}.md"
    positions = _candidate_context_by_symbol()
    if positions.empty:
        frame = pd.DataFrame([{"symbol": "", "suggested_position_action": "manual_review", "primary_reason": "insufficient_data", "diagnostics_only": True}])
        frame.to_csv(csv_path, index=False)
        md_path.write_text("# Position Gate Degradation\n\nStatus: insufficient_data\n", encoding="utf-8")
        return {"csv_path": csv_path, "markdown_path": md_path, "rows": len(frame), "frame": frame, "status": "insufficient_data"}

    pnl = pd.to_numeric(positions.get("unrealized_pl", pd.Series(0, index=positions.index)), errors="coerce").fillna(0)
    portfolio_warning = bool(len(pnl) > 0 and ((pnl < 0).all() or pnl.sum() < 0))
    rows = []
    for _, row in positions.iterrows():
        enriched = row.to_dict()
        enriched["pnl_pct"] = _num(row.get("unrealized_plpc")) * 100
        enriched.update(classify_position(row, portfolio_warning=portfolio_warning))
        rows.append(enriched)
    frame = pd.DataFrame(rows)
    frame.to_csv(csv_path, index=False)
    top = frame.sort_values("gate_degradation_score", ascending=False).head(5)
    md_path.write_text(
        "# Position Gate Degradation\n\n"
        f"Generated: {now.isoformat()}\n\n"
        f"Positions: {len(frame)}\n"
        f"Portfolio warning: {portfolio_warning}\n"
        f"Top degraded: {', '.join(top.get('symbol', pd.Series(dtype=str)).astype(str).tolist())}\n",
        encoding="utf-8",
    )
    return {"csv_path": csv_path, "markdown_path": md_path, "rows": len(frame), "frame": frame, "status": "ok"}
