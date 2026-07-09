from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from stockml.common.paths import PROJECT_ROOT


LONG = "LONG"
SHORT = "SHORT"
NONE = "NONE"

EXPANSION_FIELDS = [
    "source_expansion_candidate",
    "source_expansion_decision",
    "source_expansion_reason",
    "would_upgrade_to_source_long",
]

DEFAULT_REASON_ALLOWLIST = ("source_threshold_too_strict", "weak_confidence")
DEFAULT_SCOPE_ALLOWLIST = ("ticker", "bucket", "side")
DEFAULT_BLOCK_IF = (
    "direction_memory_conflict",
    "meta_label_rejected",
    "model_evidence_missing",
    "risk_gate_failed",
    "asset_not_overnight_tradable",
    "price_below_minimum",
)


@dataclass(frozen=True)
class SourceApprovalExpansionConfig:
    enabled: bool = False
    mode: str = "diagnostic_only"
    side: str = "LONG_ONLY"
    min_ticker_direction_sample_count: int = 50
    require_ticker_direction_bias: str = "trust_long"
    require_positive_validated_expected_return: bool = True
    require_expected_return_scope_in: tuple[str, ...] = DEFAULT_SCOPE_ALLOWLIST
    require_risk_tier_not_reject: bool = True
    require_volatility_not_extreme: bool = True
    require_source_no_decision_reason_in: tuple[str, ...] = DEFAULT_REASON_ALLOWLIST
    block_if: tuple[str, ...] = DEFAULT_BLOCK_IF


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


def _tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        return tuple(_text(item).lower() for item in value if _text(item))
    text = _text(value)
    if not text:
        return default
    return tuple(part.strip().lower() for part in text.replace(";", ",").split(",") if part.strip())


def _direction(value: Any) -> str:
    text = _text(value).lower().replace("_", " ")
    if text in {"long", "buy"}:
        return LONG
    if text in {"short", "sell"}:
        return SHORT
    return NONE


def _all_reasons(row: Any) -> set[str]:
    reasons: list[str] = []
    for column in [
        "source_no_decision_reason",
        "primary_block_reason",
        "all_block_reasons",
        "trade_quality_reason",
        "direction_primary_reason",
        "direction_resolution_reason",
        "session_reject_reason",
    ]:
        value = _text(row.get(column, "") if hasattr(row, "get") else "")
        if value:
            reasons.extend([part.strip().lower() for part in value.replace(";", "|").split("|") if part.strip()])
    return set(reasons)


def load_source_approval_expansion_config(path: Path | str | None = None) -> SourceApprovalExpansionConfig:
    config_path = Path(path) if path else PROJECT_ROOT / "config" / "trading.yaml"
    payload: dict[str, Any] = {}
    if config_path.exists():
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            payload = {}
    data = payload.get("source_approval_expansion", {}) if isinstance(payload, dict) else {}
    return SourceApprovalExpansionConfig(
        enabled=_bool(data.get("enabled"), False),
        mode=_text(data.get("mode")) or "diagnostic_only",
        side=_text(data.get("side")) or "LONG_ONLY",
        min_ticker_direction_sample_count=_int(data.get("min_ticker_direction_sample_count"), 50),
        require_ticker_direction_bias=_text(data.get("require_ticker_direction_bias")) or "trust_long",
        require_positive_validated_expected_return=_bool(data.get("require_positive_validated_expected_return"), True),
        require_expected_return_scope_in=_tuple(data.get("require_expected_return_scope_in"), DEFAULT_SCOPE_ALLOWLIST),
        require_risk_tier_not_reject=_bool(data.get("require_risk_tier_not_reject"), True),
        require_volatility_not_extreme=_bool(data.get("require_volatility_not_extreme"), True),
        require_source_no_decision_reason_in=_tuple(data.get("require_source_no_decision_reason_in"), DEFAULT_REASON_ALLOWLIST),
        block_if=_tuple(data.get("block_if"), DEFAULT_BLOCK_IF),
    )


def _base(candidate: bool, decision: str, reason: str, would_upgrade: bool) -> dict[str, Any]:
    return {
        "source_expansion_candidate": candidate,
        "source_expansion_decision": decision,
        "source_expansion_reason": reason,
        "would_upgrade_to_source_long": would_upgrade,
    }


def evaluate_source_approval_expansion(
    row: Any,
    *,
    config: SourceApprovalExpansionConfig | None = None,
) -> dict[str, Any]:
    cfg = config or load_source_approval_expansion_config()
    source = _direction(row.get("source_trade_action", "") if hasattr(row, "get") else "")
    trade = _direction(row.get("trade_action", "") if hasattr(row, "get") else "")
    directional = _direction(row.get("directional_action", "") if hasattr(row, "get") else "")
    planner = trade if trade != NONE else directional

    if source != NONE:
        return _base(False, "not_applicable", "already_source_approved", False)
    if planner != LONG:
        reason = "planner_short_not_allowed" if planner == SHORT else "planner_direction_not_long"
        return _base(False, "not_candidate", reason, False)
    if cfg.side.upper() != "LONG_ONLY":
        return _base(False, "blocked", "unsupported_expansion_side", False)

    reasons = _all_reasons(row)
    hard_blockers = sorted(reasons.intersection(set(cfg.block_if)))
    if hard_blockers:
        return _base(True, "blocked", hard_blockers[0], False)

    no_decision_reason = _text(row.get("source_no_decision_reason", "") if hasattr(row, "get") else "").lower()
    if no_decision_reason and no_decision_reason not in set(cfg.require_source_no_decision_reason_in):
        return _base(True, "blocked", f"source_no_decision_reason_not_allowed:{no_decision_reason}", False)

    sample_count = int(_num(row.get("ticker_direction_sample_count", "") if hasattr(row, "get") else "") or 0)
    if sample_count < cfg.min_ticker_direction_sample_count:
        return _base(True, "blocked", "insufficient_direction_memory", False)

    bias = _text(row.get("ticker_direction_bias", "") if hasattr(row, "get") else "").lower()
    if bias != cfg.require_ticker_direction_bias.lower():
        return _base(True, "blocked", "ticker_direction_bias_not_trust_long", False)

    expected_bps = _num(row.get("validated_expected_return_bps", "") if hasattr(row, "get") else "")
    if cfg.require_positive_validated_expected_return and (expected_bps is None or expected_bps <= 0):
        return _base(True, "blocked", "non_positive_validated_expected_return", False)

    scope = _text(row.get("expected_return_scope", "") if hasattr(row, "get") else "").lower()
    if scope not in set(cfg.require_expected_return_scope_in):
        return _base(True, "blocked", "expected_return_scope_not_allowed", False)

    risk_tier = _text(row.get("risk_tier", "") if hasattr(row, "get") else "").lower()
    if cfg.require_risk_tier_not_reject and risk_tier in {"reject", "rejected", "blocked", "unacceptable"}:
        return _base(True, "blocked", "risk_tier_reject", False)

    volatility_tier = _text(row.get("volatility_tier", "") if hasattr(row, "get") else "").lower()
    if cfg.require_volatility_not_extreme and volatility_tier == "extreme":
        return _base(True, "blocked", "volatility_extreme", False)

    if cfg.enabled and cfg.mode.lower() != "diagnostic_only":
        return _base(True, "watch_candidate", "eligible_for_watch_only_source_long_expansion", True)
    return _base(True, "would_upgrade", "diagnostic_only_would_upgrade_to_source_long", True)


def build_source_approval_expansion_detail(
    candidates: pd.DataFrame,
    *,
    config: SourceApprovalExpansionConfig | None = None,
) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return pd.DataFrame(
            columns=[
                "symbol",
                "rank",
                "source_trade_action",
                "planner_derived_direction",
                *EXPANSION_FIELDS,
                "source_no_decision_reason",
                "primary_block_reason",
                "all_block_reasons",
            ]
        )
    rows: list[dict[str, Any]] = []
    for _, row in candidates.iterrows():
        result = evaluate_source_approval_expansion(row, config=config)
        trade = _direction(row.get("trade_action", ""))
        directional = _direction(row.get("directional_action", ""))
        rows.append(
            {
                "symbol": _text(row.get("symbol")) or _text(row.get("ticker")),
                "rank": row.get("raw_rank", row.get("rank_overall", row.get("candidate_rank", ""))),
                "source_trade_action": row.get("source_trade_action", row.get("trade_action", "")),
                "planner_derived_direction": trade if trade != NONE else directional,
                **result,
                "source_no_decision_reason": row.get("source_no_decision_reason", ""),
                "ticker_direction_bias": row.get("ticker_direction_bias", ""),
                "ticker_direction_sample_count": row.get("ticker_direction_sample_count", ""),
                "expected_return_scope": row.get("expected_return_scope", ""),
                "validated_expected_return_bps": row.get("validated_expected_return_bps", ""),
                "risk_tier": row.get("risk_tier", ""),
                "volatility_tier": row.get("volatility_tier", ""),
                "primary_block_reason": row.get("primary_block_reason", ""),
                "all_block_reasons": row.get("all_block_reasons", row.get("trade_quality_reason", "")),
            }
        )
    return pd.DataFrame(rows).sort_values(["rank", "symbol"], na_position="last", kind="mergesort")


def write_source_approval_expansion_diagnostic(
    candidates: pd.DataFrame,
    *,
    output_dir: Path | str | None = None,
    stamp: str,
    config: SourceApprovalExpansionConfig | None = None,
) -> tuple[Path, Path, pd.DataFrame]:
    out_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    detail = build_source_approval_expansion_detail(candidates, config=config)
    csv_path = out_dir / f"source_approval_expansion_{stamp}.csv"
    md_path = out_dir / f"source_approval_expansion_{stamp}.md"
    detail.to_csv(csv_path, index=False)
    decisions = detail.get("source_expansion_decision", pd.Series(dtype=str)).fillna("NA").astype(str).value_counts().to_dict()
    would_upgrade = int(detail.get("would_upgrade_to_source_long", pd.Series(False, index=detail.index)).fillna(False).astype(bool).sum()) if not detail.empty else 0
    watch_only = int(detail.get("source_expansion_decision", pd.Series("", index=detail.index)).fillna("").astype(str).eq("watch_candidate").sum()) if not detail.empty else 0
    lines = [
        "# Source Approval Expansion Diagnostic",
        "",
        f"- total_rows: {len(detail)}",
        f"- would_upgrade_count: {would_upgrade}",
        f"- watch_only_count: {watch_only}",
        "",
        "## Decision Distribution",
    ]
    lines.extend([f"- {key}: {value}" for key, value in decisions.items()] or ["- none: 0"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, md_path, detail
