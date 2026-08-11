from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.candidates.execution_ranker import execution_ranked_auto_open_frame
from stockml.common.paths import DATA_DIR, PORTAL_OUTPUTS_DIR, TRADING_DIR, data_root, latest_file, timestamp


ACTION_VALUES = ("hold", "reduce", "increase", "close", "replace", "manual_review")
OUTPUT_COLUMNS = [
    "decision_id",
    "generated_at",
    "symbol",
    "side",
    "qty",
    "entry_price",
    "last_price",
    "position_age_minutes",
    "pnl_pct",
    "pnl_amount",
    "peak_pnl_pct",
    "giveback_pct",
    "source_trade_action",
    "directional_action",
    "model_signal_state",
    "signal_alignment",
    "trading_stream",
    "holding_quality",
    "holding_gate_pass",
    "holding_gate_reason",
    "holding_review_reason",
    "rank_status",
    "rank_change",
    "intraday_momentum_state",
    "quote_status",
    "spread_bps",
    "liquidity_status",
    "sector_concentration_status",
    "basket_risk_status",
    "anti_churn_status",
    "cooldown_status",
    "open_order_status",
    "pending_action_id",
    "last_position_action_at",
    "position_cap_status",
    "max_allowed_position_qty",
    "planned_suggested_quantity",
    "planned_approved_notional",
    "short_risk_status",
    "borrow_status",
    "data_quality_status",
    "decision_precedence_level",
    "recommended_action",
    "action_strength",
    "decision_confidence",
    "recommended_target_qty",
    "recommended_delta_qty",
    "recommended_delta_notional",
    "recommended_fraction_to_reduce",
    "replacement_symbol",
    "replacement_reason",
    "replacement_edge_bps",
    "replacement_quality_status",
    "replacement_risk_tier",
    "primary_reason",
    "supporting_reasons",
    "blocking_guard",
    "would_submit_order",
    "execution_allowed",
    "diagnostics_only",
]


@dataclass(frozen=True)
class PositionManagementConfig:
    loss_threshold_pct: float = -0.02
    hard_stop_pct: float = -0.04
    minimum_hold_minutes: int = 30
    meaningful_profit_pct: float = 0.02
    moderate_giveback_pct: float = 0.01
    severe_giveback_pct: float = 0.02
    max_spread_bps: float = 25.0
    default_max_position_qty_multiplier: float = 2.0
    short_add_enabled: bool = False
    profitable_replacement_min_edge_bps: float = 0.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _lower(value: Any) -> str:
    return _text(value).lower()


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        parsed = float(value)
        if pd.isna(parsed) or math.isinf(parsed):
            return default
        return parsed
    except Exception:
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _lower(value) in {"1", "true", "yes", "y", "on"}


def _bool_or_none(value: Any) -> bool | None:
    text = _lower(value)
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _time(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _side(value: Any, qty: float | None = None) -> str:
    text = _lower(value)
    if text in {"long", "buy"}:
        return "long"
    if text in {"short", "sell"}:
        return "short"
    if qty is not None and qty < 0:
        return "short"
    if qty is not None and qty > 0:
        return "long"
    return ""


def _action_side(value: Any) -> str:
    text = _lower(value).replace("_", " ")
    if text in {"long", "buy", "trade buy", "strong trade buy"} or "long" in text:
        return "long"
    if text in {"short", "sell"} or "short" in text:
        return "short"
    return ""


def _qty(row: dict[str, Any]) -> float | None:
    for key in ("qty", "quantity", "position_qty"):
        value = _float(row.get(key))
        if value is not None:
            return value
    return None


def _entry(row: dict[str, Any]) -> float | None:
    for key in ("entry_price", "avg_entry_price", "average_entry_price"):
        value = _float(row.get(key))
        if value and value > 0:
            return value
    return None


def _last(row: dict[str, Any]) -> float | None:
    for key in ("last_price", "current_price", "market_price", "close"):
        value = _float(row.get(key))
        if value and value > 0:
            return value
    return None


def _pnl_pct(row: dict[str, Any], side: str, entry: float | None, last: float | None) -> float | None:
    for key in ("pnl_pct", "unrealized_plpc", "return_pct", "position_unrealized_plpc"):
        value = _float(row.get(key))
        if value is not None:
            return value if abs(value) <= 1.5 else value / 100.0
    if entry and last:
        raw = (last / entry) - 1.0
        return -raw if side == "short" else raw
    return None


def _pnl_amount(row: dict[str, Any], qty: float, side: str, entry: float | None, last: float | None) -> float | None:
    for key in ("pnl_amount", "unrealized_pl", "profit_loss"):
        value = _float(row.get(key))
        if value is not None:
            return value
    if entry and last:
        signed_qty = abs(qty)
        return (entry - last) * signed_qty if side == "short" else (last - entry) * signed_qty
    return None


def _age_minutes(row: dict[str, Any], now: datetime) -> float | None:
    for key in ("position_age_minutes", "age_minutes"):
        value = _float(row.get(key))
        if value is not None:
            return max(0.0, value)
    for key in ("opened_at", "filled_at", "submitted_at", "created_at", "entry_time"):
        opened = _time(row.get(key))
        if opened:
            return max(0.0, (now - opened).total_seconds() / 60.0)
    return None


def _decision_id(generated_at: str, symbol: str) -> str:
    return f"pm-{generated_at.replace('-', '').replace(':', '').replace('+', '').replace('T', '-')}-{symbol}"


def _base_output(row: dict[str, Any], now: datetime, config: PositionManagementConfig) -> dict[str, Any]:
    generated = now.isoformat()
    qty = _qty(row)
    entry = _entry(row)
    last = _last(row)
    side = _side(row.get("side") or row.get("bias"), qty)
    abs_qty = abs(qty) if qty is not None else None
    pnl_pct = _pnl_pct(row, side, entry, last)
    peak = _float(row.get("peak_pnl_pct") or row.get("peak_plpc"), pnl_pct if pnl_pct is not None else 0.0)
    if peak is not None and abs(peak) > 1.5:
        peak = peak / 100.0
    giveback = max(0.0, (peak or 0.0) - (pnl_pct or 0.0))
    max_qty = _float(row.get("max_allowed_position_qty"))
    if max_qty is None and abs_qty is not None:
        max_qty = max(abs_qty, math.floor(abs_qty * config.default_max_position_qty_multiplier))
    symbol = _text(row.get("symbol")).upper()
    return {
        "decision_id": _decision_id(generated, symbol),
        "generated_at": generated,
        "symbol": symbol,
        "side": side,
        "qty": abs_qty,
        "entry_price": entry,
        "last_price": last,
        "position_age_minutes": _age_minutes(row, now),
        "pnl_pct": pnl_pct,
        "pnl_amount": _pnl_amount(row, qty or 0.0, side, entry, last) if qty is not None else None,
        "peak_pnl_pct": peak,
        "giveback_pct": giveback,
        "source_trade_action": _text(row.get("source_trade_action") or row.get("current_trade_action") or row.get("trade_action")),
        "directional_action": _text(row.get("directional_action")),
        "model_signal_state": _lower(row.get("model_signal_state") or row.get("latest_signal_status") or row.get("signal_state") or "fresh"),
        "signal_alignment": _lower(row.get("signal_alignment") or row.get("side_alignment") or ""),
        "trading_stream": _lower(row.get("trading_stream") or row.get("strategy_stream")),
        "holding_quality": _lower(row.get("holding_quality")),
        "holding_gate_pass": _bool_or_none(row.get("holding_gate_pass")),
        "holding_gate_reason": _text(row.get("holding_gate_reason") or row.get("holding_review_reason")),
        "holding_review_reason": _text(row.get("holding_review_reason") or row.get("holding_gate_reason")),
        "rank_status": _lower(row.get("rank_status") or row.get("candidate_rank_status")),
        "rank_change": _float(row.get("rank_change")),
        "intraday_momentum_state": _lower(row.get("intraday_momentum_state")),
        "quote_status": _lower(row.get("quote_status") or row.get("session_reject_reason") or "unknown"),
        "spread_bps": _float(row.get("spread_bps")),
        "liquidity_status": _lower(row.get("liquidity_status")),
        "sector_concentration_status": _lower(row.get("sector_concentration_status")),
        "basket_risk_status": _lower(row.get("basket_risk_status") or row.get("basket_state") or "normal"),
        "anti_churn_status": _lower(row.get("anti_churn_status") or "clear"),
        "cooldown_status": _lower(row.get("cooldown_status") or "clear"),
        "open_order_status": _lower(row.get("open_order_status")),
        "pending_action_id": _text(row.get("pending_action_id") or row.get("order_id")),
        "last_position_action_at": _text(row.get("last_position_action_at")),
        "position_cap_status": _lower(row.get("position_cap_status")),
        "max_allowed_position_qty": max_qty,
        "planned_suggested_quantity": _float(row.get("planned_suggested_quantity") or row.get("approved_target_quantity")),
        "planned_approved_notional": _float(row.get("planned_approved_notional") or row.get("approved_notional")),
        "short_risk_status": _lower(row.get("short_risk_status")),
        "borrow_status": _lower(row.get("borrow_status") or row.get("shortable_status")),
        "data_quality_status": "ok",
        "decision_precedence_level": 9,
        "recommended_action": "hold",
        "action_strength": "low",
        "decision_confidence": "medium",
        "recommended_target_qty": abs_qty,
        "recommended_delta_qty": 0.0,
        "recommended_delta_notional": 0.0,
        "recommended_fraction_to_reduce": 0.0,
        "replacement_symbol": _text(row.get("replacement_symbol")).upper(),
        "replacement_reason": _text(row.get("replacement_reason")),
        "replacement_edge_bps": _float(row.get("replacement_edge_bps")),
        "replacement_quality_status": _lower(row.get("replacement_quality_status")),
        "replacement_risk_tier": _lower(row.get("replacement_risk_tier")),
        "primary_reason": "no_action_required",
        "supporting_reasons": "",
        "blocking_guard": "",
        "would_submit_order": False,
        "execution_allowed": False,
        "diagnostics_only": True,
    }


def _finalize(
    out: dict[str, Any],
    action: str,
    reason: str,
    *,
    level: int,
    strength: str = "medium",
    confidence: str = "medium",
    guard: str = "",
    support: list[str] | None = None,
    fraction: float = 0.0,
    target_qty: float | None = None,
) -> dict[str, Any]:
    qty = _float(out.get("qty"), 0.0) or 0.0
    last = _float(out.get("last_price"), 0.0) or 0.0
    target = qty
    if action == "close":
        target = 0.0
        fraction = 1.0
    elif action == "reduce":
        if target_qty is not None:
            target = max(0.0, min(qty, math.floor(target_qty)))
            fraction = 1.0 if qty <= 0 else max(0.0, min(1.0, (qty - target) / qty))
        else:
            fraction = fraction or 0.5
            target = max(1.0, math.floor(qty * (1.0 - fraction))) if qty > 1 else 0.0
        if target <= 0:
            action = "close"
            fraction = 1.0
    elif action == "increase":
        max_qty = _float(out.get("max_allowed_position_qty"), qty) or qty
        target = min(max_qty, math.floor(qty * 1.25) if qty > 0 else 1.0)
        if target <= qty:
            action = "hold"
            reason = "position_cap_reached"
            target = qty
    elif action == "replace":
        target = 0.0
        fraction = 1.0
    elif action in {"hold", "manual_review"}:
        target = qty
        fraction = 0.0
    delta = target - qty
    out.update(
        {
            "recommended_action": action,
            "primary_reason": reason,
            "decision_precedence_level": level,
            "action_strength": strength,
            "decision_confidence": confidence,
            "blocking_guard": guard,
            "supporting_reasons": ";".join(support or []),
            "recommended_target_qty": target,
            "recommended_delta_qty": delta,
            "recommended_delta_notional": delta * last,
            "recommended_fraction_to_reduce": fraction,
            "would_submit_order": False,
            "execution_allowed": False,
            "diagnostics_only": True,
        }
    )
    return out


def decide_position(row: dict[str, Any], *, now: datetime | None = None, config: PositionManagementConfig | None = None) -> dict[str, Any]:
    cfg = config or PositionManagementConfig()
    stamp = now or _now()
    out = _base_output(row, stamp, cfg)
    symbol = out["symbol"]
    qty = _float(out.get("qty"))
    entry = _float(out.get("entry_price"))
    last = _float(out.get("last_price"))
    side = _text(out.get("side"))
    pnl = _float(out.get("pnl_pct"), 0.0) or 0.0
    peak = _float(out.get("peak_pnl_pct"), pnl) or pnl
    giveback = _float(out.get("giveback_pct"), 0.0) or 0.0
    support: list[str] = []

    if not symbol or side not in {"long", "short"} or qty is None or qty <= 0 or entry is None or last is None:
        out["data_quality_status"] = "insufficient_data"
        return _finalize(out, "manual_review", "insufficient_position_data", level=1, strength="high", confidence="high")
    signed_qty = _qty(row)
    if signed_qty == 0 or (side == "long" and signed_qty is not None and signed_qty < 0) or (side == "short" and signed_qty is not None and signed_qty > 0):
        out["data_quality_status"] = "ambiguous"
        return _finalize(out, "manual_review", "ambiguous_position_state", level=1, strength="high", confidence="high")
    if out["open_order_status"] in {"open", "new", "accepted", "pending", "pending_new", "submitted"} or out["pending_action_id"]:
        return _finalize(out, "hold", "action_already_pending", level=1, strength="high", confidence="high", guard="symbol_already_has_open_order")

    reason_blob = " ".join(str(row.get(key) or "") for key in ("reason", "decision_reason", "position_health_reason", "holding_review_reason", "short_risk_status")).lower()
    if _bool(row.get("hard_stop_hit")) or "hard_stop" in reason_blob or pnl <= cfg.hard_stop_pct:
        return _finalize(out, "close", "hard_stop_hit", level=2, strength="high", confidence="high", support=support)
    if _bool(row.get("emergency_risk_breach")) or pnl <= (cfg.hard_stop_pct * 1.5):
        return _finalize(out, "close", "emergency_risk_breach", level=2, strength="high", confidence="high")
    if _bool(row.get("severe_loss_threshold_breached")) or pnl <= -0.06:
        return _finalize(out, "close", "severe_loss_threshold_breached", level=2, strength="high", confidence="high")

    monitor_decision = _lower(row.get("decision") or row.get("recommended_action"))
    age_minutes = _float(out.get("position_age_minutes"))
    fresh_hold_active = age_minutes is not None and age_minutes < cfg.minimum_hold_minutes
    if fresh_hold_active and monitor_decision in {"close", "replace", "rotate"}:
        return _finalize(
            out,
            "hold",
            "minimum_hold_period_not_met",
            level=3,
            strength="high",
            confidence="high",
            support=[f"position_age_minutes={age_minutes:.2f}", f"minimum_hold_minutes={cfg.minimum_hold_minutes}"],
            guard="minimum_hold_period_not_met",
        )
    if monitor_decision == "close":
        return _finalize(out, "close", "monitor_close", level=3, strength="high", confidence="high", support=support)
    max_holding_days = _float(row.get("max_holding_days") or row.get("max_hold_days"))
    if max_holding_days and max_holding_days > 0 and age_minutes is not None and age_minutes >= max_holding_days * 1440:
        return _finalize(out, "close", "max_holding_days_exceeded", level=3, strength="high", confidence="high", support=support)

    alignment = out["signal_alignment"]
    source_side = _action_side(out["source_trade_action"])
    directional_side = _action_side(out["directional_action"])
    confirmed_reversal = _bool(row.get("confirmed_model_reversal")) or alignment in {"reversed", "against", "opposed"} or "confirmed_model_reversal" in reason_blob or "signal_reversal_confirmed" in reason_blob
    if confirmed_reversal:
        if out["anti_churn_status"] not in {"", "clear", "allowed"} or out["cooldown_status"] not in {"", "clear", "allowed"}:
            return _finalize(out, "manual_review", "confirmed_model_reversal_blocked_by_guard", level=3, strength="high", confidence="medium", guard="anti_churn_or_cooldown")
        return _finalize(out, "close", "confirmed_model_reversal", level=3, strength="high", confidence="high")

    signal_state = out["model_signal_state"]
    holding_quality = out["holding_quality"]
    holding_reason = _lower(out["holding_review_reason"])
    edge_failed = holding_quality in {"avoid", "fail", "failed", "reject"} or "holding_edge_not_confirmed" in holding_reason or "holding_edge_failed" in holding_reason
    max_holding_days_for_stream = _float(row.get("max_holding_days") or row.get("max_hold_days"))
    same_day_stream = out["trading_stream"] == "same_day" or max_holding_days_for_stream == 1
    holding_gate_failed = out["holding_gate_pass"] is False or "holding_edge_not_confirmed" in holding_reason or "holding_edge_failed" in holding_reason
    if same_day_stream and edge_failed and holding_gate_failed:
        support.extend(["same_day_position", "holding_edge_failed"])
        if pnl <= 0:
            return _finalize(out, "close", "same_day_holding_edge_failed", level=4, strength="high", confidence="medium", support=support)
        return _finalize(out, "reduce", "same_day_holding_edge_failed_profitable", level=4, strength="medium", confidence="medium", support=support, fraction=0.5)
    planned_qty = _float(out.get("planned_suggested_quantity"))
    if planned_qty is not None and planned_qty > 0 and qty > planned_qty * 1.05:
        return _finalize(
            out,
            "reduce",
            "position_exceeds_approved_plan_size",
            level=4,
            strength="high",
            confidence="high",
            support=["actual_qty_above_planned_suggested_quantity"],
            target_qty=planned_qty,
        )
    weakening = edge_failed or holding_quality == "watch" or alignment in {"weakening", "not_aligned"} or out["rank_status"] in {"deteriorated", "dropped", "weak"} or (_float(out["rank_change"]) is not None and (_float(out["rank_change"]) or 0) < 0)
    fresh_aligned = signal_state in {"fresh", "fresh_or_unflagged", "aligned"} and (source_side == side or alignment in {"aligned", "fresh_aligned"})
    meaningful_profit = pnl >= cfg.meaningful_profit_pct or peak >= cfg.meaningful_profit_pct
    material_giveback = giveback >= cfg.moderate_giveback_pct
    severe_giveback = giveback >= cfg.severe_giveback_pct
    replacement_symbol = _text(out.get("replacement_symbol")).upper()
    replacement_edge = _float(out.get("replacement_edge_bps"), 0.0) or 0.0
    replacement_quality = _lower(out.get("replacement_quality_status"))
    replacement_is_eligible = bool(
        replacement_symbol
        and replacement_symbol != symbol
        and replacement_quality in {"approved", "reduced"}
        and replacement_edge > cfg.profitable_replacement_min_edge_bps
    )

    if replacement_is_eligible and monitor_decision in {"replace", "rotate"} and (weakening or pnl <= cfg.loss_threshold_pct):
        support.extend(
            [
                "central_brain_replacement",
                "eligible_replacement_available",
                f"replacement={replacement_symbol}",
                f"replacement_edge_bps={replacement_edge:.2f}",
            ]
        )
        return _finalize(out, "replace", "central_brain_replace_weak_position", level=4, strength="high", confidence="medium", support=support)

    if pnl > 0 and replacement_is_eligible and weakening:
        support.extend(
            [
                "profitable_position",
                "eligible_replacement_available",
                f"replacement={replacement_symbol}",
                f"replacement_edge_bps={replacement_edge:.2f}",
            ]
        )
        return _finalize(out, "close", "take_profit_hit", level=4, strength="high", confidence="medium", support=support)

    if meaningful_profit and material_giveback:
        support.extend(["meaningful_profit", f"giveback={giveback:.4f}"])
        if giveback >= 0.03 and edge_failed:
            return _finalize(out, "close", "severe_profit_giveback_and_holding_edge_failed", level=4, strength="high", confidence="medium", support=support)
        if not fresh_aligned or weakening:
            return _finalize(out, "reduce", "profit_giveback_with_weakening_edge", level=4, strength="medium", confidence="medium", support=support, fraction=0.5 if severe_giveback else 0.25)
        return _finalize(out, "hold", "profit_giveback_below_fresh_signal_tolerance", level=4, strength="medium", confidence="medium", support=support)

    if weakening:
        support.append("edge_deterioration")
        if pnl > 0:
            return _finalize(out, "reduce", "profitable_position_edge_deteriorated", level=5, strength="medium", confidence="medium", support=support, fraction=0.25)
        if pnl <= cfg.loss_threshold_pct:
            if signal_state in {"stale", "unknown", "missing"} and not source_side:
                return _finalize(out, "manual_review", "losing_position_edge_failed_signal_uncertain", level=5, strength="high", confidence="medium", support=support)
            return _finalize(out, "close", "losing_position_edge_failed", level=5, strength="high", confidence="medium", support=support)
        if signal_state in {"stale", "unknown", "missing"}:
            return _finalize(out, "manual_review", "edge_deteriorated_signal_uncertain", level=5, strength="medium", confidence="medium", support=support)

    basket_risk = out["basket_risk_status"] in {"elevated", "paused", "risk", "high", "breach"}
    sector_risk = out["sector_concentration_status"] in {"elevated", "high", "breach"}
    if basket_risk or sector_risk:
        support.append("basket_or_sector_risk")
        if pnl >= 0:
            return _finalize(out, "reduce", "basket_or_sector_risk_reduce_exposure", level=6, strength="medium", confidence="medium", support=support, fraction=0.25)
        return _finalize(out, "hold", "basket_or_sector_risk_blocks_increase", level=6, strength="medium", confidence="medium", support=support, guard="basket_or_sector_risk")

    spread = _float(out["spread_bps"])
    quote_clean = out["quote_status"] in {"", "ok", "clean", "fresh", "unknown"}
    liquidity_ok = out["liquidity_status"] in {"", "ok", "clean", "high", "medium"}
    below_cap = (_float(out["max_allowed_position_qty"], qty) or qty) > qty and out["position_cap_status"] not in {"at_cap", "over_cap"}
    source_executable = source_side in {"long", "short"}
    source_agrees = source_side == side
    can_increase = (
        pnl >= -0.0025
        and source_executable
        and source_agrees
        and fresh_aligned
        and out["rank_status"] in {"", "top", "high", "strong", "approved", "ranked"}
        and quote_clean
        and (spread is None or spread <= cfg.max_spread_bps)
        and liquidity_ok
        and below_cap
        and out["anti_churn_status"] in {"", "clear", "allowed"}
        and out["cooldown_status"] in {"", "clear", "allowed"}
    )
    if side == "short" and not cfg.short_add_enabled:
        can_increase = False
        short_add_block = True
    else:
        short_add_block = False
    if side == "short" and out["short_risk_status"] in {"squeeze", "squeeze_risk"}:
        return _finalize(out, "close", "short_squeeze_risk", level=7, strength="high", confidence="medium")
    if side == "short" and out["short_risk_status"] in {"high"} and pnl <= cfg.loss_threshold_pct:
        return _finalize(out, "close", "short_severe_adverse_risk", level=7, strength="high", confidence="medium")
    if side == "short" and out["borrow_status"] in {"", "unknown", "risky", "unavailable"} and source_side == "short":
        return _finalize(out, "manual_review", "short_borrow_or_risk_uncertain", level=7, strength="medium", confidence="medium")
    if _lower(out["source_trade_action"]) in {"no decision", "no_decision", "none"}:
        return _finalize(out, "hold", "increase_blocked_no_decision", level=7, strength="medium", confidence="high", guard="source_trade_action_no_decision")
    if source_side == "" and directional_side == side and out["directional_action"]:
        return _finalize(out, "hold", "increase_blocked_directional_action_only", level=7, strength="medium", confidence="high", guard="source_trade_action_missing")
    if source_executable and source_agrees and not can_increase:
        guard = ""
        reason = "increase_conditions_not_met"
        if short_add_block:
            guard = "short_add_disabled"
            reason = "short_increase_blocked_by_default"
        elif not below_cap:
            guard = "position_cap_reached"
            reason = "position_already_at_cap"
        elif spread is not None and spread > cfg.max_spread_bps:
            guard = "spread_too_wide"
            reason = "increase_blocked_spread_too_wide"
        elif basket_risk:
            guard = "basket_risk"
            reason = "increase_blocked_basket_risk"
        return _finalize(out, "hold", reason, level=7, strength="medium", confidence="medium", guard=guard)
    if can_increase:
        return _finalize(out, "increase", "aligned_profitable_position_below_cap", level=7, strength="medium", confidence="medium")

    if _bool(row.get("stronger_candidate_available")) and weakening and out["data_quality_status"] == "ok":
        return _finalize(out, "replace", "stronger_candidate_available", level=8, strength="medium", confidence="low")

    if signal_state in {"stale", "unknown", "missing"}:
        if pnl <= cfg.loss_threshold_pct:
            return _finalize(out, "manual_review", f"{signal_state}_signal_with_loss_threshold", level=9, strength="medium", confidence="medium")
        if pnl > 0 and giveback >= cfg.moderate_giveback_pct:
            return _finalize(out, "reduce", f"{signal_state}_signal_profit_giveback", level=9, strength="medium", confidence="medium", fraction=0.25)
        return _finalize(out, "hold", f"{signal_state}_signal_no_exit_evidence", level=9, strength="low", confidence="medium")

    return _finalize(out, "hold", "no_action_required", level=9, strength="low", confidence="medium")


def _read_latest(directory: Path, pattern: str) -> pd.DataFrame:
    path = latest_file(directory, pattern)
    if path is None:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _map_by_symbol(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty or "symbol" not in frame.columns:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in frame.fillna("").to_dict("records"):
        symbol = _text(row.get("symbol")).upper()
        if symbol:
            out[symbol] = row
    return out


def _open_order_map(tracking: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if tracking.empty or "symbol" not in tracking.columns:
        return {}
    status = tracking.get("alpaca_status", tracking.get("status", pd.Series("", index=tracking.index))).fillna("").astype(str).str.lower()
    openish = status.isin({"new", "accepted", "pending_new", "pending", "partially_filled", "submitted"})
    out: dict[str, dict[str, Any]] = {}
    for row in tracking[openish].fillna("").to_dict("records"):
        symbol = _text(row.get("symbol")).upper()
        if symbol:
            out[symbol] = row
    return out


def enrich_positions(
    positions: pd.DataFrame,
    *,
    holding_review: pd.DataFrame | None = None,
    order_tracking: pd.DataFrame | None = None,
    candidate_plan: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    reviews = _map_by_symbol(holding_review if holding_review is not None else pd.DataFrame())
    plans = _map_by_symbol(candidate_plan if candidate_plan is not None else pd.DataFrame())
    orders = _open_order_map(order_tracking if order_tracking is not None else pd.DataFrame())
    rows: list[dict[str, Any]] = []
    for row in positions.fillna("").to_dict("records"):
        symbol = _text(row.get("symbol")).upper()
        merged = dict(row)
        if symbol in reviews:
            merged.update({key: value for key, value in reviews[symbol].items() if key not in merged or merged.get(key) in ("", None)})
        if symbol in plans:
            for key in ("source_trade_action", "trade_action", "directional_action", "rank_status", "rank_change", "risk_tier", "spread_bps", "liquidity_status"):
                if key in plans[symbol] and not merged.get(key):
                    merged[key] = plans[symbol][key]
            if "suggested_quantity" in plans[symbol]:
                merged["planned_suggested_quantity"] = plans[symbol]["suggested_quantity"]
            if "approved_notional" in plans[symbol]:
                merged["planned_approved_notional"] = plans[symbol]["approved_notional"]
        if symbol in orders:
            order = orders[symbol]
            merged["open_order_status"] = order.get("alpaca_status") or order.get("status") or "open"
            merged["pending_action_id"] = order.get("order_id") or order.get("client_order_id")
        rows.append(merged)
    return rows


def build_position_management_decisions(
    positions: pd.DataFrame,
    *,
    holding_review: pd.DataFrame | None = None,
    order_tracking: pd.DataFrame | None = None,
    candidate_plan: pd.DataFrame | None = None,
    now: datetime | None = None,
    config: PositionManagementConfig | None = None,
) -> pd.DataFrame:
    stamp = now or _now()
    rows = enrich_positions(
        positions,
        holding_review=holding_review,
        order_tracking=order_tracking,
        candidate_plan=candidate_plan,
    )
    decisions = [decide_position(row, now=stamp, config=config) for row in rows]
    if not decisions:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(decisions).reindex(columns=OUTPUT_COLUMNS)


def _diagnostics_dir(root: Path | None = None) -> Path:
    base = Path(root) if root else DATA_DIR.parent
    return base / "data" / "trading" / "diagnostics"


def latest_input_frames(root: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = data_root(root)
    positions = _read_latest(base / "portal_outputs", "08_alpaca_paper_positions_*.csv")
    holding = _read_latest(base / "trading" / "holding_period", "holding_review_*.csv")
    tracking = _read_latest(base / "portal_outputs", "08_alpaca_paper_order_tracking_*.csv")
    plan = execution_ranked_auto_open_frame(root=root)
    if plan.empty:
        plan = _read_latest(base / "portal_outputs", "08_alpaca_paper_order_plan_*.csv")
    return positions, holding, tracking, plan


def write_markdown_report(decisions: pd.DataFrame, path: Path) -> None:
    counts = decisions["recommended_action"].value_counts().to_dict() if not decisions.empty else {}
    def _markdown_table(frame: pd.DataFrame) -> str:
        if frame.empty:
            return "None."
        cols = ["symbol", "side", "pnl_pct", "recommended_action", "primary_reason", "blocking_guard"]
        cols = [column for column in cols if column in frame.columns]
        rows = frame[cols].head(10).fillna("").astype(str).to_dict("records")
        header = "| " + " | ".join(cols) + " |"
        sep = "| " + " | ".join(["---"] * len(cols)) + " |"
        body = ["| " + " | ".join(str(row.get(column, "")) for column in cols) + " |" for row in rows]
        return "\n".join([header, sep, *body])

    def _section(title: str, frame: pd.DataFrame) -> list[str]:
        lines = [f"## {title}", ""]
        if frame.empty:
            return lines + ["None.", ""]
        lines.append(_markdown_table(frame))
        lines.append("")
        return lines
    lines = [
        "# Unified Position Management Decisions",
        "",
        "## Executive summary",
        "",
        f"- Total open positions: {len(decisions)}",
        f"- diagnostics_only=true: {bool(decisions['diagnostics_only'].eq(True).all()) if not decisions.empty else True}",
        f"- execution remains unchanged: true",
        "",
        "## Action counts",
        "",
    ]
    for action in ACTION_VALUES:
        lines.append(f"- {action}: {int(counts.get(action, 0))}")
    lines.append("")
    if decisions.empty:
        lines.extend(["No open positions were available for diagnosis.", ""])
    else:
        lines.extend(_section("Top close candidates", decisions[decisions["recommended_action"].eq("close")]))
        lines.extend(_section("Top reduce candidates", decisions[decisions["recommended_action"].eq("reduce")]))
        lines.extend(_section("Top increase candidates", decisions[decisions["recommended_action"].eq("increase")]))
        lines.extend(_section("Positions blocked by guards", decisions[decisions["blocking_guard"].fillna("").ne("")]))
        lines.extend(_section("Positions with missing data", decisions[decisions["data_quality_status"].ne("ok")]))
        lines.extend(_section("Shorts requiring review", decisions[(decisions["side"].eq("short")) & (decisions["recommended_action"].eq("manual_review"))]))
        lines.extend(_section("Positions with open orders", decisions[decisions["open_order_status"].fillna("").ne("") | decisions["pending_action_id"].fillna("").ne("")]))
        lines.extend(_section("Positions with stale/unknown signals", decisions[decisions["model_signal_state"].isin(["stale", "unknown", "missing"])]))
        lines.extend(_section("Basket or sector concentration warnings", decisions[decisions["basket_risk_status"].isin(["elevated", "paused", "risk", "high", "breach"]) | decisions["sector_concentration_status"].isin(["elevated", "high", "breach"])]))
    lines.extend(["## Execution status", "", "- would_submit_order=false for every row.", "- execution_allowed=false for every row.", "- diagnostics_only=true for every row.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_position_management_decision_outputs(
    decisions: pd.DataFrame,
    *,
    root: Path | None = None,
    stamp: str | None = None,
) -> tuple[Path, Path]:
    output_stamp = stamp or timestamp()
    out_dir = _diagnostics_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"position_management_decisions_{output_stamp}.csv"
    md_path = out_dir / f"position_management_decisions_{output_stamp}.md"
    decisions.to_csv(csv_path, index=False)
    write_markdown_report(decisions, md_path)
    return csv_path, md_path


def run_position_management_decisions(*, root: Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    positions, holding, tracking, plan = latest_input_frames(root)
    decisions = build_position_management_decisions(
        positions,
        holding_review=holding,
        order_tracking=tracking,
        candidate_plan=plan,
        now=now,
    )
    csv_path, md_path = write_position_management_decision_outputs(decisions, root=root)
    counts = decisions["recommended_action"].value_counts().to_dict() if not decisions.empty else {}
    return {
        "status": "ok" if not positions.empty else "insufficient_data",
        "rows": int(len(decisions)),
        "csv_path": str(csv_path),
        "markdown_path": str(md_path),
        "action_counts": {action: int(counts.get(action, 0)) for action in ACTION_VALUES},
        "execution_unchanged": True,
    }
