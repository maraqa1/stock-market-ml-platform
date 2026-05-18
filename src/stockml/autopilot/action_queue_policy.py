from __future__ import annotations

from typing import Any

from stockml.autopilot.position_health import PositionHealthRules, classify_position_health


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _append_reason(reason: str, addition: str) -> str:
    parts = [part for part in str(reason or "").split(";") if part]
    if addition not in parts:
        parts.append(addition)
    return ";".join(parts)


def _apply_button(operator_label: str, decision: str) -> str:
    clean_label = _text(operator_label).lower()
    clean_decision = _text(decision).lower()
    if clean_label == "watch only" or clean_decision in {"watch", "watch_loss", "healthy_hold"}:
        return "Acknowledge"
    if clean_decision == "close_now":
        return "Close now"
    if clean_decision == "close_candidate" or clean_label == "review close":
        return "Review close"
    if clean_label in {"review open", "review"}:
        return "Review"
    if clean_label == "open approved":
        return "Open paper order"
    return operator_label or "Review"


def classify_action_queue_item(
    item: dict[str, Any],
    *,
    held_symbols: set[str] | None = None,
    rules: PositionHealthRules | None = None,
) -> dict[str, Any]:
    out = dict(item)
    decision = _text(out.get("decision")).lower()
    reason = _text(out.get("decision_reason"))
    held = held_symbols or set()
    symbol = _text(out.get("symbol")).upper()

    if decision == "open_candidate":
        signal_status = _text(out.get("latest_signal_status") or out.get("signal_state")).lower()
        if "candidate_slot_available" in reason and signal_status != "fresh":
            reason = _append_reason(reason, "requires_fresh_rescore")
            out.update(
                {
                    "decision_reason": reason,
                    "operator_call": "info",
                    "operator_call_label": "Review open",
                    "operator_call_reason": "Candidate slot is available, but a fresh signal rescore is required before any paper order.",
                    "operator_apply_enabled": False,
                }
            )
        else:
            out.setdefault("operator_call", "info")
            out.setdefault("operator_call_label", "Review open")
            out.setdefault("operator_call_reason", "Review candidate for possible paper entry.")
            out.setdefault("operator_apply_enabled", False)
        out["action_button_label"] = _apply_button(out.get("operator_call_label", ""), out.get("decision", ""))
        return out

    if symbol and symbol in held and decision in {"watch", "close"}:
        health = classify_position_health(out, rules)
        out.update(health)
        status = health["position_health_status"]
        if decision == "close" and status != "close_now":
            status = "close_candidate"
            out["position_health_status"] = status
        if status == "close_now":
            out.update(
                {
                    "decision": "close_now",
                    "operator_call": "close",
                    "operator_call_label": "Close now",
                    "operator_call_reason": "Close-now risk condition is breached for this open paper position.",
                    "operator_apply_enabled": True,
                }
            )
        elif status == "close_candidate":
            out.update(
                {
                    "decision": "close_candidate",
                    "operator_call": "close",
                    "operator_call_label": "Review close",
                    "operator_call_reason": "Close candidate. Review broker state before paper close submission.",
                    "operator_apply_enabled": True,
                }
            )
        elif status in {"watch", "watch_loss", "healthy_hold"}:
            out.update(
                {
                    "decision": status,
                    "operator_call": "watch",
                    "operator_call_label": "Watch only",
                    "operator_call_reason": "No paper order. Acknowledge and continue monitoring.",
                    "operator_apply_enabled": False,
                }
            )

    out["action_button_label"] = _apply_button(out.get("operator_call_label", ""), out.get("decision", ""))
    return out
