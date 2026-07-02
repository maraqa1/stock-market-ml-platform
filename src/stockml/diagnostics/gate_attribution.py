from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, latest_file
from stockml.strategy.gate_registry import GATE_REGISTRY, gates_from_reasons, get_gate


DIAGNOSTICS_DIR = PROJECT_ROOT / "data" / "trading" / "diagnostics"


def _read(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _latest_candidates() -> Path | None:
    return latest_file(PROJECT_ROOT / "data" / "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")


def _forward_return(frame: pd.DataFrame) -> pd.Series | None:
    for col in ["forward_5d_return", "forward_20d_return", "realised_forward_return_bps", "realized_forward_return_bps"]:
        if col in frame.columns:
            series = pd.to_numeric(frame[col], errors="coerce")
            return series * 10000 if "return" in col and series.abs().max(skipna=True) <= 5 else series
    return None


def _recommendation(record, has_forward: bool, blocked_mean: float | None, passed_mean: float | None) -> str:
    if record.gate_class == "must_have_safety" or record.severity.startswith("critical"):
        return "mandatory_do_not_tune"
    if not has_forward:
        return "insufficient_data"
    if blocked_mean is not None and passed_mean is not None and blocked_mean > passed_mean:
        return "tune"
    if blocked_mean is not None and blocked_mean < 0:
        return "keep"
    return "research_only" if record.gate_class == "research_only" else "keep"


def build_gate_attribution(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    csv_path = DIAGNOSTICS_DIR / f"gate_attribution_{stamp}.csv"
    md_path = DIAGNOSTICS_DIR / f"gate_attribution_summary_{stamp}.md"
    candidates = _read(_latest_candidates())
    if candidates.empty:
        frame = pd.DataFrame([{"gate_name": "", "recommendation": "insufficient_data", "evidence_quality": "candidate_pool_missing"}])
        frame.to_csv(csv_path, index=False)
        md_path.write_text("# Gate Attribution\n\nStatus: insufficient_data\n", encoding="utf-8")
        return {"csv_path": csv_path, "markdown_path": md_path, "rows": len(frame), "frame": frame, "status": "insufficient_data"}

    reason_cols = [col for col in ["all_block_reasons", "trade_quality_reason", "primary_block_reason"] if col in candidates.columns]
    candidate_reasons = []
    for _, row in candidates.iterrows():
        gates: set[str] = set()
        for col in reason_cols:
            gates.update(gates_from_reasons(row.get(col)))
        candidate_reasons.append(gates)
    returns = _forward_return(candidates)
    has_forward = returns is not None and returns.notna().any()
    status = candidates.get("trade_quality_status", candidates.get("status", pd.Series("", index=candidates.index))).astype(str).str.lower()
    passed_mask = status.isin({"approved", "reduced", "executable"})
    side = candidates.get("side", candidates.get("trade_action", pd.Series("", index=candidates.index))).astype(str).str.lower()
    session = candidates.get("session_mode", pd.Series("", index=candidates.index)).astype(str).str.lower()

    all_gate_names = sorted(set(GATE_REGISTRY) | set().union(*candidate_reasons) if candidate_reasons else set(GATE_REGISTRY))
    rows: list[dict[str, Any]] = []
    for gate_name in all_gate_names:
        record = get_gate(gate_name)
        blocked_mask = pd.Series([gate_name in gates for gates in candidate_reasons], index=candidates.index)
        blocked_returns = returns[blocked_mask] if returns is not None else pd.Series(dtype=float)
        passed_returns = returns[~blocked_mask & passed_mask] if returns is not None else pd.Series(dtype=float)
        blocked_mean = float(blocked_returns.mean()) if not blocked_returns.empty and blocked_returns.notna().any() else None
        passed_mean = float(passed_returns.mean()) if not passed_returns.empty and passed_returns.notna().any() else None
        rows.append(
            {
                "gate_name": gate_name,
                "gate_class": record.gate_class,
                "candidates_seen": len(candidates),
                "candidates_blocked": int(blocked_mask.sum()),
                "candidates_passed": int((~blocked_mask & passed_mask).sum()),
                "blocked_forward_return_bps": blocked_mean,
                "passed_forward_return_bps": passed_mean,
                "blocked_side_adjusted_return_bps": blocked_mean,
                "passed_side_adjusted_return_bps": passed_mean,
                "false_positive_block_count": int((blocked_returns > 0).sum()) if has_forward else 0,
                "false_positive_block_rate": round(float((blocked_returns > 0).mean()), 6) if has_forward and len(blocked_returns) else 0.0,
                "false_negative_pass_count": int((passed_returns < 0).sum()) if has_forward else 0,
                "false_negative_pass_rate": round(float((passed_returns < 0).mean()), 6) if has_forward and len(passed_returns) else 0.0,
                "long_blocked_count": int((blocked_mask & side.isin({"buy", "long"})).sum()),
                "short_blocked_count": int((blocked_mask & side.isin({"sell", "short"})).sum()),
                "regular_blocked_count": int((blocked_mask & session.eq("regular_session")).sum()),
                "overnight_blocked_count": int((blocked_mask & session.eq("overnight_24_5")).sum()),
                "sample_count": int(blocked_mask.sum()),
                "evidence_quality": "forward_marks_available" if has_forward else "insufficient_data",
                "recommendation": _recommendation(record, has_forward, blocked_mean, passed_mean),
            }
        )
    frame = pd.DataFrame(rows).sort_values(["candidates_blocked", "gate_name"], ascending=[False, True])
    frame.to_csv(csv_path, index=False)
    md_path.write_text(
        "# Gate Attribution\n\n"
        f"Generated: {now.isoformat()}\n\n"
        f"Candidate rows: {len(candidates)}\n"
        f"Forward marks available: {has_forward}\n"
        f"Top blocked gates: {', '.join(frame.head(5)['gate_name'].astype(str).tolist())}\n",
        encoding="utf-8",
    )
    return {"csv_path": csv_path, "markdown_path": md_path, "rows": len(frame), "frame": frame, "status": "ok"}
