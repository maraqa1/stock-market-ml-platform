from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp


CALIBRATION_COLUMNS = [
    "calibration_id",
    "built_at",
    "model_version",
    "horizon",
    "side",
    "bucket_type",
    "bucket_id",
    "bucket_min_score",
    "bucket_max_score",
    "bucket_min_rank_pct",
    "bucket_max_rank_pct",
    "sample_count",
    "win_count",
    "loss_count",
    "hit_rate",
    "avg_forward_return_bps",
    "median_forward_return_bps",
    "avg_win_bps",
    "avg_loss_bps",
    "profit_factor",
    "estimated_spread_cost_bps",
    "estimated_slippage_bps",
    "net_expected_return_bps",
    "confidence_level",
    "calibration_quality",
    "insufficient_data_reason",
]

MAPPING_COLUMNS = [
    "calibrated_bucket_id",
    "validated_expected_return_bps",
    "validated_hit_rate",
    "validated_profit_factor",
    "calibration_quality",
    "expected_return_quality",
    "calibration_sample_count",
    "execution_block_reason",
]


@dataclass(frozen=True)
class ValidationBucketCalibrationOutputs:
    calibration_path: Path
    latest_path: Path
    summary_path: Path
    validation_rows_used: int
    buckets_built: int
    usable_buckets: int
    weak_buckets: int
    insufficient_buckets: int


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
        if pd.isna(parsed) or math.isinf(parsed):
            return None
        return parsed
    except Exception:
        return None


def _series_num(frame: pd.DataFrame, column: str, default: float | None = None) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _side(value: Any) -> str:
    text = _text(value).lower().replace("_", " ")
    if text in {"long", "buy", "trade buy", "strong trade buy"} or "long" in text:
        return "Long"
    if text in {"short", "sell"} or "short" in text:
        return "Short"
    return ""


def _return_bps(value: Any) -> float | None:
    number = _num(value)
    if number is None:
        return None
    return number if abs(number) > 2 else number * 10000.0


def _forward_return_column(frame: pd.DataFrame) -> str | None:
    for column in [
        "realised_forward_return",
        "realized_forward_return",
        "forward_5d_return",
        "forward_10d_return",
        "forward_20d_return",
        "target_forward_return",
        "future_price_return",
    ]:
        if column in frame.columns:
            return column
    return None


def _score_column(frame: pd.DataFrame) -> str | None:
    for column in ["model_score", "risk_adjusted_score", "raw_score", "score", "confidence_score"]:
        if column in frame.columns:
            return column
    return None


def _model_version(frame: pd.DataFrame, fallback: str = "unknown") -> str:
    for column in ["model_version", "model_id", "model_name"]:
        if column in frame.columns:
            values = frame[column].dropna().astype(str)
            if not values.empty:
                return values.iloc[-1]
    return fallback


def _rank_percentile(frame: pd.DataFrame, *, within_side: bool = False) -> pd.Series:
    if "predicted_rank_pct" in frame.columns:
        return pd.to_numeric(frame["predicted_rank_pct"], errors="coerce").clip(0, 1)
    rank_col = "candidate_rank" if "candidate_rank" in frame.columns else "rank_overall" if "rank_overall" in frame.columns else None
    if rank_col is not None:
        ranks = pd.to_numeric(frame[rank_col], errors="coerce")
        if within_side and "side" in frame.columns:
            return ranks.groupby(frame["side"]).rank(method="first", pct=True).clip(0, 1)
        return ranks.rank(method="first", pct=True).clip(0, 1)
    score_col = _score_column(frame)
    if score_col is None:
        return pd.Series(pd.NA, index=frame.index, dtype="float64")
    scores = pd.to_numeric(frame[score_col], errors="coerce")
    if within_side and "side" in frame.columns:
        return scores.groupby(frame["side"]).rank(method="first", ascending=False, pct=True).clip(0, 1)
    return scores.rank(method="first", ascending=False, pct=True).clip(0, 1)


def _bucket_id_from_pct(pct: pd.Series) -> pd.Series:
    values = pd.to_numeric(pct, errors="coerce")
    buckets = (values.mul(10).apply(lambda value: math.ceil(value) if pd.notna(value) else pd.NA).clip(1, 10)).astype("Int64")
    return buckets.map(lambda value: f"D{int(value):02d}" if pd.notna(value) else "unknown")


def prepare_validation_rows(
    predictions: pd.DataFrame,
    *,
    estimated_spread_cost_bps: float = 0.0,
    estimated_slippage_bps: float = 5.0,
    borrow_cost_estimate_bps: float = 0.0,
) -> pd.DataFrame:
    if predictions is None or predictions.empty:
        return pd.DataFrame()
    forward_col = _forward_return_column(predictions)
    if forward_col is None:
        return pd.DataFrame()
    frame = predictions.copy()
    frame["side"] = frame.get("side", frame.get("trade_action", "")).map(_side)
    frame = frame[frame["side"].isin(["Long", "Short"])].copy()
    if frame.empty:
        return frame
    if "is_out_of_sample" in frame.columns:
        frame = frame[frame["is_out_of_sample"].astype(str).str.lower().isin(["true", "1", "yes"])].copy()
    if "split" in frame.columns:
        frame = frame[~frame["split"].astype(str).str.lower().isin(["train", "training", "in_sample", "insample"])].copy()
    raw_forward = frame[forward_col].map(_return_bps)
    frame = frame[raw_forward.notna()].copy()
    raw_forward = raw_forward.loc[frame.index]
    spread = _series_num(frame, "spread_bps", estimated_spread_cost_bps).fillna(estimated_spread_cost_bps)
    slippage = _series_num(frame, "slippage_bps", estimated_slippage_bps).fillna(estimated_slippage_bps)
    borrow = _series_num(frame, "borrow_cost_estimate_bps", borrow_cost_estimate_bps).fillna(borrow_cost_estimate_bps)
    frame["estimated_spread_cost_bps"] = spread.clip(lower=0)
    frame["estimated_slippage_bps"] = slippage.clip(lower=0)
    frame["future_price_return_bps"] = raw_forward.astype(float)
    long_net = frame["future_price_return_bps"] - frame["estimated_spread_cost_bps"] - frame["estimated_slippage_bps"]
    short_net = -frame["future_price_return_bps"] - frame["estimated_spread_cost_bps"] - frame["estimated_slippage_bps"] - borrow.clip(lower=0)
    frame["forward_return_bps"] = long_net.where(frame["side"].eq("Long"), short_net)
    score_col = _score_column(frame)
    frame["model_score_for_bucket"] = pd.to_numeric(frame[score_col], errors="coerce") if score_col else pd.NA
    frame["rank_pct"] = _rank_percentile(frame)
    frame["side_rank_pct"] = _rank_percentile(frame, within_side=True)
    return frame


def _bucket_quality(sample_count: int) -> tuple[str, str, str]:
    if sample_count < 30:
        return "insufficient_data", "low", "sample_count_below_30"
    if sample_count < 100:
        return "weak", "medium", ""
    return "usable", "high", ""


def _build_bucket_rows(frame: pd.DataFrame, *, bucket_type: str, side: str, built_at: str, model_version: str, horizon: str) -> list[dict[str, Any]]:
    if side == "combined":
        scoped = frame.copy()
    else:
        scoped = frame[frame["side"].eq(side)].copy()
    if scoped.empty:
        return []
    if bucket_type == "model_score_decile":
        bucket = _bucket_id_from_pct(scoped["model_score_for_bucket"].rank(method="first", ascending=False, pct=True))
    elif bucket_type == "rank_percentile_decile":
        bucket = _bucket_id_from_pct(scoped["rank_pct"])
    elif bucket_type == "side_specific_rank_decile":
        if side == "combined":
            return []
        bucket = _bucket_id_from_pct(scoped["side_rank_pct"])
    elif bucket_type == "sector_neutral_decile":
        if "sector" not in scoped.columns:
            return []
        sector_pct = scoped.groupby(scoped["sector"].fillna("unknown"))["model_score_for_bucket"].rank(method="first", ascending=False, pct=True)
        bucket = scoped["sector"].fillna("unknown").astype(str) + ":" + _bucket_id_from_pct(sector_pct).astype(str)
    else:
        return []
    scoped = scoped.assign(bucket_id=bucket)
    rows: list[dict[str, Any]] = []
    for bucket_id, group in scoped.groupby("bucket_id", dropna=False):
        returns = pd.to_numeric(group["forward_return_bps"], errors="coerce").dropna()
        sample_count = int(len(returns))
        wins = returns[returns > 0]
        losses = returns[returns < 0]
        quality, confidence, reason = _bucket_quality(sample_count)
        gross_win = float(wins.sum()) if not wins.empty else 0.0
        gross_loss = abs(float(losses.sum())) if not losses.empty else 0.0
        profit_factor = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
        rows.append(
            {
                "calibration_id": f"{model_version}:{horizon}:{side}:{bucket_type}:{bucket_id}",
                "built_at": built_at,
                "model_version": model_version,
                "horizon": horizon,
                "side": side,
                "bucket_type": bucket_type,
                "bucket_id": str(bucket_id),
                "bucket_min_score": group["model_score_for_bucket"].min(),
                "bucket_max_score": group["model_score_for_bucket"].max(),
                "bucket_min_rank_pct": group["side_rank_pct" if bucket_type == "side_specific_rank_decile" else "rank_pct"].min(),
                "bucket_max_rank_pct": group["side_rank_pct" if bucket_type == "side_specific_rank_decile" else "rank_pct"].max(),
                "sample_count": sample_count,
                "win_count": int((returns > 0).sum()),
                "loss_count": int((returns < 0).sum()),
                "hit_rate": float((returns > 0).mean()) if sample_count else 0.0,
                "avg_forward_return_bps": float(returns.mean()) if sample_count else 0.0,
                "median_forward_return_bps": float(returns.median()) if sample_count else 0.0,
                "avg_win_bps": float(wins.mean()) if not wins.empty else 0.0,
                "avg_loss_bps": float(losses.mean()) if not losses.empty else 0.0,
                "profit_factor": profit_factor,
                "estimated_spread_cost_bps": float(pd.to_numeric(group["estimated_spread_cost_bps"], errors="coerce").mean()),
                "estimated_slippage_bps": float(pd.to_numeric(group["estimated_slippage_bps"], errors="coerce").mean()),
                "net_expected_return_bps": float(returns.mean()) if sample_count else 0.0,
                "confidence_level": confidence,
                "calibration_quality": quality,
                "insufficient_data_reason": reason,
            }
        )
    return rows


def _flag_non_monotonic(calibration: pd.DataFrame) -> pd.DataFrame:
    if calibration.empty:
        return calibration
    out = calibration.copy()
    for (bucket_type, side), idx in out.groupby(["bucket_type", "side"]).groups.items():
        group = out.loc[list(idx)].copy()
        simple = group[group["bucket_id"].astype(str).str.match(r"^D\d{2}$")].sort_values("bucket_id")
        if len(simple) < 3:
            continue
        values = pd.to_numeric(simple["net_expected_return_bps"], errors="coerce")
        if values.isna().any():
            continue
        # D01 is intended to be strongest. Flag material reversals but do not
        # discard the bucket; diagnostics should expose this rather than hide it.
        non_monotonic = any(values.iloc[i] + 1e-9 < values.iloc[i + 1] for i in range(len(values) - 1))
        if non_monotonic:
            target = simple.index
            reasons = out.loc[target, "insufficient_data_reason"].fillna("").astype(str)
            out.loc[target, "insufficient_data_reason"] = reasons.map(lambda reason: "non_monotonic_bucket" if not reason else f"{reason}|non_monotonic_bucket")
    return out


def build_validation_bucket_calibration(
    predictions: pd.DataFrame,
    *,
    model_version: str | None = None,
    horizon: str = "5d",
    built_at: datetime | None = None,
    estimated_spread_cost_bps: float = 0.0,
    estimated_slippage_bps: float = 5.0,
    borrow_cost_estimate_bps: float = 0.0,
) -> tuple[pd.DataFrame, int]:
    prepared = prepare_validation_rows(
        predictions,
        estimated_spread_cost_bps=estimated_spread_cost_bps,
        estimated_slippage_bps=estimated_slippage_bps,
        borrow_cost_estimate_bps=borrow_cost_estimate_bps,
    )
    built = (built_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    version = model_version or _model_version(predictions)
    if prepared.empty:
        return pd.DataFrame(columns=CALIBRATION_COLUMNS), 0
    rows: list[dict[str, Any]] = []
    for side in ["Long", "Short", "combined"]:
        for bucket_type in ["model_score_decile", "rank_percentile_decile", "side_specific_rank_decile", "sector_neutral_decile"]:
            rows.extend(_build_bucket_rows(prepared, bucket_type=bucket_type, side=side, built_at=built, model_version=version, horizon=horizon))
    calibration = pd.DataFrame(rows, columns=CALIBRATION_COLUMNS)
    calibration = _flag_non_monotonic(calibration)
    return calibration.reindex(columns=CALIBRATION_COLUMNS), int(len(prepared))


def _candidate_rank_pct(candidates: pd.DataFrame, *, side_specific: bool = True) -> pd.Series:
    frame = candidates.copy()
    if "side" not in frame.columns:
        frame["side"] = frame.get("trade_action", "").map(_side)
    return _rank_percentile(frame, within_side=side_specific)


def map_candidates_to_calibration(
    candidates: pd.DataFrame,
    calibration: pd.DataFrame,
    *,
    bucket_type: str = "side_specific_rank_decile",
    weak_allowed_by_config: bool = False,
    min_sample_count: int = 100,
) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=MAPPING_COLUMNS)
    out = pd.DataFrame(index=candidates.index)
    for column in MAPPING_COLUMNS:
        out[column] = pd.Series([pd.NA] * len(candidates), index=candidates.index, dtype="object")
    out["calibration_sample_count"] = 0
    if calibration is None or calibration.empty:
        out["expected_return_quality"] = "invalid"
        out["execution_block_reason"] = "expected_return_uncalibrated"
        return out
    frame = candidates.copy()
    frame["side"] = frame.get("side", frame.get("trade_action", "")).map(_side)
    rank_pct = _candidate_rank_pct(frame, side_specific=True)
    usable_calibration = calibration[
        (calibration["bucket_type"].astype(str) == bucket_type)
        & (calibration["side"].astype(str).isin(["Long", "Short"]))
    ].copy()
    for idx, row in frame.iterrows():
        side = row.get("side")
        pct = _num(rank_pct.loc[idx])
        if not side or pct is None:
            out.loc[idx, "expected_return_quality"] = "invalid"
            out.loc[idx, "execution_block_reason"] = "expected_return_uncalibrated"
            continue
        matches = usable_calibration[
            (usable_calibration["side"].eq(side))
            & (pd.to_numeric(usable_calibration["bucket_min_rank_pct"], errors="coerce") <= pct)
            & (pd.to_numeric(usable_calibration["bucket_max_rank_pct"], errors="coerce") >= pct)
        ]
        if matches.empty:
            out.loc[idx, "expected_return_quality"] = "invalid"
            out.loc[idx, "execution_block_reason"] = "expected_return_uncalibrated"
            continue
        match = matches.sort_values("sample_count", ascending=False).iloc[0]
        quality = _text(match.get("calibration_quality"))
        sample_count = int(_num(match.get("sample_count")) or 0)
        usable = quality == "usable" and sample_count >= min_sample_count
        weak_allowed = weak_allowed_by_config and quality == "weak" and sample_count >= 30
        out.loc[idx, "calibrated_bucket_id"] = match.get("bucket_id", "")
        out.loc[idx, "validated_expected_return_bps"] = match.get("net_expected_return_bps", "")
        out.loc[idx, "validated_hit_rate"] = match.get("hit_rate", "")
        out.loc[idx, "validated_profit_factor"] = match.get("profit_factor", "")
        out.loc[idx, "calibration_quality"] = quality
        out.loc[idx, "calibration_sample_count"] = sample_count
        out.loc[idx, "expected_return_quality"] = "usable" if usable else "weak_allowed_by_config" if weak_allowed else "uncalibrated"
        out.loc[idx, "execution_block_reason"] = "" if usable or weak_allowed else "expected_return_uncalibrated"
    return out.reindex(columns=MAPPING_COLUMNS)


def latest_csv(directory: Path, patterns: list[str]) -> Path | None:
    files: list[Path] = []
    for pattern in patterns:
        files.extend([p for p in directory.glob(pattern) if p.is_file()])
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def load_latest_validation_predictions(root: Path | str | None = None) -> tuple[Path | None, pd.DataFrame]:
    base = Path(root) if root else PROJECT_ROOT
    path = latest_csv(
        base / "data" / "model_outputs",
        [
            "walk_forward_predictions_*.csv",
            "*walk_forward*predictions*.csv",
            "*validation_predictions*.csv",
            "advanced_model_latest_predictions_*.csv",
        ],
    )
    if path is None:
        return None, pd.DataFrame()
    return path, pd.read_csv(path, low_memory=False)


def write_validation_bucket_calibration(
    predictions: pd.DataFrame,
    *,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
    model_version: str | None = None,
    horizon: str = "5d",
) -> ValidationBucketCalibrationOutputs:
    out_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "data" / "model_outputs" / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    calibration, validation_rows = build_validation_bucket_calibration(predictions, model_version=model_version, horizon=horizon)
    calibration_path = out_dir / f"expected_return_bucket_calibration_{run_stamp}.csv"
    latest_path = out_dir / "expected_return_bucket_calibration_latest.csv"
    summary_path = out_dir / f"expected_return_bucket_calibration_summary_{run_stamp}.md"
    calibration.to_csv(calibration_path, index=False)
    shutil.copyfile(calibration_path, latest_path)
    usable = int(calibration["calibration_quality"].eq("usable").sum()) if not calibration.empty else 0
    weak = int(calibration["calibration_quality"].eq("weak").sum()) if not calibration.empty else 0
    insufficient = int(calibration["calibration_quality"].eq("insufficient_data").sum()) if not calibration.empty else 0
    summary_path.write_text(
        render_summary(
            calibration,
            validation_rows_used=validation_rows,
            usable_buckets=usable,
            weak_buckets=weak,
            insufficient_buckets=insufficient,
        ),
        encoding="utf-8",
    )
    return ValidationBucketCalibrationOutputs(calibration_path, latest_path, summary_path, validation_rows, len(calibration), usable, weak, insufficient)


def render_summary(
    calibration: pd.DataFrame,
    *,
    validation_rows_used: int,
    usable_buckets: int,
    weak_buckets: int,
    insufficient_buckets: int,
) -> str:
    lines = [
        "# Validation Bucket Expected Return Calibration",
        "",
        f"- validation_rows_used: {validation_rows_used}",
        f"- buckets_built: {len(calibration)}",
        f"- usable_buckets: {usable_buckets}",
        f"- weak_buckets: {weak_buckets}",
        f"- insufficient_buckets: {insufficient_buckets}",
        f"- live_candidates_can_be_safely_mapped: {'yes' if usable_buckets > 0 else 'no'}",
        f"- executable_safe_calibration_exists: {'yes' if usable_buckets > 0 else 'no'}",
        "",
        "## Side Performance",
        "",
    ]
    if calibration.empty:
        lines.append("No calibration buckets were built.")
    else:
        side_summary = calibration.groupby("side", dropna=False).agg(
            buckets=("bucket_id", "count"),
            usable=("calibration_quality", lambda s: int((s == "usable").sum())),
            mean_net_expected_return_bps=("net_expected_return_bps", "mean"),
        )
        lines.append(side_summary.reset_index().to_csv(index=False).strip())
        lines.extend(["", "## Monotonicity", ""])
        non_mono = calibration["insufficient_data_reason"].fillna("").astype(str).str.contains("non_monotonic_bucket").sum()
        lines.append(f"- non_monotonic_bucket_rows: {int(non_mono)}")
    if usable_buckets == 0:
        lines.extend(["", "## Warning", "", "No executable-safe calibration exists. Keep expected_return_uncalibrated active."])
    return "\n".join(lines) + "\n"


def build_latest_validation_bucket_calibration(root: Path | str | None = None, *, output_dir: Path | str | None = None) -> ValidationBucketCalibrationOutputs:
    _, predictions = load_latest_validation_predictions(root)
    return write_validation_bucket_calibration(predictions, output_dir=output_dir)
