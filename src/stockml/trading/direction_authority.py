from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from stockml.candidates.short_side_policy import ShortSidePolicy, load_short_side_policy, short_side_block_reason
from stockml.common.paths import PROJECT_ROOT


LONG = "LONG"
SHORT = "SHORT"
NONE = "NONE"

AUTHORITY_COLUMNS = [
    "direction_authority_source",
    "source_approved_direction",
    "planner_derived_direction",
    "final_proposed_side",
    "final_execution_side",
    "executable_direction_status",
    "direction_alignment_status",
    "direction_conflict",
    "direction_conflict_reason",
    "direction_memory_supports_side",
    "direction_memory_opposes_side",
    "direction_memory_status",
    "direction_resolution",
    "direction_resolution_reason",
    "raw_side_score",
    "calibrated_probability_win",
    "probability_calibration_status",
    "probability_usable_for_sizing",
]

NON_EXECUTABLE_STATUSES = {
    "source_approved_memory_conflict",
    "source_approved_memory_insufficient",
    "planner_only_not_executable",
    "no_decision_not_executable",
    "side_validation_failed",
    "blocked_by_risk_gate",
    "blocked_by_session_gate",
}


@dataclass(frozen=True)
class DirectionAuthorityConfig:
    execution_direction_source: str = "source_trade_action"
    allow_planner_derived_execution: bool = False
    require_source_approval: bool = True
    require_memory_alignment_for_full_approval: bool = True
    memory_insufficient_default: str = "watch"
    block_direction_memory_conflict: bool = True
    allow_inverse_execution: bool = False
    inverse_min_sample_count: int = 50
    block_uncalibrated_probability_for_sizing: bool = True
    block_short_side_until_validated: bool = True


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return default


def _int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


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


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


def _direction(value: Any) -> str:
    text = _text(value).strip().lower().replace("_", " ")
    if text in {"long", "buy"}:
        return LONG
    if text in {"short", "sell"}:
        return SHORT
    return NONE


def _append_reason(existing: Any, reason: str) -> str:
    text = _text(existing)
    parts = [part for part in text.replace(";", "|").split("|") if part and part.lower() not in {"nan", "none", "null"}]
    if reason and reason not in parts:
        parts.append(reason)
    return "|".join(parts)


def load_direction_authority_config(path: Path | str | None = None) -> DirectionAuthorityConfig:
    config_path = Path(path) if path else PROJECT_ROOT / "config" / "trading.yaml"
    payload: dict[str, Any] = {}
    if config_path.exists():
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            payload = {}
    data = payload.get("direction_authority", {}) if isinstance(payload, dict) else {}
    return DirectionAuthorityConfig(
        execution_direction_source=_text(data.get("execution_direction_source")) or "source_trade_action",
        allow_planner_derived_execution=_bool(data.get("allow_planner_derived_execution"), False),
        require_source_approval=_bool(data.get("require_source_approval"), True),
        require_memory_alignment_for_full_approval=_bool(data.get("require_memory_alignment_for_full_approval"), True),
        memory_insufficient_default=_text(data.get("memory_insufficient_default")) or "watch",
        block_direction_memory_conflict=_bool(data.get("block_direction_memory_conflict"), True),
        allow_inverse_execution=_bool(data.get("allow_inverse_execution"), False),
        inverse_min_sample_count=_int(data.get("inverse_min_sample_count"), 50),
        block_uncalibrated_probability_for_sizing=_bool(data.get("block_uncalibrated_probability_for_sizing"), True),
        block_short_side_until_validated=_bool(data.get("block_short_side_until_validated"), True),
    )


def _memory_status(row: Any) -> str:
    explicit = _text(row.get("ticker_direction_memory_status", "") if hasattr(row, "get") else "").lower()
    bias = _text(row.get("ticker_direction_bias", "") if hasattr(row, "get") else "").lower()
    if explicit in {"missing", "insufficient_samples", "insufficient_data", "available", "trust_long", "trust_short", "no_trade"}:
        return explicit
    if bias in {"trust_long", "trust_short", "no_trade"}:
        return "available"
    if bias in {"insufficient_data", ""}:
        return "insufficient_data"
    return explicit or "unknown"


def _memory_support(direction: str, bias: str) -> tuple[bool, bool]:
    clean = bias.lower()
    supports = (direction == LONG and clean == "trust_long") or (direction == SHORT and clean == "trust_short")
    opposes = (direction == LONG and clean == "trust_short") or (direction == SHORT and clean == "trust_long")
    return supports, opposes


def _probability_fields(row: Any) -> tuple[Any, Any, str]:
    raw = row.get("side_probability", "") if hasattr(row, "get") else ""
    calibrated = row.get("calibrated_probability_win", "") if hasattr(row, "get") else ""
    status = _text(row.get("probability_calibration_status", "") if hasattr(row, "get") else "").lower()
    if not status:
        status = "calibrated" if _num(calibrated) is not None else "uncalibrated"
    if status == "uncalibrated" and _num(calibrated) is None:
        calibrated = None
    return raw, calibrated, status


def _base_result(row: Any, source_direction: str, planner_direction: str, final_side: str, cfg: DirectionAuthorityConfig) -> dict[str, Any]:
    raw_side_score, calibrated_probability_win, probability_status = _probability_fields(row)
    probability_usable_for_sizing = probability_status == "calibrated" and _num(calibrated_probability_win) is not None
    bias = _text(row.get("ticker_direction_bias", "") if hasattr(row, "get") else "").lower()
    memory_supports, memory_opposes = _memory_support(source_direction, bias)
    return {
        "direction_authority_source": cfg.execution_direction_source,
        "source_approved_direction": source_direction,
        "planner_derived_direction": planner_direction,
        "final_proposed_side": final_side,
        "final_execution_side": NONE,
        "executable_direction_status": "",
        "direction_alignment_status": "",
        "direction_conflict": False,
        "direction_conflict_reason": "",
        "direction_memory_supports_side": memory_supports,
        "direction_memory_opposes_side": memory_opposes,
        "direction_memory_status": _memory_status(row),
        "direction_resolution": "",
        "direction_resolution_reason": "",
        "raw_side_score": raw_side_score,
        "calibrated_probability_win": calibrated_probability_win,
        "probability_calibration_status": probability_status,
        "probability_usable_for_sizing": probability_usable_for_sizing,
    }


def resolve_direction_authority(
    row: Any,
    *,
    config: DirectionAuthorityConfig | None = None,
    short_policy: ShortSidePolicy | None = None,
) -> dict[str, Any]:
    cfg = config or load_direction_authority_config()
    source = _direction(row.get("source_trade_action", "") if hasattr(row, "get") else "")
    trade = _direction(row.get("trade_action", "") if hasattr(row, "get") else "")
    directional = _direction(row.get("directional_action", "") if hasattr(row, "get") else "")
    planner = trade if trade != NONE else directional
    final_side = source if source != NONE else NONE
    result = _base_result(row, source, planner, final_side, cfg)

    if source == NONE:
        result["final_proposed_side"] = NONE
        if planner != NONE:
            result.update(
                executable_direction_status="planner_only_not_executable",
                direction_alignment_status="planner_only",
                direction_resolution="research_only",
                direction_resolution_reason="planner_derived_action_without_source_approval",
            )
        else:
            result.update(
                executable_direction_status="no_decision_not_executable",
                direction_alignment_status="no_source_direction",
                direction_resolution="blocked",
                direction_resolution_reason="source_trade_action_not_executable",
            )
        return result

    if trade != NONE and trade != source:
        result.update(
            executable_direction_status="source_approved_memory_conflict",
            direction_alignment_status="conflict",
            direction_conflict=True,
            direction_conflict_reason="trade_action_conflicts_with_source_trade_action",
            direction_resolution="blocked",
            direction_resolution_reason="direction_memory_conflict",
        )
        return result

    if directional != NONE and directional != source:
        result.update(
            executable_direction_status="source_approved_memory_conflict",
            direction_alignment_status="conflict",
            direction_conflict=True,
            direction_conflict_reason="directional_action_conflicts_with_source_trade_action",
            direction_resolution="blocked",
            direction_resolution_reason="direction_memory_conflict",
        )
        return result

    bias = _text(row.get("ticker_direction_bias", "") if hasattr(row, "get") else "").lower()
    memory_supports, memory_opposes = _memory_support(source, bias)
    if memory_opposes and cfg.block_direction_memory_conflict:
        result.update(
            final_proposed_side=NONE,
            executable_direction_status="source_approved_memory_conflict",
            direction_alignment_status="memory_conflict",
            direction_conflict=True,
            direction_conflict_reason="direction_memory_conflict",
            direction_resolution="blocked",
            direction_resolution_reason="direction_memory_conflict",
        )
        return result

    if cfg.require_memory_alignment_for_full_approval and not memory_supports:
        status = "source_approved_memory_insufficient"
        reason = "direction_memory_insufficient"
        if bias == "no_trade":
            status = "source_approved_memory_conflict"
            reason = "direction_memory_conflict"
        result.update(
            final_proposed_side=NONE if cfg.memory_insufficient_default.lower() == "block" or bias == "no_trade" else source,
            executable_direction_status=status,
            direction_alignment_status="memory_insufficient",
            direction_resolution="blocked" if cfg.memory_insufficient_default.lower() == "block" or bias == "no_trade" else "watch",
            direction_resolution_reason=reason,
        )
        return result

    validated_bps = _num(row.get("validated_expected_return_bps", "") if hasattr(row, "get") else "")
    if validated_bps is not None and validated_bps <= 0:
        result.update(
            executable_direction_status="side_validation_failed",
            direction_alignment_status="aligned",
            direction_resolution="blocked",
            direction_resolution_reason="short_side_validation_required" if source == SHORT else "side_validation_failed",
        )
        return result

    if source == SHORT and cfg.block_short_side_until_validated:
        reason = short_side_block_reason(row, short_policy or load_short_side_policy())
        if reason:
            result.update(
                executable_direction_status="side_validation_failed",
                direction_alignment_status="aligned",
                direction_resolution="blocked",
                direction_resolution_reason=reason,
            )
            return result

    result.update(
        executable_direction_status="source_approved_memory_aligned",
        direction_alignment_status="aligned",
        direction_resolution="executable_direction",
        direction_resolution_reason="source_trade_action_memory_aligned",
        final_execution_side=source,
    )
    return result


def apply_authority_to_row(row: pd.Series, *, config: DirectionAuthorityConfig | None = None) -> pd.Series:
    out = row.copy()
    resolution = resolve_direction_authority(out, config=config)
    for key, value in resolution.items():
        out[key] = value
    existing_status = _text(out.get("trade_quality_status", "")).lower()
    order_eligible = out.get("order_eligible", True)
    try:
        order_eligible = bool(order_eligible) and not pd.isna(order_eligible)
    except Exception:
        order_eligible = bool(order_eligible)
    if existing_status == "rejected" or not order_eligible:
        out["final_execution_side"] = NONE
    reason = str(resolution.get("direction_resolution_reason") or "")
    status = str(resolution.get("executable_direction_status") or "")
    if status in NON_EXECUTABLE_STATUSES or resolution.get("direction_resolution") in {"blocked", "research_only", "watch"}:
        out["trade_quality_status"] = "rejected"
        out["candidate_status"] = "research_only" if resolution.get("direction_resolution") in {"research_only", "watch"} else "rejected"
        out["order_eligible"] = False
        out["approved_notional"] = 0.0
        out["notional"] = 0.0
        out["suggested_quantity"] = 0
        if resolution.get("direction_resolution") in {"research_only", "watch"}:
            out["research_only"] = True
        if reason:
            out["trade_quality_reason"] = _append_reason(out.get("trade_quality_reason", ""), reason)
            out["primary_block_reason"] = _text(out.get("primary_block_reason")) or reason
    return out
