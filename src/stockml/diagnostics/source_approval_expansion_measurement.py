from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.diagnostics.source_direction_coverage import build_source_direction_coverage_detail
from stockml.trading.counterfactual_log import latest_counterfactual_candidates, write_counterfactual_forward_returns
from stockml.trading.source_approval_expansion import (
    SourceApprovalExpansionConfig,
    evaluate_source_approval_expansion,
    load_source_approval_expansion_config,
)


BUCKET_SOURCE_APPROVED = "source_approved"
BUCKET_EXPANSION_ELIGIBLE = "expansion_eligible"
BUCKET_STILL_BLOCKED = "still_blocked"
NON_MATERIAL_CONFIRMATION = "non_material_measurement_only_no_lane_change"


@dataclass(frozen=True)
class ExpansionMeasurementOutput:
    ticket13_detail_path: Path
    ticket13_summary_path: Path
    ticket14_report_path: Path
    ticket14_summary_path: Path
    ticket13_rows: int
    ticket14_rows: int
    daily_bucket_counts: dict[str, int]
    edge_verdict: str
    edge_n: dict[str, int]
    materiality_confirmation: str


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


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"true", "1", "yes", "y"}


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(pd.NA, index=frame.index, dtype="Float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _source_action(row: pd.Series) -> str:
    return _text(row.get("source_trade_action")).lower().replace("_", " ")


def _is_source_approved(row: pd.Series) -> bool:
    action = _source_action(row)
    if action in {"long", "short"}:
        return True
    if _text(row.get("final_execution_side")).upper() in {"LONG", "SHORT"}:
        return True
    if _bool(row.get("executable")) or _text(row.get("status")).lower() == "executable":
        return True
    return False


def _reason_set(row: pd.Series) -> set[str]:
    values: list[str] = []
    for column in [
        "source_no_decision_reason",
        "source_expansion_reason",
        "primary_block_reason",
        "all_block_reasons",
        "trade_quality_reason",
        "direction_primary_reason",
        "direction_resolution_reason",
        "session_reject_reason",
    ]:
        text = _text(row.get(column)).lower()
        if text:
            values.extend(part.strip() for part in text.replace(";", "|").split("|") if part.strip())
    return set(values)


def _condition_pass_columns(row: pd.Series, cfg: SourceApprovalExpansionConfig) -> dict[str, Any]:
    sample_count = int(_num(row.get("ticker_direction_sample_count")) or 0)
    bias = _text(row.get("ticker_direction_bias")).lower()
    expected = _num(row.get("validated_expected_return_bps"))
    scope = _text(row.get("expected_return_scope")).lower()
    risk_tier = _text(row.get("risk_tier")).lower()
    volatility_tier = _text(row.get("volatility_tier")).lower()
    no_decision_reason = _text(row.get("source_no_decision_reason")).lower()
    reasons = _reason_set(row)
    block_if_hits = sorted(reasons.intersection(set(cfg.block_if)))
    return {
        "sample_count_pass": sample_count >= cfg.min_ticker_direction_sample_count,
        "ticker_direction_bias_pass": bias == cfg.require_ticker_direction_bias.lower(),
        "positive_validated_expected_return_pass": (expected is not None and expected > 0) if cfg.require_positive_validated_expected_return else True,
        "expected_return_scope_pass": scope in set(cfg.require_expected_return_scope_in),
        "risk_tier_pass": risk_tier not in {"reject", "rejected", "blocked", "unacceptable"} if cfg.require_risk_tier_not_reject else True,
        "volatility_tier_pass": volatility_tier != "extreme" if cfg.require_volatility_not_extreme else True,
        "source_no_decision_reason_pass": (not no_decision_reason or no_decision_reason in set(cfg.require_source_no_decision_reason_in)),
        "block_if_pass": not block_if_hits,
        "block_if_hits": "|".join(block_if_hits),
    }


def _normalize_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    frame = candidates.copy() if candidates is not None else pd.DataFrame()
    if frame.empty:
        return frame
    coverage = build_source_direction_coverage_detail(frame).reset_index(drop=True)
    frame = frame.reset_index(drop=True)
    for column in ["source_no_decision_reason", "primary_block_reason", "execution_domain"]:
        if column in coverage.columns:
            if column in frame.columns:
                frame[column] = frame[column].where(frame[column].astype(str).str.strip().ne(""), coverage[column])
            else:
                frame[column] = coverage[column]
    if "planner_derived_direction" not in frame.columns and "planner_derived_direction" in coverage.columns:
        frame["planner_derived_direction"] = coverage["planner_derived_direction"]
    if "rank" not in frame.columns and "rank" in coverage.columns:
        frame["rank"] = coverage["rank"]
    return frame


def build_expansion_bucket_detail(
    candidates: pd.DataFrame,
    *,
    config: SourceApprovalExpansionConfig | None = None,
) -> pd.DataFrame:
    cfg = config or load_source_approval_expansion_config()
    frame = _normalize_candidates(candidates)
    if frame.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        expansion = evaluate_source_approval_expansion(row, config=cfg)
        source_approved = _is_source_approved(row)
        if source_approved:
            bucket = BUCKET_SOURCE_APPROVED
        elif expansion.get("source_expansion_decision") in {"would_upgrade", "watch_candidate"} and expansion.get("would_upgrade_to_source_long"):
            bucket = BUCKET_EXPANSION_ELIGIBLE
        else:
            bucket = BUCKET_STILL_BLOCKED
        out = row.to_dict()
        out.update(expansion)
        out.update(_condition_pass_columns(row, cfg))
        out["expansion_bucket"] = bucket
        out["expansion_config_enabled"] = cfg.enabled
        out["expansion_config_mode"] = cfg.mode
        out["materiality_confirmation"] = NON_MATERIAL_CONFIRMATION
        if "symbol" not in out or not _text(out.get("symbol")):
            out["symbol"] = _text(out.get("ticker")).upper()
        rows.append(out)
    detail = pd.DataFrame(rows)
    sort_cols = [column for column in ["rank", "raw_rank", "symbol"] if column in detail.columns]
    return detail.sort_values(sort_cols, na_position="last", kind="mergesort") if sort_cols else detail


def _bucket_counts(detail: pd.DataFrame) -> dict[str, int]:
    counts = {BUCKET_SOURCE_APPROVED: 0, BUCKET_EXPANSION_ELIGIBLE: 0, BUCKET_STILL_BLOCKED: 0}
    if not detail.empty and "expansion_bucket" in detail.columns:
        counts.update({str(key): int(value) for key, value in detail["expansion_bucket"].fillna(BUCKET_STILL_BLOCKED).value_counts().items()})
    return counts


def _ticket13_lines(detail: pd.DataFrame, source_path: Path | str | None, detail_path: Path) -> list[str]:
    counts = _bucket_counts(detail)
    eligible = detail[detail.get("expansion_bucket", pd.Series("", index=detail.index)).eq(BUCKET_EXPANSION_ELIGIBLE)] if not detail.empty else detail
    spot_cols = [
        "symbol",
        "rank",
        "ticker_direction_sample_count",
        "ticker_direction_bias",
        "validated_expected_return_bps",
        "expected_return_scope",
        "risk_tier",
        "volatility_tier",
        "source_no_decision_reason",
        "source_expansion_reason",
    ]
    spot_cols = [col for col in spot_cols if col in eligible.columns]
    lines = [
        "# Ticket 13 - Source Approval Expansion Measurement",
        "",
        f"- created_at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- candidate_source: `{source_path or ''}`",
        f"- detail_path: `{detail_path}`",
        f"- materiality_confirmation: {NON_MATERIAL_CONFIRMATION}",
        "- executable_status_changed: false",
        "- config_change: none",
        "",
        "## Daily Bucket Counts",
        f"- {BUCKET_SOURCE_APPROVED}: {counts[BUCKET_SOURCE_APPROVED]}",
        f"- {BUCKET_EXPANSION_ELIGIBLE}: {counts[BUCKET_EXPANSION_ELIGIBLE]}",
        f"- {BUCKET_STILL_BLOCKED}: {counts[BUCKET_STILL_BLOCKED]}",
        "",
        "## Expansion Eligible Spot Check",
    ]
    if eligible.empty:
        lines.append("- none")
    else:
        for payload in eligible.head(10)[spot_cols].to_dict("records"):
            lines.append("- " + ", ".join(f"{key}={_text(value)}" for key, value in payload.items()))
    return lines


def _latest_forward_returns(root: Path) -> Path | None:
    directory = root / "data" / "trading" / "forward_paper"
    files = [path for path in directory.glob("counterfactual_forward_returns_*.csv") if path.is_file()]
    return max(files, key=lambda item: item.stat().st_mtime) if files else None


def _latest_full_candidate_pool(root: Path) -> tuple[Path | None, pd.DataFrame]:
    portal = root / "data" / "portal_outputs"
    pools = [path for path in portal.glob("08_alpaca_paper_candidate_pool_*.csv") if path.is_file()]
    plans = [path for path in portal.glob("08_alpaca_paper_order_plan_*.csv") if path.is_file()]
    files = pools or plans
    if not files:
        return None, pd.DataFrame()
    source = max(files, key=lambda item: item.stat().st_mtime)
    return source, pd.read_csv(source, low_memory=False)


def _load_counterfactual_with_returns(
    *,
    root: Path,
    counterfactual_path: Path | str | None = None,
    stamp: str | None = None,
) -> tuple[pd.DataFrame, Path | None]:
    source = Path(counterfactual_path) if counterfactual_path else _latest_forward_returns(root)
    if source is not None and source.exists():
        frame = pd.read_csv(source, low_memory=False)
        if "directional_forward_5d_bps" in frame.columns:
            return frame, source
    candidate_source = Path(counterfactual_path) if counterfactual_path else latest_counterfactual_candidates(root)
    if candidate_source is None:
        return pd.DataFrame(), None
    result = write_counterfactual_forward_returns(candidate_source, output_dir=root / "data" / "trading" / "forward_paper", stamp=stamp)
    return pd.read_csv(result.path, low_memory=False), result.path


def _attach_buckets_to_counterfactual(frame: pd.DataFrame, cfg: SourceApprovalExpansionConfig) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    if "source_trade_action" not in out.columns:
        out["source_trade_action"] = out.get("trade_action", "")
    if "planner_derived_direction" not in out.columns:
        out["planner_derived_direction"] = out.get("side", "")
    return build_expansion_bucket_detail(out, config=cfg)


def build_expansion_edge_report(
    counterfactual_with_returns: pd.DataFrame,
    *,
    config: SourceApprovalExpansionConfig | None = None,
    minimum_powered_rows: int = 30,
) -> tuple[pd.DataFrame, str, dict[str, int]]:
    cfg = config or load_source_approval_expansion_config()
    detail = _attach_buckets_to_counterfactual(counterfactual_with_returns, cfg)
    if detail.empty:
        empty = pd.DataFrame(
            columns=[
                "expansion_bucket",
                "rows",
                "n_5d",
                "n_10d",
                "mean_gross_5d_bps",
                "mean_net_5d_bps",
                "mean_gross_10d_bps",
                "mean_net_10d_bps",
                "hit_rate_5d",
                "verdict",
            ]
        )
        return empty, "INSUFFICIENT DATA", {}

    gross_5d = _numeric_series(detail, "directional_forward_5d_bps")
    if gross_5d.isna().all() and "forward_5d_return" in detail.columns:
        sign = detail.get("side", pd.Series("", index=detail.index)).astype(str).str.lower().map({"buy": 1.0, "long": 1.0, "sell": -1.0, "short": -1.0}).fillna(0.0)
        gross_5d = pd.to_numeric(detail.get("forward_5d_return"), errors="coerce") * sign * 10000.0
    gross_10d = _numeric_series(detail, "directional_forward_10d_bps")
    if gross_10d.isna().all() and "forward_10d_return" in detail.columns:
        sign = detail.get("side", pd.Series("", index=detail.index)).astype(str).str.lower().map({"buy": 1.0, "long": 1.0, "sell": -1.0, "short": -1.0}).fillna(0.0)
        gross_10d = pd.to_numeric(detail.get("forward_10d_return"), errors="coerce") * sign * 10000.0
    cost = pd.to_numeric(detail.get("estimated_execution_cost_bps"), errors="coerce").fillna(10.0)
    detail["__gross_5d_bps"] = gross_5d
    detail["__net_5d_bps"] = gross_5d - cost
    detail["__gross_10d_bps"] = gross_10d
    detail["__net_10d_bps"] = gross_10d - cost

    rows: list[dict[str, Any]] = []
    n_by_bucket: dict[str, int] = {}
    for bucket in [BUCKET_SOURCE_APPROVED, BUCKET_EXPANSION_ELIGIBLE, BUCKET_STILL_BLOCKED]:
        group = detail[detail["expansion_bucket"].eq(bucket)]
        g5 = pd.to_numeric(group["__gross_5d_bps"], errors="coerce")
        n5 = int(g5.notna().sum())
        n10 = int(pd.to_numeric(group["__gross_10d_bps"], errors="coerce").notna().sum())
        n_by_bucket[bucket] = n5
        rows.append(
            {
                "expansion_bucket": bucket,
                "rows": int(len(group)),
                "n_5d": n5,
                "n_10d": n10,
                "mean_gross_5d_bps": g5.mean() if n5 else pd.NA,
                "mean_net_5d_bps": pd.to_numeric(group["__net_5d_bps"], errors="coerce").mean() if n5 else pd.NA,
                "mean_gross_10d_bps": pd.to_numeric(group["__gross_10d_bps"], errors="coerce").mean() if n10 else pd.NA,
                "mean_net_10d_bps": pd.to_numeric(group["__net_10d_bps"], errors="coerce").mean() if n10 else pd.NA,
                "hit_rate_5d": float((pd.to_numeric(group["__net_5d_bps"], errors="coerce") > 0).mean()) if n5 else pd.NA,
                "verdict": "INSUFFICIENT DATA" if n5 < minimum_powered_rows else "READY_FOR_REVIEW",
            }
        )
    report = pd.DataFrame(rows)
    if min(n_by_bucket.get(BUCKET_SOURCE_APPROVED, 0), n_by_bucket.get(BUCKET_EXPANSION_ELIGIBLE, 0)) < minimum_powered_rows:
        verdict = "INSUFFICIENT DATA"
    else:
        expansion_net = pd.to_numeric(report.loc[report["expansion_bucket"].eq(BUCKET_EXPANSION_ELIGIBLE), "mean_net_5d_bps"], errors="coerce").iloc[0]
        source_net = pd.to_numeric(report.loc[report["expansion_bucket"].eq(BUCKET_SOURCE_APPROVED), "mean_net_5d_bps"], errors="coerce").iloc[0]
        verdict = "PASS_MEASUREMENT_ONLY" if expansion_net > 0 and expansion_net >= source_net - 10 else "FAIL_NO_EDGE"
    report["overall_verdict"] = verdict
    report["materiality_confirmation"] = NON_MATERIAL_CONFIRMATION
    return report, verdict, n_by_bucket


def _ticket14_lines(report: pd.DataFrame, source_path: Path | str | None, report_path: Path, verdict: str) -> list[str]:
    lines = [
        "# Ticket 14 - Source Expansion Counterfactual Edge Test",
        "",
        f"- created_at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- counterfactual_source: `{source_path or ''}`",
        f"- report_path: `{report_path}`",
        f"- verdict: {verdict}",
        f"- materiality_confirmation: {NON_MATERIAL_CONFIRMATION}",
        "- promotion_action_taken: false",
        "",
        "## Buckets",
    ]
    if report.empty:
        lines.append("- none")
    else:
        for row in report.to_dict("records"):
            lines.append(
                f"- {row['expansion_bucket']}: rows={row['rows']}, n_5d={row['n_5d']}, "
                f"mean_net_5d_bps={_text(row['mean_net_5d_bps'])}, verdict={row['verdict']}"
            )
    return lines


def run_source_approval_expansion_measurement(
    *,
    candidates: pd.DataFrame | None = None,
    candidate_path: Path | str | None = None,
    counterfactual_path: Path | str | None = None,
    output_dir: Path | str | None = None,
    root: Path | None = None,
    stamp: str | None = None,
    config: SourceApprovalExpansionConfig | None = None,
) -> ExpansionMeasurementOutput:
    base = root or PROJECT_ROOT
    cfg = config or load_source_approval_expansion_config()
    run_stamp = stamp or timestamp()
    out_dir = Path(output_dir) if output_dir else base / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)

    source_path: Path | str | None = candidate_path
    if candidates is None:
        if candidate_path:
            source_path = Path(candidate_path)
            candidates = pd.read_csv(source_path, low_memory=False) if Path(source_path).exists() else pd.DataFrame()
        else:
            source_path, candidates = _latest_full_candidate_pool(base)
    ticket13 = build_expansion_bucket_detail(candidates if candidates is not None else pd.DataFrame(), config=cfg)
    ticket13_detail = out_dir / f"source_approval_expansion_measurement_detail_{run_stamp}.csv"
    ticket13_summary = out_dir / f"source_approval_expansion_measurement_summary_{run_stamp}.md"
    ticket13.to_csv(ticket13_detail, index=False)
    ticket13_summary.write_text("\n".join(_ticket13_lines(ticket13, source_path, ticket13_detail)) + "\n", encoding="utf-8")

    cf_frame, cf_source = _load_counterfactual_with_returns(root=base, counterfactual_path=counterfactual_path, stamp=run_stamp)
    ticket14, verdict, n_by_bucket = build_expansion_edge_report(cf_frame, config=cfg)
    ticket14_report = out_dir / f"source_approval_expansion_edge_test_{run_stamp}.csv"
    ticket14_summary = out_dir / f"source_approval_expansion_edge_test_summary_{run_stamp}.md"
    ticket14.to_csv(ticket14_report, index=False)
    ticket14_summary.write_text("\n".join(_ticket14_lines(ticket14, cf_source, ticket14_report, verdict)) + "\n", encoding="utf-8")

    return ExpansionMeasurementOutput(
        ticket13_detail_path=ticket13_detail,
        ticket13_summary_path=ticket13_summary,
        ticket14_report_path=ticket14_report,
        ticket14_summary_path=ticket14_summary,
        ticket13_rows=len(ticket13),
        ticket14_rows=len(ticket14),
        daily_bucket_counts=_bucket_counts(ticket13),
        edge_verdict=verdict,
        edge_n=n_by_bucket,
        materiality_confirmation=NON_MATERIAL_CONFIRMATION,
    )
