from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.diagnostics.expected_return_calibration import infer_expected_return_source
from stockml.diagnostics.validation_bucket_calibration import (
    CALIBRATION_COLUMNS,
    _num,
    _rank_percentile,
    _side,
    build_validation_bucket_calibration,
    load_latest_validation_predictions,
    prepare_validation_rows,
)


DEBUG_COLUMNS = [
    "section",
    "record_type",
    "name",
    "path",
    "exists",
    "row_count",
    "min_date",
    "max_date",
    "model_version_values",
    "symbol_count",
    "required_columns_present",
    "missing_columns",
    "forward_1d_return_coverage",
    "forward_5d_return_coverage",
    "forward_20d_return_coverage",
    "alpha_vs_spy_coverage",
    "alpha_vs_sector_coverage",
    "realised_forward_return_bps_coverage",
    "side",
    "bucket_id",
    "sample_count",
    "hit_rate",
    "avg_forward_return_bps",
    "net_expected_return_bps",
    "calibration_quality",
    "insufficient_data_reason",
    "symbol",
    "model_version",
    "model_score",
    "rank_pct",
    "candidate_date",
    "matched_calibration_bucket",
    "match_failure_reason",
    "expected_return_source",
    "expected_return_quality",
    "expected_return_issue",
    "root_cause",
]

SUMMARY_COLUMNS = ["metric", "value"]


@dataclass(frozen=True)
class CalibrationCoverageDebugOutputs:
    diagnostic_path: Path
    summary_path: Path
    validation_inputs_found: int
    forward_label_coverage: dict[str, float]
    bucket_count: int
    usable_bucket_count: int
    candidate_mapping_coverage: int
    root_cause: str
    recommended_next_fix: str


INPUT_SPECS = [
    (
        "walk_forward_predictions",
        ["walk_forward_predictions_*.csv", "*walk_forward*predictions*.csv"],
        ["ticker", "side", "trade_action"],
    ),
    ("validation_leaderboard", ["*validation_leaderboard*.csv", "*validation*.csv"], ["ticker"]),
    ("confidence_bucket_performance", ["advanced_model_confidence_bucket_performance_*.csv", "*bucket_performance*.csv"], ["rank_bucket"]),
    ("signal_table", ["advanced_model_signal_table_*.csv"], ["ticker", "trade_action"]),
    ("model_status", ["advanced_model_model_status_*.csv"], []),
    (
        "realised_forward_return_labels",
        ["*forward*return*.csv", "*labels*.csv", "gold_stock_decision_daily_*.csv", "06_us_gold_ml_dataset_*.csv"],
        ["ticker"],
    ),
    (
        "expected_return_bucket_calibration_latest",
        ["validation/expected_return_bucket_calibration_latest.csv"],
        ["bucket_type", "bucket_id", "side", "net_expected_return_bps", "calibration_quality"],
    ),
]

FORWARD_LABEL_COLUMNS = [
    "forward_1d_return",
    "forward_5d_return",
    "forward_20d_return",
    "alpha_vs_spy",
    "alpha_vs_sector",
    "realised_forward_return_bps",
]


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


def _latest_file(directory: Path, patterns: list[str]) -> Path | None:
    files: list[Path] = []
    for pattern in patterns:
        files.extend([p for p in directory.glob(pattern) if p.is_file()])
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _safe_read(path: Path | None, *, nrows: int | None = None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False, nrows=nrows)
    except Exception:
        return pd.DataFrame()


def _csv_row_count(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    try:
        with path.open("rb") as handle:
            lines = sum(1 for _ in handle)
        return max(0, lines - 1)
    except Exception:
        return 0


def _date_bounds(frame: pd.DataFrame) -> tuple[str, str]:
    for column in ["date", "event_at", "timestamp", "as_of_date", "prediction_date"]:
        if column in frame.columns:
            values = pd.to_datetime(frame[column], errors="coerce", utc=True).dropna()
            if not values.empty:
                return values.min().isoformat(), values.max().isoformat()
    return "", ""


def _model_versions(frame: pd.DataFrame) -> str:
    for column in ["model_version", "model_id", "model_name"]:
        if column in frame.columns:
            values = frame[column].dropna().astype(str).unique().tolist()
            return "|".join(values[:10])
    return ""


def _symbol_count(frame: pd.DataFrame) -> int:
    for column in ["symbol", "ticker"]:
        if column in frame.columns:
            return int(frame[column].dropna().astype(str).str.upper().nunique())
    return 0


def _coverage(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").notna().mean())


def locate_validation_inputs(root: Path | str | None = None) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    base = Path(root) if root else PROJECT_ROOT
    model_dir = base / "data" / "model_outputs"
    gold_dir = base / "data" / "gold"
    frames: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []
    for name, patterns, required in INPUT_SPECS:
        search_dir = gold_dir if name == "realised_forward_return_labels" else model_dir
        path = _latest_file(search_dir, patterns)
        if path is None and name == "realised_forward_return_labels":
            path = _latest_file(model_dir, patterns)
        frame = _safe_read(path, nrows=100_000)
        row_count = _csv_row_count(path) if path else 0
        frames[name] = frame
        min_date, max_date = _date_bounds(frame)
        missing = [column for column in required if column not in frame.columns]
        row = {
            "section": "validation_inputs",
            "record_type": "input_file",
            "name": name,
            "path": str(path or ""),
            "exists": bool(path and path.exists()),
            "row_count": int(row_count or len(frame)),
            "min_date": min_date,
            "max_date": max_date,
            "model_version_values": _model_versions(frame),
            "symbol_count": _symbol_count(frame),
            "required_columns_present": not missing,
            "missing_columns": "|".join(missing),
        }
        for column in FORWARD_LABEL_COLUMNS:
            row[f"{column}_coverage"] = _coverage(frame, column)
        rows.append(row)
    return pd.DataFrame(rows), frames


def _best_validation_frame(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    for name in ["walk_forward_predictions", "validation_leaderboard", "realised_forward_return_labels", "signal_table"]:
        frame = frames.get(name, pd.DataFrame())
        if not frame.empty:
            return frame
    path, frame = load_latest_validation_predictions()
    return frame


def _bucket_debug_rows(calibration: pd.DataFrame) -> pd.DataFrame:
    if calibration.empty:
        return pd.DataFrame(
            [
                {
                    "section": "bucket_construction",
                    "record_type": "bucket",
                    "name": "no_buckets",
                    "insufficient_data_reason": "missing_forward_return_labels",
                    "root_cause": "missing_forward_return_labels",
                }
            ]
        )
    rows = []
    for row in calibration.to_dict("records"):
        rows.append(
            {
                "section": "bucket_construction",
                "record_type": "bucket",
                "name": row.get("bucket_type", ""),
                "side": row.get("side", ""),
                "bucket_id": row.get("bucket_id", ""),
                "sample_count": row.get("sample_count", 0),
                "hit_rate": row.get("hit_rate", pd.NA),
                "avg_forward_return_bps": row.get("avg_forward_return_bps", pd.NA),
                "net_expected_return_bps": row.get("net_expected_return_bps", pd.NA),
                "calibration_quality": row.get("calibration_quality", ""),
                "insufficient_data_reason": row.get("insufficient_data_reason", ""),
            }
        )
    return pd.DataFrame(rows)


def _candidate_model_version(row: pd.Series) -> str:
    for column in ["model_version", "model_id", "model_name"]:
        value = _text(row.get(column))
        if value:
            return value
    return ""


def _candidate_date(row: pd.Series) -> str:
    for column in ["date", "as_of_date", "prediction_date", "timestamp"]:
        value = _text(row.get(column))
        if value:
            return value
    return ""


def _normalised_candidate_frame(candidates: pd.DataFrame) -> pd.DataFrame:
    frame = candidates.copy()
    if "side" not in frame.columns:
        frame["side"] = frame.get("trade_action", "").map(_side)
    else:
        frame["side"] = frame["side"].map(_side)
        missing = frame["side"].eq("")
        if "trade_action" in frame.columns:
            frame.loc[missing, "side"] = frame.loc[missing, "trade_action"].map(_side)
    return frame


def candidate_mapping_debug(candidates: pd.DataFrame, calibration: pd.DataFrame | None) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=DEBUG_COLUMNS)
    frame = _normalised_candidate_frame(candidates)
    rank_pct = _rank_percentile(frame, within_side=True)
    calibration = calibration if calibration is not None else pd.DataFrame(columns=CALIBRATION_COLUMNS)
    side_specific = calibration[
        (calibration.get("bucket_type", pd.Series(dtype=object)).astype(str).eq("side_specific_rank_decile"))
        & (calibration.get("side", pd.Series(dtype=object)).astype(str).isin(["Long", "Short"]))
    ].copy() if not calibration.empty else pd.DataFrame(columns=CALIBRATION_COLUMNS)
    calibration_versions = set(calibration.get("model_version", pd.Series(dtype=object)).dropna().astype(str).unique().tolist()) if not calibration.empty else set()
    rows: list[dict[str, Any]] = []
    for idx, row in frame.iterrows():
        source, quality, issue = infer_expected_return_source(row)
        side = _text(row.get("side"))
        pct = _num(rank_pct.loc[idx]) if idx in rank_pct.index else None
        model_version = _candidate_model_version(row)
        failure = ""
        matched = ""
        matches = pd.DataFrame()
        if calibration.empty:
            failure = "calibration_file_missing"
        elif not model_version:
            failure = "missing_model_version"
        elif calibration_versions and model_version not in calibration_versions:
            failure = "model_version_not_found"
        elif not side:
            failure = "side_not_found"
        elif pct is None:
            failure = "candidate_rank_missing"
        else:
            scoped = side_specific[side_specific["side"].astype(str).eq(side)]
            if scoped.empty:
                failure = "side_not_found"
            else:
                matches = scoped[
                    (pd.to_numeric(scoped["bucket_min_rank_pct"], errors="coerce") <= pct)
                    & (pd.to_numeric(scoped["bucket_max_rank_pct"], errors="coerce") >= pct)
                ]
                if matches.empty:
                    failure = "rank_bucket_not_found"
                else:
                    match = matches.sort_values("sample_count", ascending=False).iloc[0]
                    matched = _text(match.get("bucket_id"))
                    if _text(match.get("calibration_quality")) != "usable":
                        failure = "calibration_quality_insufficient"
        rows.append(
            {
                "section": "candidate_mapping",
                "record_type": "candidate",
                "name": _text(row.get("symbol")) or _text(row.get("ticker")),
                "symbol": _text(row.get("symbol")) or _text(row.get("ticker")),
                "side": side,
                "model_version": model_version,
                "model_score": row.get("model_score", pd.NA),
                "rank_pct": pct if pct is not None else pd.NA,
                "candidate_date": _candidate_date(row),
                "matched_calibration_bucket": matched,
                "match_failure_reason": failure,
                "expected_return_source": source,
                "expected_return_quality": quality,
                "expected_return_issue": issue,
            }
        )
    return pd.DataFrame(rows)


def _root_cause(input_rows: pd.DataFrame, calibration: pd.DataFrame, mapping: pd.DataFrame, validation_rows_used: int) -> str:
    if validation_rows_used == 0:
        return "missing_forward_return_labels"
    if calibration.empty:
        return "bucket_construction_failed"
    if int(calibration["calibration_quality"].eq("usable").sum()) == 0:
        return "insufficient_bucket_sample_count"
    if not mapping.empty and mapping["match_failure_reason"].fillna("").astype(str).ne("").all():
        failures = mapping["match_failure_reason"].fillna("").astype(str).value_counts()
        return failures.index[0] if not failures.empty else "candidate_mapping_failed"
    return "calibration_available"


def _recommended_fix(root_cause: str) -> str:
    if root_cause == "missing_forward_return_labels":
        return "Generate or persist out-of-sample forward-return labels in walk-forward validation outputs before building buckets."
    if root_cause == "insufficient_bucket_sample_count":
        return "Increase validation sample coverage per side/rank bucket or lower only the diagnostic bucket granularity, not trading gates."
    if root_cause == "model_version_not_found":
        return "Align candidate model_version with the validation calibration model_version."
    if root_cause in {"side_not_found", "rank_bucket_not_found", "candidate_rank_missing"}:
        return "Persist side and rank percentile fields from model output through the candidate pool."
    return "Inspect calibration coverage debug rows for the first non-empty failure section."


def build_calibration_coverage_debug(
    *,
    root: Path | str | None = None,
    candidates: pd.DataFrame | None = None,
    validation_frame: pd.DataFrame | None = None,
    calibration: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    base = Path(root) if root else PROJECT_ROOT
    input_rows, frames = locate_validation_inputs(base)
    if validation_frame is None:
        validation_frame = _best_validation_frame(frames)
    if calibration is None:
        calibration = frames.get("expected_return_bucket_calibration_latest", pd.DataFrame())
        if calibration.empty:
            calibration, _ = build_validation_bucket_calibration(validation_frame)
    prepared = prepare_validation_rows(validation_frame)
    rebuilt_calibration, validation_rows_used = build_validation_bucket_calibration(validation_frame)
    effective_calibration = calibration if calibration is not None and not calibration.empty else rebuilt_calibration
    bucket_rows = _bucket_debug_rows(effective_calibration)
    if candidates is None:
        candidate_path = _latest_file(base / "data" / "portal_outputs", ["08_alpaca_paper_candidate_pool_*.csv"])
        candidates = _safe_read(candidate_path)
    mapping_rows = candidate_mapping_debug(candidates, effective_calibration)
    cause = _root_cause(input_rows, effective_calibration, mapping_rows, validation_rows_used)
    summary = {
        "validation_inputs_found": int(input_rows["exists"].fillna(False).sum()) if not input_rows.empty else 0,
        "forward_label_coverage": {column: _coverage(validation_frame, column) for column in FORWARD_LABEL_COLUMNS},
        "validation_rows_used": validation_rows_used,
        "bucket_count": int(len(effective_calibration)),
        "usable_bucket_count": int(effective_calibration["calibration_quality"].eq("usable").sum()) if not effective_calibration.empty else 0,
        "candidate_mapping_coverage": int(mapping_rows["match_failure_reason"].fillna("").eq("").sum()) if not mapping_rows.empty else 0,
        "root_cause": cause,
        "recommended_next_fix": _recommended_fix(cause),
    }
    root_row = pd.DataFrame(
        [
            {
                "section": "summary",
                "record_type": "root_cause",
                "name": "root_cause",
                "root_cause": cause,
                "insufficient_data_reason": summary["recommended_next_fix"],
            }
        ]
    )
    debug = pd.concat([input_rows, bucket_rows, mapping_rows, root_row], ignore_index=True, sort=False)
    return debug.reindex(columns=DEBUG_COLUMNS), summary


def _render_summary(summary: dict[str, Any], debug: pd.DataFrame) -> str:
    lines = [
        "# Calibration Coverage Debug",
        "",
        f"- validation_inputs_found: {summary.get('validation_inputs_found', 0)}",
        f"- validation_rows_used: {summary.get('validation_rows_used', 0)}",
        f"- bucket_count: {summary.get('bucket_count', 0)}",
        f"- usable_bucket_count: {summary.get('usable_bucket_count', 0)}",
        f"- candidate_mapping_coverage: {summary.get('candidate_mapping_coverage', 0)}",
        f"- root_cause: {summary.get('root_cause', '')}",
        f"- recommended_next_fix: {summary.get('recommended_next_fix', '')}",
        "",
        "## Forward Label Coverage",
        "",
    ]
    for column, coverage in summary.get("forward_label_coverage", {}).items():
        lines.append(f"- {column}: {coverage:.4f}")
    if not debug.empty and "match_failure_reason" in debug.columns:
        failures = debug.loc[debug["section"].eq("candidate_mapping"), "match_failure_reason"].fillna("").astype(str).value_counts()
        if not failures.empty:
            lines.extend(["", "## Candidate Mapping Failures", ""])
            for reason, count in failures.to_dict().items():
                lines.append(f"- {reason or 'mapped'}: {count}")
    return "\n".join(lines) + "\n"


def write_calibration_coverage_debug(
    *,
    root: Path | str | None = None,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
) -> CalibrationCoverageDebugOutputs:
    base = Path(root) if root else PROJECT_ROOT
    out_dir = Path(output_dir) if output_dir else base / "data" / "model_outputs" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    debug, summary = build_calibration_coverage_debug(root=base)
    diagnostic_path = out_dir / f"calibration_coverage_debug_{run_stamp}.csv"
    summary_path = out_dir / f"calibration_coverage_debug_summary_{run_stamp}.md"
    debug.to_csv(diagnostic_path, index=False)
    summary_path.write_text(_render_summary(summary, debug), encoding="utf-8")
    return CalibrationCoverageDebugOutputs(
        diagnostic_path=diagnostic_path,
        summary_path=summary_path,
        validation_inputs_found=int(summary["validation_inputs_found"]),
        forward_label_coverage=dict(summary["forward_label_coverage"]),
        bucket_count=int(summary["bucket_count"]),
        usable_bucket_count=int(summary["usable_bucket_count"]),
        candidate_mapping_coverage=int(summary["candidate_mapping_coverage"]),
        root_cause=str(summary["root_cause"]),
        recommended_next_fix=str(summary["recommended_next_fix"]),
    )
