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
    if clean_label in {"review open", "review replacement", "review"}:
        return "Review"
    if clean_label == "open approved":
        return "Open paper order"
    return operator_label or "Review"


def _close_is_automatic(mode: str | None) -> bool:
    return _text(mode).lower() != "review_only"


def classify_action_queue_item(
    item: dict[str, Any],
    *,
    held_symbols: set[str] | None = None,
    rules: PositionHealthRules | None = None,
    close_automation_mode: str = "automatic",
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

    if decision == "replace" and _text(out.get("recommended_action")).lower() == "review_edge_replacement":
        replacement = _text(out.get("replacement_symbol")).upper()
        detail = f" Stronger candidate: {replacement}." if replacement else ""
        out.update(
            {
                "operator_call": "warning",
                "operator_call_label": "Review replacement",
                "operator_call_reason": (
                    "A stronger same-side candidate is available, but this is review-only; "
                    "confirm close and replacement before any paper order."
                    + detail
                ),
                "operator_apply_enabled": False,
            }
        )
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
            automatic = _close_is_automatic(close_automation_mode) and decision == "close"
            out.update(
                {
                    "decision": "close_now",
                    "operator_call": "close" if automatic else "warning",
                    "operator_call_label": "Auto close now" if automatic else "Review close now",
                    "operator_call_reason": (
                        "Paper Autopilot will submit this close automatically on the next clock tick."
                        if automatic
                        else "Close-now risk condition is visible, but the monitor has not emitted an automatic close decision."
                    ),
                    "operator_apply_enabled": False,
                }
            )
        elif status == "close_candidate":
            preclassified_watch = _text(out.get("operator_call_label")).lower() == "watch only"
            automatic = _close_is_automatic(close_automation_mode) and not preclassified_watch
            out.update(
                {
                    "decision": "close_candidate",
                    "operator_call": "close" if automatic else "warning",
                    "operator_call_label": "Auto close" if automatic else "Review close",
                    "operator_call_reason": (
                        "Paper Autopilot will submit this close automatically when the clock runs."
                        if automatic
                        else "Close candidate is visible in position health, but the monitor has not emitted an automatic close decision."
                    ),
                    "operator_apply_enabled": False if automatic or preclassified_watch else True,
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

    if _close_is_automatic(close_automation_mode) and out.get("operator_call_label") in {"Auto close", "Auto close now"}:
        out["action_button_label"] = "Auto managed"
    else:
        out["action_button_label"] = _apply_button(out.get("operator_call_label", ""), out.get("decision", ""))
    return out
