from __future__ import annotations

from typing import Any, Iterable

from stockml.autopilot.position_lifecycle_guard import PositionLifecycleConfig, evaluate_exit_request


def apply_position_lifecycle_policy(rows: Iterable[dict[str, Any]], *, config: PositionLifecycleConfig | None = None) -> list[dict[str, Any]]:
    """Annotate monitor rows that ask for a close with lifecycle guard decisions."""
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        decision = str(item.get("decision") or "").lower()
        if decision in {"close", "close_now", "close_candidate"}:
            check = evaluate_exit_request(item, reason=str(item.get("decision_reason") or ""), config=config)
            if not check["allowed"]:
                item["decision"] = "manual_review"
                item["operator_call"] = "warning"
                item["operator_call_label"] = "Manual review"
                item["operator_call_reason"] = check["reason"]
                item["operator_apply_enabled"] = False
        out.append(item)
    return out
