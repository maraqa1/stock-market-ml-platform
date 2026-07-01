from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import math
import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp

DIAGNOSTIC_COLUMNS = [
    "symbol",
    "side",
    "candidate_rank",
    "rank_bucket",
    "model_score",
    "expected_trade_return",
    "risk_adjusted_score",
    "realised_forward_return",
    "validation_bucket_realised_return",
    "expected_return_source",
    "expected_return_quality",
    "expected_return_issue",
    "validated_expected_return_bps",
    "validated_hit_rate",
    "validated_avg_gain",
    "validated_avg_loss",
    "execution_allowed",
    "execution_block_reason",
]

SUMMARY_COLUMNS = ["metric", "value"]


@dataclass(frozen=True)
class ExpectedReturnCalibrationOutputs:
    diagnostic_path: Path
    summary_path: Path
    rows: int
    unrealistic_rows: int
    calibrated_rows: int
    executable_rows: int


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


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def _close(a: float | None, b: float | None, *, rel: float = 1e-6, abs_tol: float = 1e-9) -> bool:
    return _finite(a) and _finite(b) and math.isclose(float(a), float(b), rel_tol=rel, abs_tol=abs_tol)


def rank_bucket(series: pd.Series) -> pd.Series:
    ranks = pd.to_numeric(series, errors="coerce")
    if ranks.notna().sum() == 0:
        return pd.Series(["unknown"] * len(series), index=series.index)
    pct = ranks.rank(method="first", ascending=True, pct=True)
    buckets = (pct.mul(10).apply(math.ceil).clip(1, 10)).astype("Int64")
    return buckets.map(lambda x: f"D{int(x):02d}" if pd.notna(x) else "unknown")


def _forward_return(row: pd.Series) -> float | None:
    for column in [
        "realised_forward_return",
        "realized_forward_return",
        "forward_5d_return",
        "forward_10d_return",
        "forward_20d_return",
        "target_forward_return",
    ]:
        if column in row.index:
            value = _num(row.get(column))
            if value is not None:
                return value
    return None


def infer_expected_return_source(row: pd.Series) -> tuple[str, str, str]:
    expected = _num(row.get("expected_trade_return"))
    model = _num(row.get("model_score"))
    risk = _num(row.get("risk_adjusted_score"))
    if expected is None:
        return "unknown", "invalid", "missing"
    if not math.isfinite(expected):
        return "unknown", "invalid", "infinite"

    forward = _forward_return(row)
    if _close(expected, forward):
        return "forward_realised_return_leakage", "invalid", "matches_forward_return"

    if abs(expected) > 0.20:
        if _close(expected, model) or _close(expected, risk):
            return "raw_model_score", "invalid", "score_scale_not_return_scale"
        if _finite(model) and abs(float(model)) > 0 and any(_close(expected, float(model) * factor, rel=1e-4, abs_tol=1e-6) for factor in [2, 10, 100, 1000, 10000]):
            return "transformed_model_score", "invalid", "score_transform_not_return_scale"
        if _finite(risk) and abs(float(risk)) > 0 and any(_close(expected, float(risk) * factor, rel=1e-4, abs_tol=1e-6) for factor in [2, 10, 100, 1000, 10000]):
            return "transformed_model_score", "invalid", "score_transform_not_return_scale"
        if abs(expected) > 2000:
            return "unknown", "invalid", "extreme_return_gt_2000"
        return "unit_ambiguous", "uncalibrated", "outside_return_bounds"

    if abs(expected) > 0.02 and abs(expected) <= 20:
        return "percent_or_ratio", "uncalibrated", "bps_percent_ambiguous" if abs(expected) > 1 else "requires_bucket_validation"
    return "percent", "usable", "within_return_bounds"


def _validation_columns(frame: pd.DataFrame) -> dict[str, str]:
    cols = {c.lower(): c for c in frame.columns}
    out: dict[str, str] = {}
    for key, options in {
        "rank_bucket": ["rank_bucket", "bucket", "decile", "confidence_bucket"],
        "return": ["validated_expected_return_bps", "realized_return_bps", "mean_return_bps", "avg_return_bps", "mean_realized_move_bps"],
        "hit_rate": ["validated_hit_rate", "hit_rate", "win_rate"],
        "avg_gain": ["validated_avg_gain", "avg_gain", "mean_gain_bps"],
        "avg_loss": ["validated_avg_loss", "avg_loss", "mean_loss_bps"],
    }.items():
        for option in options:
            if option in cols:
                out[key] = cols[option]
                break
    return out


def _normalise_validation(validation: pd.DataFrame | None) -> pd.DataFrame:
    if validation is None or validation.empty:
        return pd.DataFrame(columns=["rank_bucket", "validated_expected_return_bps", "validated_hit_rate", "validated_avg_gain", "validated_avg_loss"])
    mapping = _validation_columns(validation)
    if "rank_bucket" not in mapping:
        return pd.DataFrame(columns=["rank_bucket", "validated_expected_return_bps", "validated_hit_rate", "validated_avg_gain", "validated_avg_loss"])
    out = pd.DataFrame()
    out["rank_bucket"] = validation[mapping["rank_bucket"]].map(lambda v: f"D{int(v):02d}" if str(v).strip().isdigit() else str(v))
    out["validated_expected_return_bps"] = pd.to_numeric(validation[mapping.get("return", "")], errors="coerce") if mapping.get("return") else pd.NA
    out["validated_hit_rate"] = pd.to_numeric(validation[mapping.get("hit_rate", "")], errors="coerce") if mapping.get("hit_rate") else pd.NA
    out["validated_avg_gain"] = pd.to_numeric(validation[mapping.get("avg_gain", "")], errors="coerce") if mapping.get("avg_gain") else pd.NA
    out["validated_avg_loss"] = pd.to_numeric(validation[mapping.get("avg_loss", "")], errors="coerce") if mapping.get("avg_loss") else pd.NA
    return out.drop_duplicates("rank_bucket", keep="last")


def build_expected_return_calibration(candidates: pd.DataFrame, validation: pd.DataFrame | None = None) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=DIAGNOSTIC_COLUMNS)
    frame = candidates.copy()
    symbol_col = "symbol" if "symbol" in frame.columns else "ticker" if "ticker" in frame.columns else None
    frame["symbol"] = frame[symbol_col].astype(str).str.upper() if symbol_col else ""
    if "candidate_rank" not in frame.columns:
        frame["candidate_rank"] = frame.get("rank_overall", pd.Series(range(1, len(frame) + 1), index=frame.index))
    frame["rank_bucket"] = rank_bucket(frame["candidate_rank"])
    classified = frame.apply(lambda row: infer_expected_return_source(row), axis=1)
    frame["expected_return_source"] = [item[0] for item in classified]
    frame["expected_return_quality"] = [item[1] for item in classified]
    frame["expected_return_issue"] = [item[2] for item in classified]

    validation_norm = _normalise_validation(validation)
    if not validation_norm.empty:
        frame = frame.merge(validation_norm, on="rank_bucket", how="left")
        has_validation = pd.to_numeric(frame["validated_expected_return_bps"], errors="coerce").notna()
        frame.loc[has_validation & frame["expected_return_quality"].isin(["uncalibrated", "invalid"]), "expected_return_quality"] = "calibrated"
        frame.loc[has_validation, "expected_return_source"] = frame.loc[has_validation, "expected_return_source"].where(
            frame.loc[has_validation, "expected_return_source"].eq("forward_realised_return_leakage"),
            "historical_bucket_return",
        )
        frame.loc[has_validation, "expected_return_issue"] = "validated_by_rank_bucket"
    else:
        for column in ["validated_expected_return_bps", "validated_hit_rate", "validated_avg_gain", "validated_avg_loss"]:
            frame[column] = pd.NA

    forward = frame.apply(_forward_return, axis=1)
    frame["realised_forward_return"] = forward
    executable = frame["expected_return_quality"].isin(["usable", "calibrated"])
    frame["execution_allowed"] = executable
    frame["execution_block_reason"] = executable.map(lambda ok: "" if ok else "expected_return_uncalibrated")
    return frame.reindex(columns=DIAGNOSTIC_COLUMNS)


def apply_expected_return_execution_safety(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    out = frame.copy()
    diag = build_expected_return_calibration(out)
    quality = diag["expected_return_quality"].reindex(out.index)
    block = ~quality.isin(["usable", "calibrated"])
    out["expected_return_quality"] = quality
    out["expected_return_source"] = diag["expected_return_source"].reindex(out.index)
    out["expected_return_issue"] = diag["expected_return_issue"].reindex(out.index)
    if "trade_quality_status" in out.columns:
        out.loc[block, "trade_quality_status"] = "rejected"
    if "order_eligible" in out.columns:
        out.loc[block, "order_eligible"] = False
    reason_col = "trade_quality_reason" if "trade_quality_reason" in out.columns else "reject_reason" if "reject_reason" in out.columns else None
    if reason_col:
        current = out.get(reason_col, pd.Series("", index=out.index)).fillna("").astype(str)
        suffix = current.where(current.eq(""), current + "|") + "expected_return_uncalibrated"
        out.loc[block, reason_col] = suffix.loc[block]
    return out


def expected_return_safety_reason(row: pd.Series) -> str:
    _, quality, _ = infer_expected_return_source(row)
    return "" if quality in {"usable", "calibrated"} else "expected_return_uncalibrated"


def latest_csv(directory: Path, pattern: str) -> Path | None:
    files = [p for p in directory.glob(pattern) if p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def load_latest_candidate_pool(root: Path | str | None = None) -> tuple[Path | None, pd.DataFrame]:
    base = Path(root) if root else PROJECT_ROOT
    path = latest_csv(base / "data" / "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
    if path is None:
        return None, pd.DataFrame()
    return path, pd.read_csv(path, low_memory=False)


def load_latest_validation(root: Path | str | None = None) -> tuple[Path | None, pd.DataFrame]:
    base = Path(root) if root else PROJECT_ROOT
    out_dir = base / "data" / "model_outputs"
    patterns = [
        "advanced_model_confidence_bucket_performance_*.csv",
        "meta_label_bucket_performance_*.csv",
        "*bucket_performance*.csv",
    ]
    files: list[Path] = []
    for pattern in patterns:
        files.extend([p for p in out_dir.glob(pattern) if p.is_file()])
    if not files:
        return None, pd.DataFrame()
    path = max(files, key=lambda p: p.stat().st_mtime)
    return path, pd.read_csv(path, low_memory=False)


def _render_summary(report: pd.DataFrame, *, candidate_path: Path | None, validation_path: Path | None) -> str:
    rows = len(report)
    invalid = int(report["expected_return_quality"].isin(["invalid", "uncalibrated"]).sum()) if rows else 0
    calibrated = int(report["expected_return_quality"].eq("calibrated").sum()) if rows else 0
    executable = int(report["execution_allowed"].fillna(False).sum()) if rows else 0
    lines = [
        "# Expected Return Calibration Diagnostic",
        "",
        f"- candidate_file: {candidate_path or ''}",
        f"- validation_file: {validation_path or ''}",
        f"- rows: {rows}",
        f"- invalid_or_uncalibrated_rows: {invalid}",
        f"- calibrated_rows: {calibrated}",
        f"- executable_rows_after_expected_return_safety: {executable}",
        f"- expected_trade_return_safe_for_execution: {'yes' if invalid == 0 and rows > 0 else 'no'}",
        "",
        "## Quality Counts",
        "",
    ]
    if rows:
        for key, value in report["expected_return_quality"].value_counts(dropna=False).to_dict().items():
            lines.append(f"- {key}: {value}")
        lines.extend(["", "## Source Counts", ""])
        for key, value in report["expected_return_source"].value_counts(dropna=False).to_dict().items():
            lines.append(f"- {key}: {value}")
    return "\n".join(lines) + "\n"


def write_expected_return_calibration(
    candidates: pd.DataFrame,
    validation: pd.DataFrame | None = None,
    *,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
    candidate_path: Path | None = None,
    validation_path: Path | None = None,
) -> ExpectedReturnCalibrationOutputs:
    out_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "data" / "model_outputs" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    report = build_expected_return_calibration(candidates, validation)
    diagnostic_path = out_dir / f"expected_return_calibration_{run_stamp}.csv"
    summary_path = out_dir / f"expected_return_calibration_summary_{run_stamp}.md"
    report.to_csv(diagnostic_path, index=False)
    summary_path.write_text(_render_summary(report, candidate_path=candidate_path, validation_path=validation_path), encoding="utf-8")
    unrealistic = int(report["expected_return_quality"].isin(["invalid", "uncalibrated"]).sum()) if not report.empty else 0
    calibrated = int(report["expected_return_quality"].eq("calibrated").sum()) if not report.empty else 0
    executable = int(report["execution_allowed"].fillna(False).sum()) if not report.empty else 0
    return ExpectedReturnCalibrationOutputs(diagnostic_path, summary_path, len(report), unrealistic, calibrated, executable)


def build_latest_expected_return_calibration(root: Path | str | None = None, *, output_dir: Path | str | None = None) -> ExpectedReturnCalibrationOutputs:
    candidate_path, candidates = load_latest_candidate_pool(root)
    validation_path, validation = load_latest_validation(root)
    return write_expected_return_calibration(
        candidates,
        validation,
        output_dir=output_dir,
        candidate_path=candidate_path,
        validation_path=validation_path,
    )
