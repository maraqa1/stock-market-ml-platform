from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PositionLifecycleConfig:
    require_exit_confirmation: bool = True
    stale_signal_is_exit_reason: bool = False
    unknown_signal_is_exit_reason: bool = False
    defensive_close_requires_loss_or_risk_breach: bool = True


def _text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "" if text in {"nan", "none", "null"} else text


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _truthy(value: Any) -> bool:
    return _text(value) in {"1", "true", "yes", "y", "on"} or value is True


def evaluate_exit_request(position: dict[str, Any] | None = None, *, reason: str = "", config: PositionLifecycleConfig | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or PositionLifecycleConfig()
    pos = dict(position or {})
    ctx = {**pos, **dict(context or {})}
    clean_reason = _text(reason or ctx.get("decision_reason") or ctx.get("reason") or ctx.get("close_reason"))

    if "signal_stale" in clean_reason or "stale_signal" in clean_reason:
        if not cfg.stale_signal_is_exit_reason and not any(token in clean_reason for token in ("hard_stop", "stop_loss", "take_profit", "emergency")):
            return {"allowed": False, "reason": "stale_signal_not_exit_reason"}
    if "latest_signal_unknown" in clean_reason or "unknown_signal" in clean_reason or clean_reason == "unknown":
        if not cfg.unknown_signal_is_exit_reason and not any(token in clean_reason for token in ("hard_stop", "stop_loss", "take_profit", "emergency")):
            return {"allowed": False, "reason": "unknown_signal_not_exit_reason"}

    defensive = "defensive_close" in clean_reason or "defensive_stale" in clean_reason
    if defensive and cfg.defensive_close_requires_loss_or_risk_breach:
        plpc = _float(ctx.get("unrealized_plpc") or ctx.get("plpc") or ctx.get("pnl_pct") or ctx.get("return_pct"), 0.0)
        has_loss = plpc < 0 or _float(ctx.get("unrealized_pl") or ctx.get("pnl"), 0.0) < 0
        risk_breach = any(
            _truthy(ctx.get(key))
            for key in ("loss_threshold_breached", "basket_risk_breach", "risk_tier_reject", "confirmed_reversal", "hard_stop_hit")
        )
        if not has_loss and not risk_breach:
            return {"allowed": False, "reason": "defensive_close_requires_loss_or_risk_breach"}

    return {"allowed": True, "reason": "allowed"}
