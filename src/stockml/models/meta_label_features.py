from __future__ import annotations

import pandas as pd


ALLOWED_META_LABEL_FEATURES = [
    "side_probability",
    "probability_edge",
    "confidence_score",
    "candidate_rank_overall",
    "candidate_rank_by_sector",
    "validation_bucket_hit_rate",
    "validation_bucket_avg_gain",
    "expected_trade_return",
    "risk_adjusted_score",
    "volatility_20d",
    "volatility_60d",
    "avg_dollar_volume_20d",
    "market_cap",
    "sector_relative_strength_score",
    "market_regime_score",
    "sentiment_score_mean",
    "news_attention_score",
    "risk_tier",
    "liquidity_tier",
    "volatility_tier",
]

LEAKAGE_TOKENS = ("target_", "future_", "realized_", "pnl_")
LEAKAGE_EXACT = {
    "trade_action",
    "signal",
    "validation_fold",
    "test_fold",
    "train_fold",
    "fold",
    "order_id",
    "client_order_id",
    "filled_qty",
    "filled_avg_price",
    "alpaca_status",
    "submitted_at",
    "updated_at",
    "api_error",
    "http_status",
}


def leakage_reason(column: str) -> str:
    lower = column.lower()
    if lower in LEAKAGE_EXACT:
        return "blocked_identity_or_execution_column"
    if any(lower.startswith(token) for token in LEAKAGE_TOKENS):
        return "blocked_future_or_outcome_column"
    if "execution" in lower or "fill" in lower or "order" in lower:
        return "blocked_execution_result_column"
    return ""


def selected_meta_features(frame: pd.DataFrame) -> list[str]:
    return [column for column in ALLOWED_META_LABEL_FEATURES if column in frame.columns and not leakage_reason(column)]


def leakage_audit(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    selected = set(selected_meta_features(frame))
    for column in frame.columns:
        reason = leakage_reason(column)
        allowed = column in ALLOWED_META_LABEL_FEATURES
        rows.append(
            {
                "feature_name": column,
                "allowed": allowed,
                "included": column in selected,
                "exclusion_reason": reason or ("" if allowed else "not_in_meta_feature_allowlist"),
            }
        )
    return pd.DataFrame(rows)


def build_feature_matrix(frame: pd.DataFrame, features: list[str] | None = None, columns: list[str] | None = None) -> tuple[pd.DataFrame, list[str]]:
    chosen = features or selected_meta_features(frame)
    if not chosen:
        return pd.DataFrame(index=frame.index), []
    raw = frame[chosen].copy()
    categorical = [col for col in ["risk_tier", "liquidity_tier", "volatility_tier"] if col in raw.columns]
    numeric = raw.drop(columns=categorical, errors="ignore").apply(pd.to_numeric, errors="coerce")
    encoded = pd.get_dummies(raw[categorical].fillna("unknown").astype(str), columns=categorical, prefix=categorical) if categorical else pd.DataFrame(index=frame.index)
    matrix = pd.concat([numeric, encoded], axis=1).replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)
    if columns is not None:
        matrix = matrix.reindex(columns=columns, fill_value=0.0)
        return matrix, columns
    return matrix, matrix.columns.tolist()
