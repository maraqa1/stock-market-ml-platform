from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from stockml.common.paths import PROJECT_ROOT
from stockml.trading.ticker_direction_memory import (
    BIAS_INVERSE_WATCH,
    BIAS_NO_TRADE,
    BIAS_TRUST_ORIGINAL,
)


PASS = "direction_pass"
BLOCK = "direction_block"
RESEARCH_ONLY = "direction_research_only"
INVERSE_WATCH = "direction_inverse_watch"
MANUAL_REVIEW = "direction_manual_review"
INSUFFICIENT_DATA = "direction_insufficient_data"

SOURCE_LONG = {"long", "buy"}
SOURCE_SHORT = {"short", "sell"}
NO_DECISION = {"no decision", "no_decision", "none", "null", "nan", ""}


@dataclass(frozen=True)
class DirectionGateConfig:
    enabled: bool = True
    require_source_trade_action: bool = True
    allow_directional_action_execution: bool = False
    allow_planner_derived_no_decision_execution: bool = False
    require_positive_validated_expected_return: bool = True
    allow_missing_validated_return: bool = False
    min_validated_profit_factor: float = 1.05
    require_meta_label_acceptance: bool = False
    block_negative_expected_return: bool = True
    inverse_warning_blocks_execution: bool = True
    shorts_require_short_policy_pass: bool = True


@dataclass(frozen=True)
class ShortDirectionGateConfig:
    enabled: bool = True
    min_short_win_rate: float = 0.45
    min_short_profit_factor: float = 1.10
    require_positive_short_expected_return: bool = True
    block_high_squeeze_risk: bool = True
    default_short_decision: str = "research_only"


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


def _float(value: Any, default: float) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
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


def _clean_action(value: Any) -> str:
    return _text(value).strip().lower().replace("_", " ")


def _num(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        parsed = float(value)
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


def load_direction_gate_config(path: Path | str | None = None) -> DirectionGateConfig:
    config_path = Path(path) if path else PROJECT_ROOT / "config" / "trading.yaml"
    payload: dict[str, Any] = {}
    if config_path.exists():
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            payload = {}
    data = payload.get("direction_gate", {}) if isinstance(payload, dict) else {}
    return DirectionGateConfig(
        enabled=_bool(data.get("enabled"), True),
        require_source_trade_action=_bool(data.get("require_source_trade_action"), True),
        allow_directional_action_execution=_bool(data.get("allow_directional_action_execution"), False),
        allow_planner_derived_no_decision_execution=_bool(data.get("allow_planner_derived_no_decision_execution"), False),
        require_positive_validated_expected_return=_bool(data.get("require_positive_validated_expected_return"), True),
        min_validated_profit_factor=_float(data.get("min_validated_profit_factor"), 1.05),
        require_meta_label_acceptance=_bool(data.get("require_meta_label_acceptance"), False),
        block_negative_expected_return=_bool(data.get("block_negative_expected_return"), True),
        inverse_warning_blocks_execution=_bool(data.get("inverse_warning_blocks_execution"), True),
        shorts_require_short_policy_pass=_bool(data.get("shorts_require_short_policy_pass"), True),
    )


def load_short_direction_gate_config(path: Path | str | None = None) -> ShortDirectionGateConfig:
    config_path = Path(path) if path else PROJECT_ROOT / "config" / "trading.yaml"
    payload: dict[str, Any] = {}
    if config_path.exists():
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            payload = {}
    data = payload.get("short_direction_gate", {}) if isinstance(payload, dict) else {}
    return ShortDirectionGateConfig(
        enabled=_bool(data.get("enabled"), True),
        min_short_win_rate=_float(data.get("min_short_win_rate"), 0.45),
        min_short_profit_factor=_float(data.get("min_short_profit_factor"), 1.10),
        require_positive_short_expected_return=_bool(data.get("require_positive_short_expected_return"), True),
        block_high_squeeze_risk=_bool(data.get("block_high_squeeze_risk"), True),
        default_short_decision=_text(data.get("default_short_decision")) or "research_only",
    )


def _proposed_side(row: Any) -> str:
    side = _clean_action(row.get("side", "") if hasattr(row, "get") else "")
    if side in {"buy", "long"}:
        return "long"
    if side in {"sell", "short"}:
        return "short"
    action = _clean_action(row.get("trade_action", "") if hasattr(row, "get") else "")
    if action in SOURCE_LONG:
        return "long"
    if action in SOURCE_SHORT:
        return "short"
    return ""


def _status_for(decision: str) -> str:
    return {
        PASS: "pass",
        BLOCK: "block",
        RESEARCH_ONLY: "research_only",
        INVERSE_WATCH: "inverse_watch",
        MANUAL_REVIEW: "manual_review",
        INSUFFICIENT_DATA: "insufficient_data",
    }.get(decision, "manual_review")


def _result(
    *,
    decision: str,
    reason: str,
    supporting: list[str] | None = None,
    blocking: list[str] | None = None,
    confidence: float = 0.0,
    source: str = "source_trade_action",
    quality: str = "untrusted",
    inverse_warning: bool = False,
) -> dict[str, Any]:
    return {
        "direction_gate_status": _status_for(decision),
        "direction_gate_pass": decision == PASS,
        "direction_confidence": round(float(confidence), 4),
        "direction_decision": decision,
        "direction_primary_reason": reason,
        "direction_supporting_reasons": "|".join(supporting or []),
        "direction_blocking_reasons": "|".join(blocking or ([reason] if reason else [])),
        "direction_source": source,
        "direction_quality": quality,
        "direction_inverse_warning": bool(inverse_warning),
        "direction_research_only": decision == RESEARCH_ONLY,
        "diagnostics_only": False,
    }


def evaluate_direction_gate(
    row: Any,
    *,
    config: DirectionGateConfig | None = None,
    short_config: ShortDirectionGateConfig | None = None,
) -> dict[str, Any]:
    """Evaluate whether the candidate's proposed direction is executable.

    This is a pure gate. It never submits orders, alters sizing, or flips a
    trade. Failing candidates can still be retained for research diagnostics.
    """

    cfg = config or load_direction_gate_config()
    short_cfg = short_config or load_short_direction_gate_config()
    if not cfg.enabled:
        return _result(decision=PASS, reason="direction_gate_disabled", supporting=["gate_disabled"], confidence=0.0, quality="disabled")

    proposed = _proposed_side(row)
    source = _clean_action(row.get("source_trade_action", "") if hasattr(row, "get") else "")
    trade = _clean_action(row.get("trade_action", "") if hasattr(row, "get") else "")
    directional = _clean_action(row.get("directional_action", "") if hasattr(row, "get") else "")
    validated_bps = _num(row.get("validated_expected_return_bps", None) if hasattr(row, "get") else None)
    profit_factor = _num(row.get("validated_profit_factor", None) if hasattr(row, "get") else None)
    hit_rate = _num(row.get("validated_hit_rate", None) if hasattr(row, "get") else None)
    quality = _clean_action(row.get("expected_return_quality", "") if hasattr(row, "get") else "")
    calibration = _clean_action(row.get("calibration_quality", "") if hasattr(row, "get") else "")
    meta_decision = _clean_action(row.get("meta_label_decision", "") if hasattr(row, "get") else "")
    short_policy = _clean_action(row.get("short_policy_status", "") if hasattr(row, "get") else "")
    short_validation = _clean_action(row.get("short_side_validation_status", "") if hasattr(row, "get") else "")
    inverse_flag = _bool(row.get("inverse_watch_flag", False) if hasattr(row, "get") else False, False)
    inverse_return = _num(row.get("inverse_return_bps", None) if hasattr(row, "get") else None)
    intraday_state = _clean_action(row.get("intraday_momentum_state", "") if hasattr(row, "get") else "")
    ticker_bias = _text(row.get("ticker_direction_bias", "") if hasattr(row, "get") else "").strip().lower()
    ticker_confidence = _num(row.get("ticker_direction_confidence", None) if hasattr(row, "get") else None)
    ticker_samples = _num(row.get("ticker_direction_sample_count", None) if hasattr(row, "get") else None)
    ticker_reason = _clean_action(row.get("ticker_direction_reason", "") if hasattr(row, "get") else "")

    if cfg.require_source_trade_action and not source:
        if trade in SOURCE_LONG | SOURCE_SHORT and cfg.allow_planner_derived_no_decision_execution:
            source = trade  # fall through: treat missing source same as no-decision when flag allows it
        else:
            return _result(decision=MANUAL_REVIEW, reason="missing_source_trade_action", blocking=["missing_source_trade_action"], source="missing", quality="missing")

    if source in {"no decision", "no decision"} or source.replace(" ", "_") in {"no_decision"} or source in NO_DECISION:
        if trade in SOURCE_LONG | SOURCE_SHORT and cfg.allow_planner_derived_no_decision_execution:
            source = trade
        elif trade in SOURCE_LONG | SOURCE_SHORT:
            reason = "planner_derived_action_without_source_approval"
            return _result(decision=RESEARCH_ONLY, reason=reason, blocking=[reason, "source_trade_action_not_executable"], source="source_trade_action", quality="research_only")
        elif directional in SOURCE_LONG | SOURCE_SHORT:
            reason = "directional_action_research_only"
            return _result(decision=RESEARCH_ONLY, reason=reason, blocking=[reason, "source_trade_action_not_executable"], source="source_trade_action", quality="research_only")
        else:
            reason = "source_trade_action_not_executable"
            return _result(decision=RESEARCH_ONLY, reason=reason, blocking=[reason, "source_trade_action_not_executable"], source="source_trade_action", quality="research_only")

    source_side = "long" if source in SOURCE_LONG else ("short" if source in SOURCE_SHORT else "")
    trade_side = "long" if trade in SOURCE_LONG else ("short" if trade in SOURCE_SHORT else "")
    directional_side = "long" if directional in SOURCE_LONG else ("short" if directional in SOURCE_SHORT else "")
    conflicts = {side for side in [source_side, trade_side, directional_side] if side}
    if len(conflicts) > 1:
        return _result(decision=MANUAL_REVIEW, reason="conflicting_direction_signals", blocking=["conflicting_direction_signals"], source="mixed", quality="conflict")

    if not proposed:
        return _result(decision=INSUFFICIENT_DATA, reason="missing_proposed_side", blocking=["missing_proposed_side"], source="side", quality="missing")

    if proposed != source_side:
        if source_side:
            return _result(decision=BLOCK, reason="source_trade_action_side_mismatch", blocking=["source_trade_action_side_mismatch"], source="source_trade_action", quality="blocked")
        return _result(decision=MANUAL_REVIEW, reason="source_trade_action_not_executable", blocking=["source_trade_action_not_executable"], source="source_trade_action", quality="manual_review")

    if cfg.inverse_warning_blocks_execution and (inverse_flag or (inverse_return is not None and inverse_return > 0 and validated_bps is not None and inverse_return > validated_bps)):
        return _result(
            decision=INVERSE_WATCH,
            reason="inverse_side_outperforms",
            blocking=["inverse_side_outperforms"],
            source="inverse_diagnostics",
            quality="inverse_warning",
            inverse_warning=True,
        )

    if ticker_bias == BIAS_INVERSE_WATCH:
        return _result(
            decision=INVERSE_WATCH,
            reason=ticker_reason or "ticker_direction_memory_prefers_inverse",
            blocking=["ticker_direction_memory_prefers_inverse"],
            confidence=ticker_confidence or 0.0,
            source="ticker_direction_memory",
            quality="inverse_warning",
            inverse_warning=True,
        )
    if ticker_bias == BIAS_NO_TRADE:
        return _result(
            decision=BLOCK,
            reason=ticker_reason or "ticker_direction_memory_blocks_ticker",
            blocking=["ticker_direction_memory_blocks_ticker"],
            confidence=ticker_confidence or 0.0,
            source="ticker_direction_memory",
            quality="blocked",
        )

    if validated_bps is None:
        if not cfg.allow_missing_validated_return:
            return _result(decision=INSUFFICIENT_DATA, reason="missing_validated_expected_return", blocking=["missing_validated_expected_return"], source="validated_expected_return_bps", quality="missing")
    if cfg.require_positive_validated_expected_return and validated_bps is not None and validated_bps <= 0:
        reasons = ["negative_validated_expected_return"]
        if proposed == "short":
            reasons.append("short_negative_edge")
        return _result(decision=BLOCK, reason="negative_validated_expected_return", blocking=reasons, source="validated_expected_return_bps", quality="blocked")

    if profit_factor is None:
        if not cfg.allow_missing_validated_return:
            return _result(decision=INSUFFICIENT_DATA, reason="missing_validated_profit_factor", blocking=["missing_validated_profit_factor"], source="validated_profit_factor", quality="missing")
    min_pf = short_cfg.min_short_profit_factor if proposed == "short" else cfg.min_validated_profit_factor
    if profit_factor is not None and profit_factor < min_pf:
        return _result(decision=BLOCK, reason="validated_profit_factor_below_one", blocking=["validated_profit_factor_below_one"], source="validated_profit_factor", quality="blocked")

    if quality and quality not in {"usable", "accepted", "calibrated", "weak allowed by config", "weak_allowed_by_config"}:
        return _result(decision=BLOCK, reason="expected_return_quality_not_usable", blocking=["expected_return_quality_not_usable"], source="expected_return_quality", quality="blocked")
    if calibration and calibration not in {"usable", "accepted", "calibrated"}:
        return _result(decision=BLOCK, reason="calibration_quality_not_usable", blocking=["calibration_quality_not_usable"], source="calibration_quality", quality="blocked")

    if cfg.require_meta_label_acceptance and meta_decision and meta_decision not in {"accepted", "pass", "approved"}:
        return _result(decision=BLOCK, reason="meta_label_not_accepted", blocking=["meta_label_not_accepted"], source="meta_label_decision", quality="blocked")

    if proposed == "long" and intraday_state in {"strongly bearish", "bearish breakdown", "strong_bearish", "bearish_breakdown"}:
        return _result(decision=MANUAL_REVIEW, reason="intraday_direction_conflict", blocking=["intraday_direction_conflict"], source="intraday_momentum_state", quality="manual_review")
    if proposed == "short":
        if short_cfg.default_short_decision == "research_only":
            if short_policy not in {"enabled", "pass", "passed"} or short_validation not in {"pass", "passed", "accepted"}:
                return _result(decision=RESEARCH_ONLY, reason="short_side_validation_required", blocking=["short_side_validation_required"], source="short_direction_gate", quality="research_only")
        if hit_rate is not None and hit_rate < short_cfg.min_short_win_rate:
            return _result(decision=RESEARCH_ONLY, reason="short_side_validation_required", blocking=["short_side_validation_required", "short_hit_rate_below_threshold"], source="short_direction_gate", quality="research_only")
        if intraday_state in {"strongly bullish", "bullish breakout", "strong_bullish", "bullish_breakout"}:
            return _result(decision=INVERSE_WATCH, reason="short_against_strong_momentum", blocking=["short_against_strong_momentum"], source="intraday_momentum_state", quality="inverse_warning", inverse_warning=True)

    supporting = [
        "source_trade_action_authoritative",
        "positive_validated_expected_return",
        "validated_profit_factor_pass",
    ]
    confidence = 0.55
    if hit_rate is not None:
        confidence += max(0.0, min(0.2, hit_rate - 0.45))
    confidence += min(0.2, max(0.0, (profit_factor - 1.0) / 5.0))
    source_name = "source_trade_action"
    quality_name = "trusted"
    if ticker_bias == BIAS_TRUST_ORIGINAL:
        supporting.append("ticker_direction_memory_supports_original")
        source_name = "source_trade_action+ticker_direction_memory"
        quality_name = "ticker_supported"
        if ticker_confidence is not None:
            confidence = max(confidence, min(0.95, ticker_confidence))
    if ticker_samples is not None and ticker_samples <= 0:
        supporting.append("ticker_direction_memory_missing")
    return _result(
        decision=PASS,
        reason="direction_supported",
        supporting=supporting,
        blocking=[],
        confidence=min(confidence, 0.95),
        source=source_name,
        quality=quality_name,
    )
