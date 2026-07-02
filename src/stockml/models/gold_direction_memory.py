from __future__ import annotations

import pandas as pd


def _text_series(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="object")
    return frame[column].fillna(default).astype(str)


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _fill_missing(out: pd.DataFrame, column: str, values: pd.Series) -> None:
    if column not in out.columns:
        out[column] = values
        return
    current = out[column]
    if pd.api.types.is_numeric_dtype(values):
        current_numeric = pd.to_numeric(current, errors="coerce")
        out[column] = current_numeric.combine_first(values)
        return
    current_text = current.fillna("").astype(str).str.strip()
    out[column] = current.where(~current_text.str.lower().isin({"", "nan", "none", "null"}), values)


def _action_series(frame: pd.DataFrame) -> pd.Series:
    action = _text_series(frame, "directional_action").str.strip().str.lower()
    fallback = _text_series(frame, "trade_action").str.strip().str.lower()
    action = action.where(action.isin({"long", "short"}), fallback)
    return action


def enrich_gold_direction_memory_fields(frame: pd.DataFrame, *, min_samples: int = 20) -> pd.DataFrame:
    """Carry Gold-derived ticker direction evidence into downstream model rows.

    This function is intentionally additive. It does not change trade_action,
    directional_action, calibration_quality, or any execution gate.
    """

    if frame is None or frame.empty:
        return frame.copy() if frame is not None else pd.DataFrame()

    out = frame.copy()
    sample_count = _numeric_series(out, "ticker_direction_sample_count").fillna(0).astype(int)
    bias_gold = _text_series(out, "ticker_direction_bias_gold")
    reason_gold = _text_series(out, "ticker_direction_reason_gold")
    status_gold = _text_series(out, "ticker_direction_memory_status")

    _fill_missing(out, "ticker_direction_bias", bias_gold)
    _fill_missing(out, "ticker_direction_reason", reason_gold)
    _fill_missing(out, "ticker_direction_sample_count", sample_count)

    if "ticker_direction_memory_status" not in out.columns:
        status = pd.Series("missing", index=out.index, dtype="object")
        status.loc[sample_count.gt(0) & sample_count.lt(min_samples)] = "insufficient_samples"
        status.loc[sample_count.ge(min_samples)] = "available"
        out["ticker_direction_memory_status"] = status
    else:
        existing = out["ticker_direction_memory_status"].fillna("").astype(str).str.strip()
        status = status_gold.where(status_gold.str.strip().ne(""), existing)
        status = status.where(status.str.strip().ne(""), "missing")
        out["ticker_direction_memory_status"] = status

    action = _action_series(out)
    long_hit = _numeric_series(out, "ticker_long_win_rate_5d")
    short_hit = _numeric_series(out, "ticker_short_win_rate_5d")
    confidence = pd.Series(float("nan"), index=out.index, dtype="float64")
    confidence.loc[action.eq("long")] = long_hit.loc[action.eq("long")]
    confidence.loc[action.eq("short")] = short_hit.loc[action.eq("short")]
    confidence = confidence.combine_first(pd.concat([long_hit, short_hit], axis=1).max(axis=1, skipna=True))
    _fill_missing(out, "ticker_direction_confidence", confidence)

    long_edge = _numeric_series(out, "ticker_avg_long_alpha_bps_5d")
    short_edge = _numeric_series(out, "ticker_avg_short_alpha_bps_5d")
    expected_bps = pd.Series(float("nan"), index=out.index, dtype="float64")
    expected_bps.loc[action.eq("long")] = long_edge.loc[action.eq("long")]
    expected_bps.loc[action.eq("short")] = short_edge.loc[action.eq("short")]
    _fill_missing(out, "validated_expected_return_bps", expected_bps)

    hit_rate = pd.Series(float("nan"), index=out.index, dtype="float64")
    hit_rate.loc[action.eq("long")] = long_hit.loc[action.eq("long")]
    hit_rate.loc[action.eq("short")] = short_hit.loc[action.eq("short")]
    _fill_missing(out, "validated_hit_rate", hit_rate)

    ticker_scope = sample_count.ge(min_samples)
    for column, metric in [
        ("expected_return_scope", "validated_expected_return_bps"),
        ("hit_rate_scope", "validated_hit_rate"),
        ("profit_factor_scope", "validated_profit_factor"),
    ]:
        values = _numeric_series(out, metric)
        scope = pd.Series("unknown", index=out.index, dtype="object")
        scope.loc[ticker_scope & values.notna()] = "ticker"
        _fill_missing(out, column, scope)

    if "ticker_inverse_advantage_bps" not in out.columns:
        out["ticker_inverse_advantage_bps"] = pd.NA
    return out

