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
    "calibration_source",
    "validation_warning",
    "model_version",
    "horizon_days",
    "side",
    "bucket_type",
    "bucket_id",
    "bucket_min_value",
    "bucket_max_value",
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
    "borrow_cost_estimate_bps",
    "net_expected_return_bps",
    "confidence_level",
    "calibration_quality",
    "insufficient_data_reason",
    "max_label_date_used",
    "excluded_recent_rows",
    "label_column_used",
    "gold_rows_read",
]

MAPPING_COLUMNS = [
    "calibrated_bucket_id",
    "validated_expected_return_bps",
    "validated_hit_rate",
    "validated_profit_factor",
    "calibration_source",
    "calibration_quality",
    "expected_return_quality",
    "calibration_sample_count",
    "execution_block_reason",
]

GOLD_TARGET_ALIASES = [
    "target_sector_relative_return_5d",
    "target_return_5d",
    "target_return_10d",
    "target_return_20d",
    "return_5d",
]

WALK_FORWARD_TARGET_ALIASES = [
    "realised_forward_return_bps",
    "realized_forward_return_bps",
    "realised_forward_return",
    "realized_forward_return",
    "forward_5d_return",
    "forward_10d_return",
    "forward_20d_return",
    "target_forward_return",
    "future_price_return",
    *GOLD_TARGET_ALIASES,
]

HISTORICAL_GOLD_WARNING = "historical_gold_fallback_not_true_walk_forward"


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
    calibration_source: str = "insufficient_data"
    gold_path: Path | None = None
    gold_rows_read: int = 0
    label_column_used: str = ""
    max_label_date_used: str = ""
    excluded_recent_rows: int = 0
    candidate_mapping_coverage: int = 0


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


def _choose_column(frame: pd.DataFrame, options: list[str]) -> str | None:
    for column in options:
        if column in frame.columns:
            return column
    return None


def _forward_return_column(frame: pd.DataFrame) -> str | None:
    return _choose_column(frame, WALK_FORWARD_TARGET_ALIASES)


def _gold_target_column(frame: pd.DataFrame) -> str | None:
    return _choose_column(frame, GOLD_TARGET_ALIASES)


def _score_column(frame: pd.DataFrame) -> str | None:
    return _choose_column(frame, ["model_score", "risk_adjusted_score", "raw_score", "score", "confidence_score", "selection_score"])


def _model_version(frame: pd.DataFrame, fallback: str = "unknown") -> str:
    for column in ["model_version", "model_id", "model_name"]:
        if column in frame.columns:
            values = frame[column].dropna().astype(str)
            if not values.empty:
                return values.iloc[-1]
    return fallback


def _rank_percentile(frame: pd.DataFrame, *, within_side: bool = False) -> pd.Series:
    for column in ["predicted_rank_pct_by_date", "predicted_rank_pct", "target_rank_pct_5d"]:
        if column in frame.columns:
            return pd.to_numeric(frame[column], errors="coerce").clip(0, 1)
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


def _horizon_days(horizon: str | int = "5d") -> int:
    if isinstance(horizon, int):
        return horizon
    digits = "".join(ch for ch in str(horizon) if ch.isdigit())
    return int(digits or 5)


def _bucket_quality(sample_count: int) -> tuple[str, str, str]:
    if sample_count < 30:
        return "insufficient_data", "low", "sample_count_below_30"
    if sample_count < 100:
        return "weak", "medium", ""
    return "usable", "high", ""


def _is_valid_validation_frame(frame: pd.DataFrame) -> bool:
    return not prepare_validation_rows(frame).empty


def prepare_validation_rows(
    predictions: pd.DataFrame,
    *,
    estimated_spread_cost_bps: float = 0.0,
    estimated_slippage_bps: float = 5.0,
    borrow_cost_estimate_bps: float = 0.0,
    calibration_source: str = "walk_forward_validation",
    label_column: str | None = None,
    horizon_days: int = 5,
    max_label_date_used: str = "",
    excluded_recent_rows: int = 0,
    gold_rows_read: int = 0,
) -> pd.DataFrame:
    if predictions is None or predictions.empty:
        return pd.DataFrame()
    forward_col = label_column or _forward_return_column(predictions)
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
    if frame.empty:
        return frame
    raw_forward = raw_forward.loc[frame.index]
    spread = _series_num(frame, "spread_bps", estimated_spread_cost_bps).fillna(estimated_spread_cost_bps)
    slippage = _series_num(frame, "slippage_bps", estimated_slippage_bps).fillna(estimated_slippage_bps)
    borrow = _series_num(frame, "borrow_cost_estimate_bps", borrow_cost_estimate_bps).fillna(borrow_cost_estimate_bps)
    frame["estimated_spread_cost_bps"] = spread.clip(lower=0)
    frame["estimated_slippage_bps"] = slippage.clip(lower=0)
    frame["borrow_cost_estimate_bps"] = borrow.clip(lower=0)
    frame["future_price_return_bps"] = raw_forward.astype(float)
    long_net = frame["future_price_return_bps"] - frame["estimated_spread_cost_bps"] - frame["estimated_slippage_bps"]
    short_net = -frame["future_price_return_bps"] - frame["estimated_spread_cost_bps"] - frame["estimated_slippage_bps"] - frame["borrow_cost_estimate_bps"]
    frame["forward_return_bps"] = long_net.where(frame["side"].eq("Long"), short_net)
    score_col = _score_column(frame)
    frame["model_score_for_bucket"] = pd.to_numeric(frame[score_col], errors="coerce") if score_col else pd.NA
    frame["rank_pct"] = _rank_percentile(frame)
    frame["side_rank_pct"] = _rank_percentile(frame, within_side=True)
    frame["calibration_source"] = calibration_source
    frame["validation_warning"] = HISTORICAL_GOLD_WARNING if calibration_source == "gold_historical_targets" else ""
    frame["label_column_used"] = forward_col
    frame["horizon_days"] = horizon_days
    frame["max_label_date_used"] = max_label_date_used
    frame["excluded_recent_rows"] = excluded_recent_rows
    frame["gold_rows_read"] = gold_rows_read
    return frame


def prepare_gold_historical_rows(
    gold: pd.DataFrame,
    *,
    horizon_days: int = 5,
    estimated_spread_cost_bps: float = 0.0,
    estimated_slippage_bps: float = 5.0,
    borrow_cost_estimate_bps: float = 0.0,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if gold is None or gold.empty:
        return pd.DataFrame(), {
            "label_column_used": "",
            "max_label_date_used": "",
            "excluded_recent_rows": 0,
            "gold_rows_read": 0,
        }
    label_col = _gold_target_column(gold)
    if label_col is None:
        return pd.DataFrame(), {
            "label_column_used": "",
            "max_label_date_used": "",
            "excluded_recent_rows": 0,
            "gold_rows_read": int(len(gold)),
        }
    frame = gold.copy()
    frame["date"] = pd.to_datetime(frame.get("date"), errors="coerce")
    frame = frame[frame["date"].notna()].copy()
    if frame.empty:
        return pd.DataFrame(), {"label_column_used": label_col, "max_label_date_used": "", "excluded_recent_rows": 0, "gold_rows_read": int(len(gold))}
    trading_dates = sorted(frame["date"].dropna().dt.normalize().unique().tolist())
    excluded_dates = set(trading_dates[-horizon_days:]) if len(trading_dates) > horizon_days else set(trading_dates)
    excluded_recent = int(frame["date"].dt.normalize().isin(excluded_dates).sum())
    frame = frame[~frame["date"].dt.normalize().isin(excluded_dates)].copy()
    max_label_date = frame["date"].max().date().isoformat() if not frame.empty else ""
    target = pd.to_numeric(frame[label_col], errors="coerce")
    frame = frame[target.notna()].copy()
    target = target.loc[frame.index]
    if frame.empty:
        return pd.DataFrame(), {
            "label_column_used": label_col,
            "max_label_date_used": max_label_date,
            "excluded_recent_rows": excluded_recent,
            "gold_rows_read": int(len(gold)),
        }
    base_cols = [c for c in ["date", "ticker", "sector", "model_score", "risk_adjusted_score", "selection_score", "rank_overall", "candidate_rank", "predicted_rank_pct_by_date", "target_rank_pct_5d", "spread_bps"] if c in frame.columns]
    long_frame = frame[base_cols].copy()
    short_frame = frame[base_cols].copy()
    long_frame["side"] = "Long"
    short_frame["side"] = "Short"
    long_frame[label_col] = target
    short_frame[label_col] = target
    doubled = pd.concat([long_frame, short_frame], ignore_index=True)
    prepared = prepare_validation_rows(
        doubled,
        estimated_spread_cost_bps=estimated_spread_cost_bps,
        estimated_slippage_bps=estimated_slippage_bps,
        borrow_cost_estimate_bps=borrow_cost_estimate_bps,
        calibration_source="gold_historical_targets",
        label_column=label_col,
        horizon_days=horizon_days,
        max_label_date_used=max_label_date,
        excluded_recent_rows=excluded_recent,
        gold_rows_read=int(len(gold)),
    )
    return prepared, {
        "label_column_used": label_col,
        "max_label_date_used": max_label_date,
        "excluded_recent_rows": excluded_recent,
        "gold_rows_read": int(len(gold)),
    }


def _bucket_spec(scoped: pd.DataFrame, bucket_type: str, side: str) -> tuple[pd.Series | None, pd.Series | None]:
    if bucket_type == "model_score_decile":
        values = pd.to_numeric(scoped["model_score_for_bucket"], errors="coerce")
        return _bucket_id_from_pct(values.rank(method="first", ascending=False, pct=True)), values
    if bucket_type == "rank_percentile_decile":
        values = pd.to_numeric(scoped["rank_pct"], errors="coerce")
        return _bucket_id_from_pct(values), values
    if bucket_type == "rank_overall_decile":
        rank_col = "rank_overall" if "rank_overall" in scoped.columns else "candidate_rank" if "candidate_rank" in scoped.columns else None
        if rank_col is None:
            return None, None
        values = pd.to_numeric(scoped[rank_col], errors="coerce")
        return _bucket_id_from_pct(values.rank(method="first", pct=True)), values
    if bucket_type == "side_specific_rank_decile":
        if side == "combined":
            return None, None
        values = pd.to_numeric(scoped["side_rank_pct"], errors="coerce")
        return _bucket_id_from_pct(values), values
    return None, None


def _build_bucket_rows(frame: pd.DataFrame, *, bucket_type: str, side: str, built_at: str, model_version: str) -> list[dict[str, Any]]:
    scoped = frame.copy() if side == "combined" else frame[frame["side"].eq(side)].copy()
    if scoped.empty:
        return []
    bucket, values = _bucket_spec(scoped, bucket_type, side)
    if bucket is None or values is None:
        return []
    scoped = scoped.assign(bucket_id=bucket, bucket_value=values)
    scoped = scoped[scoped["bucket_id"].astype(str).ne("unknown")].copy()
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
        source = _text(group["calibration_source"].iloc[0])
        horizon_days = int(group["horizon_days"].iloc[0]) if "horizon_days" in group.columns else 5
        warning = _text(group["validation_warning"].iloc[0])
        rows.append(
            {
                "calibration_id": f"{model_version}:{source}:{horizon_days}d:{side}:{bucket_type}:{bucket_id}",
                "built_at": built_at,
                "calibration_source": source,
                "validation_warning": warning,
                "model_version": model_version,
                "horizon_days": horizon_days,
                "side": side,
                "bucket_type": bucket_type,
                "bucket_id": str(bucket_id),
                "bucket_min_value": group["bucket_value"].min(),
                "bucket_max_value": group["bucket_value"].max(),
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
                "borrow_cost_estimate_bps": float(pd.to_numeric(group["borrow_cost_estimate_bps"], errors="coerce").mean()),
                "net_expected_return_bps": float(returns.mean()) if sample_count else 0.0,
                "confidence_level": confidence,
                "calibration_quality": quality,
                "insufficient_data_reason": reason,
                "max_label_date_used": _text(group["max_label_date_used"].iloc[0]),
                "excluded_recent_rows": int(group["excluded_recent_rows"].iloc[0]),
                "label_column_used": _text(group["label_column_used"].iloc[0]),
                "gold_rows_read": int(group["gold_rows_read"].iloc[0]),
            }
        )
    return rows


def _flag_non_monotonic(calibration: pd.DataFrame) -> pd.DataFrame:
    if calibration.empty:
        return calibration
    out = calibration.copy()
    for (_, side), idx in out.groupby(["bucket_type", "side"]).groups.items():
        group = out.loc[list(idx)].copy()
        simple = group[group["bucket_id"].astype(str).str.match(r"^D\d{2}$")].sort_values("bucket_id")
        if len(simple) < 3:
            continue
        values = pd.to_numeric(simple["net_expected_return_bps"], errors="coerce")
        if values.isna().any():
            continue
        non_monotonic = any(values.iloc[i] + 1e-9 < values.iloc[i + 1] for i in range(len(values) - 1))
        if non_monotonic:
            reasons = out.loc[simple.index, "insufficient_data_reason"].fillna("").astype(str)
            out.loc[simple.index, "insufficient_data_reason"] = reasons.map(lambda reason: "non_monotonic_bucket" if not reason else f"{reason}|non_monotonic_bucket")
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
    horizon_days = _horizon_days(horizon)
    prepared = prepare_validation_rows(
        predictions,
        estimated_spread_cost_bps=estimated_spread_cost_bps,
        estimated_slippage_bps=estimated_slippage_bps,
        borrow_cost_estimate_bps=borrow_cost_estimate_bps,
        calibration_source="walk_forward_validation",
        horizon_days=horizon_days,
    )
    built = (built_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    version = model_version or _model_version(predictions)
    if prepared.empty:
        return pd.DataFrame(columns=CALIBRATION_COLUMNS), 0
    rows: list[dict[str, Any]] = []
    for side in ["Long", "Short", "combined"]:
        for bucket_type in ["model_score_decile", "rank_percentile_decile", "rank_overall_decile", "side_specific_rank_decile"]:
            rows.extend(_build_bucket_rows(prepared, bucket_type=bucket_type, side=side, built_at=built, model_version=version))
    calibration = _flag_non_monotonic(pd.DataFrame(rows, columns=CALIBRATION_COLUMNS))
    return calibration.reindex(columns=CALIBRATION_COLUMNS), int(len(prepared))


def build_gold_fallback_calibration(
    gold: pd.DataFrame,
    *,
    model_version: str | None = None,
    horizon: str = "5d",
    built_at: datetime | None = None,
    estimated_spread_cost_bps: float = 0.0,
    estimated_slippage_bps: float = 5.0,
    borrow_cost_estimate_bps: float = 0.0,
) -> tuple[pd.DataFrame, int, dict[str, Any]]:
    horizon_days = _horizon_days(horizon)
    prepared, metadata = prepare_gold_historical_rows(
        gold,
        horizon_days=horizon_days,
        estimated_spread_cost_bps=estimated_spread_cost_bps,
        estimated_slippage_bps=estimated_slippage_bps,
        borrow_cost_estimate_bps=borrow_cost_estimate_bps,
    )
    built = (built_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    version = model_version or _model_version(gold)
    if prepared.empty:
        return pd.DataFrame(columns=CALIBRATION_COLUMNS), 0, metadata
    rows: list[dict[str, Any]] = []
    for side in ["Long", "Short", "combined"]:
        for bucket_type in ["model_score_decile", "rank_percentile_decile", "rank_overall_decile", "side_specific_rank_decile"]:
            rows.extend(_build_bucket_rows(prepared, bucket_type=bucket_type, side=side, built_at=built, model_version=version))
    calibration = _flag_non_monotonic(pd.DataFrame(rows, columns=CALIBRATION_COLUMNS))
    return calibration.reindex(columns=CALIBRATION_COLUMNS), int(len(prepared)), metadata


def _candidate_rank_pct(candidates: pd.DataFrame, *, side_specific: bool = True) -> pd.Series:
    frame = candidates.copy()
    if "side" not in frame.columns:
        frame["side"] = frame.get("trade_action", "").map(_side)
    else:
        frame["side"] = frame["side"].map(_side)
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
        return out.reindex(columns=MAPPING_COLUMNS)
    frame = candidates.copy()
    if "side" not in frame.columns:
        frame["side"] = frame.get("trade_action", "").map(_side)
    else:
        frame["side"] = frame["side"].map(_side)
        missing = frame["side"].eq("")
        if "trade_action" in frame.columns:
            frame.loc[missing, "side"] = frame.loc[missing, "trade_action"].map(_side)
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
        out.loc[idx, "calibration_source"] = match.get("calibration_source", "")
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
        ],
    )
    if path is None:
        return None, pd.DataFrame()
    return path, pd.read_csv(path, low_memory=False)


def load_latest_gold_panel(root: Path | str | None = None) -> tuple[Path | None, pd.DataFrame]:
    base = Path(root) if root else PROJECT_ROOT
    path = latest_csv(base / "data" / "gold", ["gold_stock_decision_daily_*.csv", "06_us_gold_ml_dataset_*.csv"])
    if path is None:
        return None, pd.DataFrame()
    header = pd.read_csv(path, nrows=0).columns.tolist()
    wanted = {
        "date",
        "ticker",
        "sector",
        "model_score",
        "risk_adjusted_score",
        "selection_score",
        "rank_overall",
        "candidate_rank",
        "predicted_rank_pct_by_date",
        "target_rank_pct_5d",
        "spread_bps",
        *GOLD_TARGET_ALIASES,
    }
    usecols = [column for column in header if column in wanted]
    return path, pd.read_csv(path, usecols=usecols, low_memory=False)


def _source_counts(calibration: pd.DataFrame) -> tuple[str, str]:
    if calibration.empty:
        return "insufficient_data", ""
    source = _text(calibration["calibration_source"].dropna().astype(str).iloc[0]) if "calibration_source" in calibration.columns else ""
    warning = _text(calibration["validation_warning"].dropna().astype(str).iloc[0]) if "validation_warning" in calibration.columns and calibration["validation_warning"].dropna().astype(str).any() else ""
    return source or "insufficient_data", warning


def write_validation_bucket_calibration(
    predictions: pd.DataFrame,
    *,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
    model_version: str | None = None,
    horizon: str = "5d",
    gold_panel: pd.DataFrame | None = None,
    gold_path: Path | None = None,
) -> ValidationBucketCalibrationOutputs:
    out_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "data" / "model_outputs" / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    calibration, validation_rows = build_validation_bucket_calibration(predictions, model_version=model_version, horizon=horizon)
    source = "walk_forward_validation" if validation_rows > 0 and not calibration.empty else "insufficient_data"
    metadata: dict[str, Any] = {}
    if calibration.empty and gold_panel is not None and not gold_panel.empty:
        calibration, validation_rows, metadata = build_gold_fallback_calibration(gold_panel, model_version=model_version, horizon=horizon)
        source = "gold_historical_targets" if validation_rows > 0 and not calibration.empty else "insufficient_data"
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
            calibration_source=source,
            gold_path=gold_path,
            gold_rows_read=int(metadata.get("gold_rows_read", 0)),
            label_column_used=str(metadata.get("label_column_used", "")),
            max_label_date_used=str(metadata.get("max_label_date_used", "")),
            excluded_recent_rows=int(metadata.get("excluded_recent_rows", 0)),
        ),
        encoding="utf-8",
    )
    mapping_coverage = 0
    return ValidationBucketCalibrationOutputs(
        calibration_path,
        latest_path,
        summary_path,
        validation_rows,
        len(calibration),
        usable,
        weak,
        insufficient,
        source,
        gold_path,
        int(metadata.get("gold_rows_read", 0)),
        str(metadata.get("label_column_used", "")),
        str(metadata.get("max_label_date_used", "")),
        int(metadata.get("excluded_recent_rows", 0)),
        mapping_coverage,
    )


def render_summary(
    calibration: pd.DataFrame,
    *,
    validation_rows_used: int,
    usable_buckets: int,
    weak_buckets: int,
    insufficient_buckets: int,
    calibration_source: str = "insufficient_data",
    gold_path: Path | None = None,
    gold_rows_read: int = 0,
    label_column_used: str = "",
    max_label_date_used: str = "",
    excluded_recent_rows: int = 0,
) -> str:
    warning = HISTORICAL_GOLD_WARNING if calibration_source == "gold_historical_targets" else ""
    lines = [
        "# Validation Bucket Expected Return Calibration",
        "",
        f"- walk_forward_validation_available: {'yes' if calibration_source == 'walk_forward_validation' else 'no'}",
        f"- calibration_source: {calibration_source}",
        f"- fallback_reason: {'walk_forward_outputs_empty_or_invalid' if calibration_source == 'gold_historical_targets' else ''}",
        f"- gold_file_used: {gold_path or ''}",
        f"- gold_rows_read: {gold_rows_read}",
        f"- rows_used_for_calibration: {validation_rows_used}",
        f"- label_column_used: {label_column_used}",
        f"- latest_date_excluded_after: {max_label_date_used}",
        f"- excluded_recent_rows: {excluded_recent_rows}",
        f"- buckets_built: {len(calibration)}",
        f"- usable_buckets: {usable_buckets}",
        f"- weak_buckets: {weak_buckets}",
        f"- insufficient_buckets: {insufficient_buckets}",
        f"- candidate_mapping_coverage: 0",
        f"- warning: {warning}",
        f"- expected_return_executable_safe: {'yes' if usable_buckets > 0 else 'no'}",
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
    if warning:
        lines.extend(["", "## Validation Warning", "", warning])
    if usable_buckets == 0:
        lines.extend(["", "## Warning", "", "No executable-safe calibration exists. Keep expected_return_uncalibrated active."])
    return "\n".join(lines) + "\n"


def build_latest_validation_bucket_calibration(root: Path | str | None = None, *, output_dir: Path | str | None = None) -> ValidationBucketCalibrationOutputs:
    validation_path, predictions = load_latest_validation_predictions(root)
    gold_path, gold = load_latest_gold_panel(root)
    return write_validation_bucket_calibration(predictions, output_dir=output_dir, gold_panel=gold, gold_path=gold_path)
