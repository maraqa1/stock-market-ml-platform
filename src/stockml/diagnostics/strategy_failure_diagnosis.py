from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT
from stockml.diagnostics.position_gate_degradation import build_position_gate_degradation


DIAGNOSTICS_DIR = PROJECT_ROOT / "data" / "trading" / "diagnostics"


def build_strategy_failure_diagnosis(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    csv_path = DIAGNOSTICS_DIR / f"strategy_failure_diagnosis_{stamp}.csv"
    md_path = DIAGNOSTICS_DIR / f"strategy_failure_diagnosis_{stamp}.md"
    degradation = build_position_gate_degradation(now)
    frame = degradation["frame"]
    rows: list[dict[str, Any]] = []

    if frame.empty or degradation["status"] == "insufficient_data":
        rows.append({"failure_area": "insufficient_data", "evidence": "open position evidence missing", "affected_symbols": "", "affected_count": 0, "pnl_impact_if_available": 0.0, "confidence": "high", "recommended_next_action": "collect_position_and_candidate_context"})
    else:
        symbols = frame.get("symbol", pd.Series(dtype=str)).astype(str)
        pnl = pd.to_numeric(frame.get("unrealized_pl", pd.Series(0, index=frame.index)), errors="coerce").fillna(0)
        rejected = frame.get("trade_quality_status", pd.Series("", index=frame.index)).astype(str).str.lower().eq("rejected")
        close_like = frame.get("suggested_position_action", pd.Series("", index=frame.index)).astype(str).isin(["close_candidate", "urgent_close_review"])
        short_mask = frame.get("side", frame.get("side_norm", pd.Series("", index=frame.index))).astype(str).str.lower().eq("short")
        overnight = frame.get("session_mode", pd.Series("", index=frame.index)).astype(str).str.lower().eq("overnight_24_5")
        missing = frame.get("missing_evidence_fields", pd.Series("", index=frame.index)).astype(str).ne("")
        rows.extend(
            [
                {"failure_area": "position_management_problem", "evidence": "positions require close/reduce/review actions", "affected_symbols": ",".join(symbols[close_like].tolist()), "affected_count": int(close_like.sum()), "pnl_impact_if_available": round(float(pnl[close_like].sum()), 2), "confidence": "high" if close_like.any() else "medium", "recommended_next_action": "apply diagnostics-only position management review before new entries"},
                {"failure_area": "gate_degradation_problem", "evidence": "open positions now rejected or failing gates", "affected_symbols": ",".join(symbols[rejected].tolist()), "affected_count": int(rejected.sum()), "pnl_impact_if_available": round(float(pnl[rejected].sum()), 2), "confidence": "high" if rejected.any() else "low", "recommended_next_action": "separate entry gates from post-entry degradation actions"},
                {"failure_area": "short_side_problem", "evidence": "short positions or short candidates require separate validation", "affected_symbols": ",".join(symbols[short_mask].tolist()), "affected_count": int(short_mask.sum()), "pnl_impact_if_available": round(float(pnl[short_mask].sum()), 2), "confidence": "high" if short_mask.any() else "monitor", "recommended_next_action": "keep shorts research-only until short attribution is positive"},
                {"failure_area": "overnight_24x5_problem", "evidence": "positions/candidates in overnight_24_5 require stricter execution evidence", "affected_symbols": ",".join(symbols[overnight].tolist()), "affected_count": int(overnight.sum()), "pnl_impact_if_available": round(float(pnl[overnight].sum()), 2), "confidence": "medium" if overnight.any() else "low", "recommended_next_action": "treat 24x5 as diagnostics-only unless overnight attribution is profitable"},
                {"failure_area": "missing_lineage_problem", "evidence": "position quality or candidate context missing", "affected_symbols": ",".join(symbols[missing].tolist()), "affected_count": int(missing.sum()), "pnl_impact_if_available": round(float(pnl[missing].sum()), 2), "confidence": "medium" if missing.any() else "low", "recommended_next_action": "improve position-to-candidate lineage coverage"},
            ]
        )
        rows.append({"failure_area": "gate_removal", "evidence": "current losses argue for position degradation handling, not removing gates", "affected_symbols": "", "affected_count": 0, "pnl_impact_if_available": 0.0, "confidence": "high", "recommended_next_action": "do_not_remove_must_have_gates"})

    out = pd.DataFrame(rows)
    out.to_csv(csv_path, index=False)
    md_path.write_text(
        "# Strategy Failure Diagnosis\n\n"
        f"Generated: {now.isoformat()}\n\n"
        + "\n".join(f"- {row['failure_area']}: {row['confidence']} confidence" for row in rows)
        + "\n",
        encoding="utf-8",
    )
    return {"csv_path": csv_path, "markdown_path": md_path, "rows": len(out), "frame": out, "status": "ok"}
