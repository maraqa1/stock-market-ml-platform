from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from stockml.candidates.short_side_policy import ShortSidePolicy, load_short_side_policy, short_side_block_reason
from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.trading.direction_gate import evaluate_direction_gate
from stockml.trading.direction_authority import AUTHORITY_COLUMNS, resolve_direction_authority
from stockml.trading.ticker_direction_memory import load_ticker_direction_memory_config


OUTPUT_COLUMNS = [
    "raw_rank",
    "model_rank",
    "research_rank",
    "execution_rank",
    "symbol",
    "side",
    "status",
    "executable",
    "research_only",
    "execution_domain",
    "execution_eligible",
    "trade_authority_status",
    "shadow_reason",
    "all_block_reasons",
    "primary_block_reason",
    "risk_tier",
    "volatility_tier",
    "validation_quality",
    "calibration_quality",
    "validated_expected_return_bps",
    "validated_hit_rate",
    "validated_profit_factor",
    "direction_gate_status",
    "direction_gate_pass",
    "direction_decision",
    "direction_confidence",
    "direction_primary_reason",
    "direction_blocking_reasons",
    "direction_supporting_reasons",
    "ticker_direction_bias",
    "ticker_direction_confidence",
    "ticker_direction_sample_count",
    "ticker_inverse_advantage_bps",
    "ticker_direction_reason",
    "expected_return_scope",
    "hit_rate_scope",
    "profit_factor_scope",
    "ticker_direction_memory_status",
    "inverse_warning_status",
    "inverse_warning_actionable",
    *AUTHORITY_COLUMNS,
    "confidence_bucket",
]
SAFE_EMPTY_REASONS = {"", "nan", "none", "null"}
EXECUTION_CANDIDATE = "execution_candidate"
WATCH_CANDIDATE = "watch_candidate"
BLOCKED_CANDIDATE = "blocked_candidate"
SHADOW_OBSERVATION = "shadow_observation"


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


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


def _symbol(row: pd.Series) -> str:
    return (_text(row.get("symbol")) or _text(row.get("ticker"))).upper()


def _side(row: pd.Series) -> str:
    side = _text(row.get("side")).lower()
    action = _text(row.get("trade_action")).lower()
    if side in {"buy", "long"} or action == "long":
        return "buy"
    if side in {"sell", "short"} or action == "short":
        return "sell"
    return side or action


def _raw_rank(frame: pd.DataFrame) -> pd.Series:
    for column in ["raw_rank", "candidate_rank", "rank_overall", "research_rank"]:
        if column in frame.columns:
            return pd.to_numeric(frame[column], errors="coerce")
    return pd.Series(range(1, len(frame) + 1), index=frame.index, dtype="float64")


def _model_rank(frame: pd.DataFrame) -> pd.Series:
    for column in ["model_rank", "rank_overall", "candidate_rank"]:
        if column in frame.columns:
            return pd.to_numeric(frame[column], errors="coerce")
    return _raw_rank(frame)


def _split_reasons(value: Any) -> list[str]:
    text = _text(value)
    if not text or text.lower() == "approved":
        return []
    return [part.strip() for part in text.replace(";", "|").split("|") if part.strip()]


def _append_reason(reasons: list[str], reason: str) -> list[str]:
    if reason and reason not in reasons:
        reasons.append(reason)
    return reasons


def _prepend_reason(reasons: list[str], reason: str) -> list[str]:
    if not reason:
        return reasons
    return [reason, *[existing for existing in reasons if existing != reason]]


def _reduced_reason(row: pd.Series) -> str:
    risk_tier = _text(row.get("risk_tier")).lower()
    volatility_tier = _text(row.get("volatility_tier")).lower()
    approved_notional = _num(row.get("approved_notional")) or _num(row.get("notional")) or 0.0
    suggested_quantity = int(_num(row.get("suggested_quantity")) or 0)
    if volatility_tier in {"high", "extreme", "speculative"}:
        return "reduced_due_to_volatility"
    if risk_tier and risk_tier not in {"high_quality", "quality", "approved"}:
        return "reduced_due_to_risk_tier"
    if approved_notional <= 0 or suggested_quantity <= 0:
        return "reduced_due_to_low_notional"
    return "reduced_due_to_position_sizing"


def _normalise_reasons(row: pd.Series, reasons: list[str]) -> list[str]:
    out: list[str] = []
    for reason in reasons:
        clean = _text(reason)
        if clean.lower() == "reduced":
            clean = _reduced_reason(row)
        if clean and clean not in out:
            out.append(clean)
    return out


SHORT_REASON_PRECEDENCE = [
    "short_side_validation_required",
    "negative_validated_expected_return",
    "direction_memory_conflict",
    "direction_memory_insufficient",
    "risk_gate_failed",
    "reduced_due_to_risk_tier",
]


def _is_source_short(row: pd.Series) -> bool:
    return _text(row.get("source_trade_action")).lower() == "short"


def _source_short_reasons(row: pd.Series, reasons: list[str]) -> list[str]:
    if not _is_source_short(row):
        return reasons
    out = list(reasons)
    validated_bps = _num(row.get("validated_expected_return_bps"))
    if validated_bps is not None and validated_bps <= 0:
        _append_reason(out, "negative_validated_expected_return")
        _append_reason(out, "short_side_validation_required")
    if _text(row.get("ticker_direction_bias")).lower() in {"", "insufficient_data"}:
        _append_reason(out, "direction_memory_insufficient")
    return out


def _ordered_primary_reason(row: pd.Series, reasons: list[str]) -> str:
    if _is_source_short(row):
        reason_set = set(reasons)
        for reason in SHORT_REASON_PRECEDENCE:
            if reason in reason_set:
                return reason
    return reasons[0] if reasons else ""


def _validation_quality(row: pd.Series) -> str:
    return _text(row.get("validation_quality")) or _text(row.get("calibration_quality"))


def _confidence_bucket(row: pd.Series, probability_status: str) -> Any:
    bucket = row.get("confidence_bucket", "")
    if _text(probability_status).lower() == "uncalibrated" and _text(bucket).upper() == "HIGH":
        return "UNCALIBRATED"
    return bucket


def _source_action_reason(row: pd.Series) -> str:
    action = _text(row.get("source_trade_action")).lower()
    if action in {"long", "short"}:
        return ""
    return "source_trade_action_not_executable"


def _source_direction_text(row: pd.Series) -> str:
    return _text(row.get("source_trade_action")).strip().lower().replace("_", " ")


def _has_source_direction(row: pd.Series) -> bool:
    return _source_direction_text(row) in {"long", "short"}


def _shadow_reason(row: pd.Series, authority: dict[str, Any]) -> str:
    if _has_source_direction(row):
        return ""
    reason = _text(authority.get("direction_resolution_reason"))
    if reason:
        return reason
    planner = _text(authority.get("planner_derived_direction"))
    if planner in {"LONG", "SHORT"}:
        return "planner_derived_action_without_source_approval"
    return "source_trade_action_not_executable"


def _execution_domain(row: pd.Series, *, status: str, executable: bool, authority: dict[str, Any]) -> tuple[str, bool, str]:
    if not _has_source_direction(row):
        return SHADOW_OBSERVATION, False, "shadow"
    if executable:
        return EXECUTION_CANDIDATE, True, "authorized"
    if status == "watch" or authority.get("direction_resolution") == "watch":
        return WATCH_CANDIDATE, False, "watch"
    return BLOCKED_CANDIDATE, False, "blocked"


def _no_decision_reason(row: pd.Series) -> str:
    action = (_text(row.get("trade_action")) or _text(row.get("source_trade_action"))).lower()
    if action in {"no decision", "no_decision", "none", ""}:
        return "no_decision"
    explicit = _text(row.get("no_decision_reason"))
    if explicit and explicit.lower() not in {"nan", "none", "not provided"}:
        return "no_decision"
    return ""


def _calibration_reason(row: pd.Series) -> str:
    quality = _text(row.get("expected_return_quality")).lower()
    calibration = _text(row.get("calibration_quality")).lower()
    if quality in {"usable", "calibrated", "weak_allowed_by_config"} or calibration == "usable":
        return ""
    if quality or calibration:
        return "expected_return_uncalibrated"
    if "expected_return_uncalibrated" in _text(row.get("trade_quality_reason")):
        return "expected_return_uncalibrated"
    if "Expected return uncalibrated" in _text(row.get("message")):
        return "expected_return_uncalibrated"
    return ""


def _status(row: pd.Series, reasons: list[str], *, research_only: bool) -> str:
    if research_only:
        return "research_only"
    current = _text(row.get("trade_quality_status")).lower() or _text(row.get("candidate_status")).lower()
    notional = _num(row.get("approved_notional")) or _num(row.get("notional")) or 0.0
    quantity = int(_num(row.get("suggested_quantity")) or 0)
    if current in {"approved", "reduced"} and not reasons and notional > 0 and quantity > 0:
        return "executable"
    if not reasons and notional > 0 and quantity > 0:
        return "executable"
    return "blocked"


def _metric_scope(row: pd.Series, column: str) -> str:
    explicit = _text(row.get(f"{column}_scope"))
    return explicit or "unknown"


def _ticker_memory_status(row: pd.Series, min_samples: int) -> str:
    sample_count = int(_num(row.get("ticker_direction_sample_count")) or 0)
    bias = _text(row.get("ticker_direction_bias")).lower()
    reason = _text(row.get("ticker_direction_reason")).lower()
    if sample_count < min_samples:
        if sample_count > 0 or bias or reason:
            return "insufficient_samples"
        return "missing"
    if bias in {"inverse_watch", "trust_original", "no_trade"}:
        return bias
    return "available"


def _infer_metric_scope(frame: pd.DataFrame, metric_column: str, output_column: str) -> pd.DataFrame:
    if metric_column not in frame.columns or frame.empty:
        frame[output_column] = "unknown"
        return frame
    values = pd.to_numeric(frame[metric_column], errors="coerce")
    scope = pd.Series("unknown", index=frame.index, dtype="object")
    rounded = values.round(8)
    if rounded.nunique(dropna=True) == 1 and values.notna().any():
        scope.loc[values.notna()] = "global"
    if "side" in frame.columns:
        for _, indexes in frame.groupby(frame["side"].fillna("").astype(str).str.lower(), dropna=False).groups.items():
            side_values = rounded.loc[indexes].dropna()
            if len(side_values) > 1 and side_values.nunique(dropna=True) == 1:
                scope.loc[list(indexes)] = "side"
    if output_column not in frame.columns:
        frame[output_column] = scope
        return frame
    current = frame[output_column].fillna("").astype(str).str.lower()
    inferred = scope.fillna("").astype(str).str.lower()
    inferred_specific = inferred.isin(["side", "global", "bucket"])
    frame.loc[inferred_specific, output_column] = scope.loc[inferred_specific]
    missing = current.isin(["", "nan", "none", "null", "unknown"])
    frame.loc[missing, output_column] = scope.loc[missing]
    return frame


def build_execution_ranked_candidates(
    candidates: pd.DataFrame,
    *,
    short_policy: ShortSidePolicy | None = None,
) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    policy = short_policy or load_short_side_policy()
    memory_cfg = load_ticker_direction_memory_config()
    min_ticker_samples = int(memory_cfg.min_ticker_samples or 20)
    frame = candidates.copy()
    frame["raw_rank"] = _raw_rank(frame)
    frame["model_rank"] = _model_rank(frame)
    frame["research_rank"] = frame["raw_rank"]
    rows: list[dict[str, Any]] = []
    for idx, row in frame.iterrows():
        reasons = _split_reasons(row.get("trade_quality_reason"))
        if not reasons:
            reasons = _split_reasons(row.get("message"))
        for reason in [_source_action_reason(row), _no_decision_reason(row), _calibration_reason(row)]:
            _append_reason(reasons, reason)
        short_reason = short_side_block_reason(row, policy)
        research_only = False
        if short_reason:
            _append_reason(reasons, short_reason)
        authority = resolve_direction_authority(row, short_policy=policy)
        authority_status = str(authority.get("executable_direction_status") or "")
        authority_reason = str(authority.get("direction_resolution_reason") or "")
        if authority_status != "source_approved_memory_aligned":
            if authority_status == "planner_only_not_executable":
                reasons = _prepend_reason(reasons, authority_reason or "planner_derived_action_without_source_approval")
            else:
                _append_reason(reasons, authority_reason or authority_status or "direction_authority_failed")
            if authority.get("direction_resolution") in {"research_only", "watch"} and not _is_source_short(row):
                research_only = True
        direction_row = row.copy()
        if policy.enabled and policy.allow_shorts_in_validation:
            direction_row["short_policy_status"] = direction_row.get("short_policy_status") or "enabled"
            direction_row["short_side_validation_status"] = direction_row.get("short_side_validation_status") or "pass"
        direction = evaluate_direction_gate(direction_row)
        sample_count = int(_num(row.get("ticker_direction_sample_count")) or 0)
        inverse_warning = bool(direction.get("direction_inverse_warning")) or _num(row.get("ticker_inverse_advantage_bps")) not in [None, 0]
        inverse_actionable = bool(inverse_warning and sample_count >= min_ticker_samples)
        inverse_status = "none"
        if inverse_warning:
            inverse_status = "present_sufficient_samples" if inverse_actionable else "present_insufficient_samples"
        if not bool(direction.get("direction_gate_pass")):
            _append_reason(reasons, str(direction.get("direction_primary_reason") or "direction_gate_failed"))
            if (
                direction.get("direction_decision") == "direction_research_only"
                and authority.get("direction_resolution") != "blocked"
                and not _is_source_short(row)
            ):
                research_only = True
        reasons = _normalise_reasons(row, _source_short_reasons(row, reasons))
        status = _status(row, reasons, research_only=research_only)
        if _is_source_short(row) and status == "research_only":
            status = "watch" if authority.get("direction_resolution") == "watch" else "blocked"
            research_only = False
        if _is_source_short(row) and authority.get("direction_resolution") == "watch":
            status = "watch"
            research_only = False
        executable = (
            status == "executable"
            and authority_status == "source_approved_memory_aligned"
            and bool(direction.get("direction_gate_pass"))
            and direction.get("direction_decision") == "direction_pass"
        )
        if not executable and status == "executable":
            status = "blocked"
        authority = dict(authority)
        if not executable:
            authority["final_execution_side"] = "NONE"
        primary_reason = _ordered_primary_reason(row, reasons)
        if status == "blocked" and not primary_reason:
            primary_reason = "unknown_rejection_reason"
            reasons = [primary_reason]
        execution_domain, execution_eligible, trade_authority_status = _execution_domain(
            row,
            status=status,
            executable=executable,
            authority=authority,
        )
        shadow_reason = _shadow_reason(row, authority) if execution_domain == SHADOW_OBSERVATION else ""
        rows.append(
            {
                "__index": idx,
                "raw_rank": row.get("raw_rank"),
                "model_rank": row.get("model_rank"),
                "research_rank": row.get("research_rank"),
                "execution_rank": pd.NA,
                "symbol": _symbol(row),
                "side": _side(row),
                "status": status,
                "executable": executable,
                "research_only": research_only,
                "execution_domain": execution_domain,
                "execution_eligible": execution_eligible,
                "trade_authority_status": trade_authority_status,
                "shadow_reason": shadow_reason,
                "all_block_reasons": "|".join(reasons),
                "primary_block_reason": primary_reason,
                "risk_tier": row.get("risk_tier", ""),
                "volatility_tier": row.get("volatility_tier", ""),
                "validation_quality": _validation_quality(row),
                "calibration_quality": row.get("calibration_quality", ""),
                "validated_expected_return_bps": row.get("validated_expected_return_bps", ""),
                "validated_hit_rate": row.get("validated_hit_rate", ""),
                "validated_profit_factor": row.get("validated_profit_factor", ""),
                "direction_gate_status": direction.get("direction_gate_status", ""),
                "direction_gate_pass": direction.get("direction_gate_pass", False),
                "direction_decision": direction.get("direction_decision", ""),
                "direction_confidence": direction.get("direction_confidence", 0.0),
                "direction_primary_reason": direction.get("direction_primary_reason", ""),
                "direction_blocking_reasons": direction.get("direction_blocking_reasons", ""),
                "direction_supporting_reasons": direction.get("direction_supporting_reasons", ""),
                "ticker_direction_bias": row.get("ticker_direction_bias", ""),
                "ticker_direction_confidence": row.get("ticker_direction_confidence", ""),
                "ticker_direction_sample_count": row.get("ticker_direction_sample_count", ""),
                "ticker_inverse_advantage_bps": row.get("ticker_inverse_advantage_bps", ""),
                "ticker_direction_reason": row.get("ticker_direction_reason", ""),
                "expected_return_scope": _metric_scope(row, "expected_return"),
                "hit_rate_scope": _metric_scope(row, "hit_rate"),
                "profit_factor_scope": _metric_scope(row, "profit_factor"),
                "ticker_direction_memory_status": _ticker_memory_status(row, min_ticker_samples),
                "inverse_warning_status": inverse_status,
                "inverse_warning_actionable": inverse_actionable,
                **authority,
                "confidence_bucket": _confidence_bucket(row, authority.get("probability_calibration_status", "")),
            }
        )
    out = pd.DataFrame(rows)
    out = _infer_metric_scope(out, "validated_expected_return_bps", "expected_return_scope")
    out = _infer_metric_scope(out, "validated_hit_rate", "hit_rate_scope")
    out = _infer_metric_scope(out, "validated_profit_factor", "profit_factor_scope")
    executable_idx = (
        out[out["executable"].eq(True)]
        .sort_values(["raw_rank", "symbol"], ascending=[True, True], kind="mergesort")
        .index
    )
    out.loc[executable_idx, "execution_rank"] = range(1, len(executable_idx) + 1)
    return out.drop(columns=["__index"]).reindex(columns=OUTPUT_COLUMNS)


def latest_candidate_or_plan(root: Path | str | None = None) -> tuple[Path | None, pd.DataFrame]:
    base = Path(root) if root else PROJECT_ROOT
    portal = base / "data" / "portal_outputs"
    patterns = ["08_alpaca_paper_order_plan_*.csv", "08_alpaca_paper_candidate_pool_*.csv"]
    files: list[Path] = []
    for pattern in patterns:
        files.extend([path for path in portal.glob(pattern) if path.is_file()])
    if not files:
        return None, pd.DataFrame()
    path = max(files, key=lambda item: item.stat().st_mtime)
    return path, pd.read_csv(path, low_memory=False)


def write_execution_ranked_candidates(
    candidates: pd.DataFrame,
    *,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
    short_policy: ShortSidePolicy | None = None,
) -> Path:
    out_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "data" / "portal_outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    ranked = build_execution_ranked_candidates(candidates, short_policy=short_policy)
    path = out_dir / f"execution_ranked_candidates_{run_stamp}.csv"
    ranked.to_csv(path, index=False)
    return path


def latest_execution_ranked_path(root: Path | str | None = None) -> Path | None:
    base = Path(root) if root else PROJECT_ROOT
    portal = base / "data" / "portal_outputs"
    files = [path for path in portal.glob("execution_ranked_candidates_*.csv") if path.is_file()]
    return max(files, key=lambda item: item.stat().st_mtime) if files else None


def _boolish(value: Any, default: bool = False) -> bool:
    if value in [None, ""]:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return default


def _rank_present(value: Any) -> bool:
    return _num(value) is not None


def _safe_block_reasons(value: Any) -> bool:
    return _text(value).lower() in SAFE_EMPTY_REASONS


def execution_ranked_auto_open_candidates(
    path: Path | str | None = None,
    *,
    root: Path | str | None = None,
) -> list[dict[str, Any]]:
    source = Path(path) if path else latest_execution_ranked_path(root)
    if source is None or not source.exists():
        return []
    try:
        frame = pd.read_csv(source, low_memory=False)
    except Exception:
        return []
    if frame.empty:
        return []
    if "execution_rank" not in frame.columns:
        return []

    out: list[dict[str, Any]] = []
    ranked = frame.sort_values(["execution_rank", "raw_rank", "symbol"], na_position="last", kind="mergesort")
    for _, row in ranked.iterrows():
        status = _text(row.get("status")).lower()
        domain = _text(row.get("execution_domain")).lower()
        side = _side(row)
        source_action = (_text(row.get("source_trade_action")) or _text(row.get("trade_action"))).lower()
        if domain != EXECUTION_CANDIDATE:
            continue
        if status != "executable":
            continue
        if not _rank_present(row.get("execution_rank")):
            continue
        if side not in {"buy", "sell"}:
            continue
        if _boolish(row.get("research_only"), False):
            continue
        if "executable" in frame.columns and not _boolish(row.get("executable"), False):
            continue
        if "execution_eligible" in frame.columns and not _boolish(row.get("execution_eligible"), False):
            continue
        if not _safe_block_reasons(row.get("all_block_reasons")):
            continue
        if source_action in {"no decision", "no_decision", "none"}:
            continue
        trade_action = "Short" if side == "sell" else "Long"
        details = row.to_dict()
        details.update(
            {
                "candidate_source": "execution_ranked_candidates",
                "model_evidence_source": "execution_ranked_candidates",
                "execution_ranked_source_path": str(source),
                "execution_ranked_candidate": True,
                "trade_quality_status": "approved",
                "candidate_status": "approved",
                "order_eligible": True,
                "side": side,
                "trade_action": trade_action,
                "source_trade_action": trade_action,
                "current_trade_action": trade_action,
                "nightly_bias": "short" if side == "sell" else "long",
                "rank_overall": row.get("raw_rank", row.get("research_rank", "")),
                "strategy_mode": row.get("strategy_mode", "execution_ranked"),
            }
        )
        out.append(
            {
                "symbol": _symbol(row),
                "side": side,
                "trade_action": trade_action,
                "source_trade_action": trade_action,
                "current_trade_action": trade_action,
                "nightly_bias": "short" if side == "sell" else "long",
                "rank_overall": row.get("raw_rank", row.get("research_rank", "")),
                "execution_rank": row.get("execution_rank", ""),
                "raw_rank": row.get("raw_rank", ""),
                "candidate_status": "approved",
                "trade_quality_status": "approved",
                "order_eligible": True,
                "details": details,
            }
        )
    return out
