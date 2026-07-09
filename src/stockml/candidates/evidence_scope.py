from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.trading.ticker_direction_memory import load_ticker_direction_memory_config


SCOPE_COLUMNS = [
    "expected_return_scope",
    "hit_rate_scope",
    "profit_factor_scope",
    "ticker_direction_memory_status",
    "ticker_direction_sample_count",
    "inverse_warning_status",
    "inverse_warning_actionable",
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


def _side_series(frame: pd.DataFrame) -> pd.Series:
    side = frame.get("side", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    action = frame.get("trade_action", frame.get("source_trade_action", pd.Series("", index=frame.index))).fillna("").astype(str).str.lower()
    out = side.copy()
    out.loc[out.isin(["buy", "long"]) | action.eq("long")] = "buy"
    out.loc[out.isin(["sell", "short"]) | action.eq("short")] = "sell"
    return out


def _scope_for_metric(frame: pd.DataFrame, column: str) -> pd.Series:
    explicit_columns = {
        "validated_expected_return_bps": "expected_return_scope",
        "validated_hit_rate": "hit_rate_scope",
        "validated_profit_factor": "profit_factor_scope",
    }
    explicit_column = explicit_columns.get(column, f"{column.removeprefix('validated_')}_scope")
    if explicit_column in frame.columns:
        explicit = frame[explicit_column].fillna("").astype(str).str.strip().str.lower()
    else:
        explicit = pd.Series("", index=frame.index, dtype="object")
    if column not in frame.columns or frame.empty:
        return explicit.where(explicit.isin({"ticker", "bucket", "side", "global"}), "unknown")
    values = pd.to_numeric(frame[column], errors="coerce")
    side = _side_series(frame)
    scope = pd.Series("unknown", index=frame.index, dtype="object")
    valid_scopes = {"ticker", "bucket", "side", "global"}
    if values.notna().sum() == 0:
        return explicit.where(explicit.isin(valid_scopes), scope)
    rounded = values.round(8)
    if rounded.nunique(dropna=True) == 1:
        scope.loc[values.notna()] = "global"
    for side_value, indexes in side.groupby(side).groups.items():
        if not side_value:
            continue
        side_values = rounded.loc[indexes].dropna()
        if len(side_values) > 1 and side_values.nunique(dropna=True) == 1:
            scope.loc[list(indexes)] = "side"
    bucket_columns = [column for column in ["rank_bucket", "score_bucket", "calibration_bucket", "bucket_id"] if column in frame.columns]
    for bucket_column in bucket_columns:
        for _, indexes in frame.groupby([side, frame[bucket_column]], dropna=True).groups.items():
            bucket_values = rounded.loc[indexes].dropna()
            if len(bucket_values) > 1 and bucket_values.nunique(dropna=True) == 1:
                scope.loc[list(indexes)] = "bucket"
    symbol = frame.get("symbol", frame.get("ticker", pd.Series("", index=frame.index))).astype(str).str.upper()
    for _, indexes in symbol.groupby(symbol).groups.items():
        symbol_values = rounded.loc[indexes].dropna()
        if len(symbol_values) > 1 and symbol_values.nunique(dropna=True) == 1:
            scope.loc[list(indexes)] = "ticker"
    resolved = explicit.where(explicit.isin(valid_scopes), scope)
    inferred = scope.fillna("").astype(str).str.lower()
    inferred_specific = inferred.isin({"side", "global", "bucket"})
    resolved.loc[inferred_specific] = scope.loc[inferred_specific]
    missing = resolved.fillna("").astype(str).str.lower().isin({"", "nan", "none", "null", "unknown"})
    resolved.loc[missing] = scope.loc[missing]
    return resolved


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


def enrich_candidate_evidence_scope(
    candidates: pd.DataFrame,
    *,
    min_ticker_samples: int | None = None,
) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        out = candidates.copy() if candidates is not None else pd.DataFrame()
        for column in SCOPE_COLUMNS:
            out[column] = pd.Series(dtype="object")
        return out
    cfg = load_ticker_direction_memory_config()
    threshold = int(min_ticker_samples or cfg.min_ticker_samples or 20)
    out = candidates.copy()
    if "symbol" not in out.columns and "ticker" in out.columns:
        out["symbol"] = out["ticker"]
    out["expected_return_scope"] = _scope_for_metric(out, "validated_expected_return_bps")
    out["hit_rate_scope"] = _scope_for_metric(out, "validated_hit_rate")
    out["profit_factor_scope"] = _scope_for_metric(out, "validated_profit_factor")
    if "ticker_direction_sample_count" not in out.columns:
        out["ticker_direction_sample_count"] = 0
    out["ticker_direction_sample_count"] = pd.to_numeric(out["ticker_direction_sample_count"], errors="coerce").fillna(0).astype(int)
    out["ticker_direction_memory_status"] = out.apply(lambda row: _ticker_memory_status(row, threshold), axis=1)
    inverse_flag = out.get("direction_decision", pd.Series("", index=out.index)).fillna("").astype(str).str.lower().eq("direction_inverse_watch")
    inverse_flag = inverse_flag | out.get("direction_inverse_warning", pd.Series(False, index=out.index)).fillna(False).astype(bool)
    inverse_advantage = pd.to_numeric(out.get("ticker_inverse_advantage_bps", pd.Series(index=out.index)), errors="coerce")
    inverse_flag = inverse_flag | inverse_advantage.gt(0)
    out["inverse_warning_status"] = "none"
    out.loc[inverse_flag, "inverse_warning_status"] = "present_insufficient_samples"
    sufficient = out["ticker_direction_sample_count"].ge(threshold)
    out.loc[inverse_flag & sufficient, "inverse_warning_status"] = "present_sufficient_samples"
    out["inverse_warning_actionable"] = bool(False)
    out.loc[inverse_flag & sufficient, "inverse_warning_actionable"] = True
    return out


def split_candidate_pools(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if frame is None or frame.empty:
        empty = pd.DataFrame()
        return {"shadow": empty, "watch": empty, "execution": empty, "blocked": empty}
    if "execution_domain" in frame.columns:
        domain = frame["execution_domain"].fillna("").astype(str).str.lower()
        return {
            "shadow": frame[domain.eq("shadow_observation")].copy(),
            "watch": frame[domain.eq("watch_candidate")].copy(),
            "execution": frame[domain.eq("execution_candidate")].copy(),
            "blocked": frame[domain.eq("blocked_candidate")].copy(),
        }
    status = frame.get("status", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    executable = frame.get("executable", pd.Series(False, index=frame.index)).fillna(False).astype(bool)
    research = frame.get("research_only", pd.Series(False, index=frame.index)).fillna(False).astype(bool) | status.eq("research_only")
    return {
        "shadow": frame[research].copy(),
        "watch": frame[status.eq("watch")].copy(),
        "execution": frame[executable & status.eq("executable")].copy(),
        "blocked": frame[~research & ~status.eq("watch") & ~(executable & status.eq("executable"))].copy(),
    }


def write_candidate_pool_splits(
    frame: pd.DataFrame,
    *,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
) -> dict[str, Path]:
    out_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "data" / "trading" / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    splits = split_candidate_pools(frame)
    paths = {
        "execution_candidate_pool": out_dir / f"execution_candidate_pool_{run_stamp}.csv",
        "watch_candidate_pool": out_dir / f"watch_candidate_pool_{run_stamp}.csv",
        "blocked_candidate_pool": out_dir / f"blocked_candidate_pool_{run_stamp}.csv",
        "shadow_observation_pool": out_dir / f"shadow_observation_pool_{run_stamp}.csv",
    }
    splits["execution"].to_csv(paths["execution_candidate_pool"], index=False)
    splits["watch"].to_csv(paths["watch_candidate_pool"], index=False)
    splits["blocked"].to_csv(paths["blocked_candidate_pool"], index=False)
    splits["shadow"].to_csv(paths["shadow_observation_pool"], index=False)
    return paths
