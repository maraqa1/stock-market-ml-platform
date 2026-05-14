from __future__ import annotations

from typing import Any


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none"} else text


def _expected_side(record: dict[str, Any]) -> str:
    side = _text(record.get("side")).lower()
    action = _text(record.get("current_trade_action") or record.get("trade_action")).lower()
    if side == "sell" or action == "short":
        return "short"
    if side == "buy" or action == "long":
        return "long"
    return ""


def _direction_side(record: dict[str, Any]) -> str:
    direction = _text(record.get("direction_context")).lower()
    if direction.startswith("short"):
        return "short"
    if direction.startswith("long"):
        return "long"
    return ""


def side_alignment(record: dict[str, Any]) -> str:
    expected = _expected_side(record)
    direction = _direction_side(record)
    if not expected or not direction:
        return "unknown"
    return "aligned" if expected == direction else "conflicted"


def confirmation_fields(record: dict[str, Any]) -> dict[str, Any]:
    alignment = side_alignment(record)
    expected_move = _num(record.get("expected_move_bps"))
    profitability = _num(record.get("expected_profitability_score"))
    stop = _num(record.get("suggested_stop_bps"))
    take_profit = _num(record.get("suggested_take_profit_bps"))

    magnitude_ok = expected_move is not None and expected_move >= 50.0
    profitability_ok = profitability is not None and profitability > 0.0
    risk_reward_ok = stop is not None and take_profit is not None and stop > 0 and take_profit >= stop

    score = 0
    reasons: list[str] = []
    if alignment == "aligned":
        score += 40
        reasons.append("side_aligned")
    elif alignment == "conflicted":
        reasons.append("side_conflicted")
    else:
        reasons.append("side_unknown")

    if magnitude_ok:
        score += 20
        reasons.append("magnitude_ok")
    else:
        reasons.append("magnitude_weak")

    if profitability_ok:
        score += 25
        reasons.append("profitability_ok")
    else:
        reasons.append("profitability_weak")

    if risk_reward_ok:
        score += 15
        reasons.append("risk_reward_ok")
    else:
        reasons.append("risk_reward_incomplete")

    if alignment == "conflicted":
        confirmation = "conflicted"
    elif score >= 80:
        confirmation = "confirmed"
    elif score >= 55:
        confirmation = "weak_confirm"
    else:
        confirmation = "insufficient_data"

    return {
        "forecast_confirmation": confirmation,
        "confirmation_score": score,
        "confirmation_reason": ";".join(reasons),
        "side_alignment": alignment,
        "magnitude_ok": magnitude_ok,
        "profitability_ok": profitability_ok,
        "risk_reward_ok": risk_reward_ok,
    }
