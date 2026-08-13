from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stockml.trading_brain_v2.shared.config import TradingBrainConfig
from stockml.trading_brain_v2.shared.models import AuditEvent, Candidate, EntryDecision, ExitDecision, PortfolioSnapshot


def build_audit_event(
    *,
    event_type: str,
    run_id: str,
    source_file: str,
    symbol: str,
    message: str,
    candidate: Candidate | None = None,
    entry_decision: EntryDecision | None = None,
    exit_decision: ExitDecision | None = None,
    portfolio: PortfolioSnapshot | None = None,
    config: TradingBrainConfig | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    cfg = config or TradingBrainConfig()
    payload = dict(details or {})
    payload.update(
        {
            "run_id": run_id,
            "source_file": source_file,
            "portfolio_state_snapshot": portfolio.to_dict() if portfolio else None,
            "config_version": "trading_brain_v2_policy_v1",
            "active_version": cfg.active_version,
            "shadow_mode": cfg.v2_shadow_mode,
            "live_execution_allowed": cfg.v2_allow_live_execution,
        }
    )
    if candidate:
        payload.update(
            {
                "ai2_status": candidate.ai2_status,
                "candidate_status": candidate.candidate_status,
                "warning_codes": list(candidate.warning_codes),
                "close_price": candidate.close_price,
            }
        )
    if entry_decision:
        payload.update(
            {
                "decision_action": entry_decision.action.value,
                "decision_reason": entry_decision.reason,
                "risk_score": entry_decision.risk_profile.get("risk_score"),
                "risk_multiplier": entry_decision.risk_profile.get("final_risk_multiplier"),
                "position_size": entry_decision.qty,
            }
        )
    if exit_decision:
        payload.update({"decision_action": exit_decision.action.value, "decision_reason": exit_decision.reason, "live_price": exit_decision.current_price, "pnl": exit_decision.pnl, "pnl_pct": exit_decision.pnl_pct})
    signal_id = (candidate.signal_id if candidate else "") or (entry_decision.signal_id if entry_decision else "") or (exit_decision.signal_id if exit_decision else "")
    candidate_id = (candidate.candidate_id if candidate else "") or (entry_decision.candidate_id if entry_decision else "") or (exit_decision.candidate_id if exit_decision else "")
    event_id = (candidate.event_id if candidate else "") or (entry_decision.event_id if entry_decision else "") or (exit_decision.event_id if exit_decision else "")
    return AuditEvent(datetime.now(timezone.utc).isoformat(), event_type, "trading_brain_v2", symbol, message, signal_id, candidate_id, event_id, payload)


class AuditLogger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, event: AuditEvent) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
        return self.path

    def read(self, *, symbol: str | None = None, run_id: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if symbol:
            records = [record for record in records if record.get("symbol") == symbol]
        if run_id:
            records = [record for record in records if record.get("details", {}).get("run_id") == run_id]
        return records
