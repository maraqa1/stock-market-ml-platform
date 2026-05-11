from __future__ import annotations

from datetime import datetime
from typing import Any

from stockml.intraday import kill_switch


def _clean_event(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    value = out.get("occurred_at")
    if isinstance(value, datetime):
        out["occurred_at"] = value.isoformat(timespec="seconds")
    return out


def kill_switch_context() -> dict[str, Any]:
    payload = kill_switch.state()
    switches = []
    for row in payload.get("switches", []):
        status = str(row.get("status") or "armed")
        switches.append(
            {
                **row,
                "label": str(row.get("name", "")).replace(".", " / ").replace("_", " ").title(),
                "status_label": "Tripped" if status == "tripped" else "Armed",
                "pill_status": "failed" if status == "tripped" else "safe",
                "requires_manual_resume": status == "tripped",
            }
        )
    events = [_clean_event(dict(row)) for row in payload.get("events", [])]
    return {
        **payload,
        "switches": switches,
        "events": list(reversed(events[-20:])),
        "tripped_count": len(payload.get("active", [])),
        "armed_count": max(0, len(switches) - len(payload.get("active", []))),
    }


def resume_kill_switch(switch_name: str, operator_id: str, notes: str) -> None:
    kill_switch.resume(switch_name, operator_id, notes)

