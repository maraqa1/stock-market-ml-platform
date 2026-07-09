from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.candidates.execution_ranker import build_execution_ranked_candidates, latest_candidate_or_plan
from stockml.common.paths import PROJECT_ROOT, timestamp


DETAIL_COLUMNS = [
    "symbol",
    "rank",
    "source_trade_action",
    "planner_derived_direction",
    "model_score",
    "rank_overall",
    "directional_strength",
    "confidence_score",
    "risk_adjusted_score",
    "meta_label_probability",
    "ticker_direction_bias",
    "ticker_direction_sample_count",
    "expected_return_scope",
    "validated_expected_return_bps",
    "risk_tier",
    "volatility_tier",
    "liquidity_tier",
    "primary_block_reason",
    "execution_domain",
    "source_no_decision_reason",
    "long_near_miss",
]

SOURCE_APPROVED = {"long", "short"}
LONG_TEXT = {"long", "buy"}


@dataclass(frozen=True)
class SourceDirectionCoverageOutput:
    detail_path: Path
    summary_path: Path
    detail_rows: int
    status: str
    no_decision_reason_distribution: dict[str, int]
    long_near_miss_count: int


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


def _series(frame: pd.DataFrame, column: str, default: object = "") -> pd.Series:
    return frame[column] if column in frame.columns else pd.Series(default, index=frame.index)


def _first_existing(row: pd.Series, names: list[str]) -> Any:
    for name in names:
        if name in row.index:
            value = row.get(name)
            if _text(value) or _num(value) is not None:
                return value
    return ""


def _source_action(row: pd.Series) -> str:
    return _text(_first_existing(row, ["source_trade_action", "current_trade_action", "trade_action"]))


def _planner_direction(row: pd.Series) -> str:
    for name in ["planner_derived_direction", "directional_action", "final_proposed_side", "trade_action", "side"]:
        value = _text(row.get(name)).lower()
        if value in {"long", "buy"}:
            return "LONG"
        if value in {"short", "sell"}:
            return "SHORT"
    return "NONE"


def _all_reasons(row: pd.Series) -> set[str]:
    reasons: list[str] = []
    for column in ["primary_block_reason", "all_block_reasons", "execution_domain_reason", "direction_primary_reason", "trade_quality_reason"]:
        value = _text(row.get(column))
        if value:
            reasons.extend([part.strip() for part in value.replace(";", "|").split("|") if part.strip()])
    return set(reasons)


def source_no_decision_reason(row: pd.Series, *, min_direction_samples: int = 20) -> str:
    source_action = _source_action(row).lower().replace("_", " ")
    if source_action in SOURCE_APPROVED:
        return ""

    reasons = _all_reasons(row)
    model_score = _num(row.get("model_score"))
    rank_overall = _num(row.get("rank_overall"))
    directional_strength = _num(row.get("directional_strength"))
    confidence_score = _num(row.get("confidence_score"))
    meta_label_probability = _num(row.get("meta_label_probability"))
    sample_count = int(_num(row.get("ticker_direction_sample_count")) or 0)
    bias = _text(row.get("ticker_direction_bias")).lower()
    planner = _planner_direction(row)

    if "source_trade_action" not in row.index and not source_action:
        return "source_signal_not_available"
    if model_score is None and rank_overall is None:
        return "missing_model_score"
    if "direction_memory_conflict" in reasons:
        return "direction_memory_conflict"
    if "risk_gate_failed" in reasons:
        return "risk_gate_failed"
    if meta_label_probability is None and "meta_label_probability_below_threshold" not in reasons:
        return "meta_label_missing"
    if "meta_label_probability_below_threshold" in reasons or _text(row.get("meta_label_decision")).lower() in {"skip", "rejected", "no trade"}:
        return "meta_label_rejected"
    if sample_count < min_direction_samples or bias in {"", "insufficient_data"}:
        return "insufficient_direction_memory"
    if directional_strength is not None and directional_strength < 0.5:
        return "weak_directional_strength"
    if confidence_score is not None and confidence_score < 0.5:
        return "weak_confidence"
    if planner in {"LONG", "SHORT"} or "planner_derived_action_without_source_approval" in reasons:
        return "planner_only_without_source_authority"
    if model_score is not None or rank_overall is not None:
        return "source_threshold_too_strict"
    return "unknown"


def _is_long_near_miss(row: pd.Series) -> bool:
    if _source_action(row).lower() in SOURCE_APPROVED:
        return False
    if _planner_direction(row) != "LONG":
        return False
    reason = _text(row.get("source_no_decision_reason"))
    hard_reasons = {
        "missing_model_score",
        "meta_label_missing",
        "risk_gate_failed",
        "direction_memory_conflict",
        "source_signal_not_available",
    }
    return reason not in hard_reasons


def build_source_direction_coverage_detail(
    candidates: pd.DataFrame,
    *,
    min_direction_samples: int = 20,
) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=DETAIL_COLUMNS)
    ranked = build_execution_ranked_candidates(candidates)
    source = candidates.reset_index(drop=True).copy()
    ranked = ranked.reset_index(drop=True)
    out = pd.DataFrame(index=ranked.index)
    out["symbol"] = _series(ranked, "symbol")
    out["rank"] = pd.to_numeric(_series(ranked, "raw_rank", _series(source, "rank_overall", "")), errors="coerce")
    out["source_trade_action"] = _series(source, "source_trade_action", _series(source, "trade_action", ""))
    out["planner_derived_direction"] = source.apply(_planner_direction, axis=1)
    for column in [
        "model_score",
        "rank_overall",
        "directional_strength",
        "confidence_score",
        "risk_adjusted_score",
        "meta_label_probability",
        "ticker_direction_bias",
        "ticker_direction_sample_count",
        "risk_tier",
        "volatility_tier",
        "liquidity_tier",
    ]:
        out[column] = _series(source, column, _series(ranked, column, ""))
    out["expected_return_scope"] = _series(ranked, "expected_return_scope", _series(source, "expected_return_scope", ""))
    out["validated_expected_return_bps"] = _series(ranked, "validated_expected_return_bps", _series(source, "validated_expected_return_bps", ""))
    out["primary_block_reason"] = _series(ranked, "primary_block_reason")
    out["execution_domain"] = _series(ranked, "execution_domain")
    out["source_no_decision_reason"] = source.apply(lambda row: source_no_decision_reason(row, min_direction_samples=min_direction_samples), axis=1)
    out["long_near_miss"] = out.apply(_is_long_near_miss, axis=1)
    return out.reindex(columns=DETAIL_COLUMNS).sort_values(["rank", "symbol"], na_position="last", kind="mergesort")


def _counts(frame: pd.DataFrame, column: str) -> dict[str, int]:
    if frame.empty or column not in frame.columns:
        return {}
    return {str(key): int(value) for key, value in frame[column].fillna("NA").astype(str).value_counts().items()}


def _summary_lines(detail: pd.DataFrame, source_path: Path | None, detail_path: Path) -> list[str]:
    source_action = detail["source_trade_action"].fillna("").astype(str).str.lower().str.replace("_", " ", regex=False) if "source_trade_action" in detail else pd.Series(dtype="object")
    no_decision = ~source_action.isin(SOURCE_APPROVED) if len(source_action) else pd.Series(dtype=bool)
    long_near = detail[detail.get("long_near_miss", pd.Series(False, index=detail.index)).fillna(False).astype(bool)].copy()
    no_decision_reasons = _counts(detail[no_decision] if len(no_decision) else detail.iloc[0:0], "source_no_decision_reason")
    missing_model = no_decision_reasons.get("missing_model_score", 0) + no_decision_reasons.get("source_signal_not_available", 0)
    conservative = no_decision_reasons.get("source_threshold_too_strict", 0) + no_decision_reasons.get("weak_directional_strength", 0) + no_decision_reasons.get("weak_confidence", 0)
    can_expand = bool(len(long_near) and conservative >= max(1, missing_model))
    top_cols = [col for col in ["rank", "symbol", "planner_derived_direction", "model_score", "directional_strength", "confidence_score", "source_no_decision_reason", "primary_block_reason"] if col in long_near.columns]
    lines = [
        "# Source Direction Coverage Diagnostic",
        "",
        f"- source_path: `{source_path}`",
        f"- detail_path: `{detail_path}`",
        f"- total_candidates: {len(detail)}",
        f"- source_approved_long_count: {int(source_action.eq('long').sum()) if len(source_action) else 0}",
        f"- source_approved_short_count: {int(source_action.eq('short').sum()) if len(source_action) else 0}",
        f"- no_decision_count: {int(no_decision.sum()) if len(no_decision) else 0}",
        f"- long_near_miss_count: {len(long_near)}",
        f"- source_signal_too_conservative: {'yes' if conservative > missing_model else 'no'}",
        f"- missing_model_evidence_main_blocker: {'yes' if missing_model > conservative else 'no'}",
        f"- long_coverage_can_be_safely_expanded: {'yes' if can_expand else 'no'}",
        "",
        "## No Decision Reasons",
    ]
    lines.extend([f"- {reason}: {count}" for reason, count in no_decision_reasons.items()] or ["- none: 0"])
    lines.extend(["", "## Top 20 Long Near Misses"])
    if long_near.empty:
        lines.append("- none")
    else:
        for row in long_near.sort_values(["rank", "symbol"], na_position="last", kind="mergesort").head(20)[top_cols].to_dict("records"):
            payload = ", ".join(f"{key}={_text(value)}" for key, value in row.items())
            lines.append(f"- {payload}")
    return lines


def run_source_direction_coverage_diagnostic(
    *,
    candidates: pd.DataFrame | None = None,
    source_path: Path | None = None,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
) -> SourceDirectionCoverageOutput:
    if candidates is None:
        source_path, candidates = latest_candidate_or_plan()
    run_stamp = stamp or timestamp()
    out_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    detail_path = out_dir / f"source_direction_coverage_detail_{run_stamp}.csv"
    summary_path = out_dir / f"source_direction_coverage_summary_{run_stamp}.md"

    if candidates is None or candidates.empty:
        detail = pd.DataFrame([{"status": "missing_data", "missing_inputs": "latest_candidate_or_plan"}])
        detail.to_csv(detail_path, index=False)
        summary_path.write_text("# Source Direction Coverage Diagnostic\n\n- status: missing_data\n- missing_inputs: latest_candidate_or_plan\n", encoding="utf-8")
        return SourceDirectionCoverageOutput(detail_path, summary_path, len(detail), "missing_data", {}, 0)

    detail = build_source_direction_coverage_detail(candidates)
    detail.to_csv(detail_path, index=False)
    summary_path.write_text("\n".join(_summary_lines(detail, source_path, detail_path)) + "\n", encoding="utf-8")
    source_action = detail["source_trade_action"].fillna("").astype(str).str.lower().str.replace("_", " ", regex=False)
    no_decision = detail[~source_action.isin(SOURCE_APPROVED)]
    return SourceDirectionCoverageOutput(
        detail_path=detail_path,
        summary_path=summary_path,
        detail_rows=len(detail),
        status="ok",
        no_decision_reason_distribution=_counts(no_decision, "source_no_decision_reason"),
        long_near_miss_count=int(detail["long_near_miss"].fillna(False).astype(bool).sum()),
    )
