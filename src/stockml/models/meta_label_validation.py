from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta

import pandas as pd
from sklearn.metrics import accuracy_score, brier_score_loss, f1_score, precision_score, recall_score, roc_auc_score

from stockml.models.meta_label_features import build_feature_matrix, selected_meta_features
from stockml.models.meta_label_targets import add_meta_label_targets, trade_examples
from stockml.models.meta_labeling import MetaLabelConfig
from sklearn.ensemble import HistGradientBoostingClassifier


def walk_forward_meta_splits(dates, embargo_days: int = 5, folds: int = 4) -> list[dict]:
    unique_dates = pd.Series(pd.to_datetime(pd.Series(dates).dropna().unique())).sort_values().reset_index(drop=True)
    if len(unique_dates) < 4:
        return []
    validation_size = max(1, len(unique_dates) // (folds + 1))
    splits = []
    for fold in range(1, folds + 1):
        valid_start_idx = min(len(unique_dates) - validation_size, fold * validation_size)
        valid_end_idx = min(len(unique_dates) - 1, valid_start_idx + validation_size - 1)
        validation_start = unique_dates.iloc[valid_start_idx]
        validation_end = unique_dates.iloc[valid_end_idx]
        train_end = validation_start - timedelta(days=int(embargo_days))
        train_dates = unique_dates[unique_dates <= train_end]
        if train_dates.empty:
            continue
        splits.append(
            {
                "fold": fold,
                "train_start": train_dates.iloc[0],
                "train_end": train_dates.iloc[-1],
                "validation_start": validation_start,
                "validation_end": validation_end,
                "embargo_days": embargo_days,
            }
        )
    return splits


def _metrics(y_true, probability, accepted, realized_gain) -> dict:
    prediction = (probability >= 0.5).astype(int)
    accepted_gain = realized_gain[accepted]
    skipped_loss = realized_gain[~accepted]
    output = {
        "meta_label_accuracy": accuracy_score(y_true, prediction),
        "precision": precision_score(y_true, prediction, zero_division=0),
        "recall": recall_score(y_true, prediction, zero_division=0),
        "f1": f1_score(y_true, prediction, zero_division=0),
        "brier_score": brier_score_loss(y_true, probability),
        "accepted_trade_hit_rate": float(y_true[accepted].mean()) if accepted.any() else 0.0,
        "accepted_trade_average_realized_gain": float(accepted_gain.mean()) if accepted.any() else 0.0,
        "skipped_trade_avoided_loss_estimate": float((-skipped_loss[skipped_loss < 0]).mean()) if (skipped_loss < 0).any() else 0.0,
        "accepted_count": int(accepted.sum()),
        "validation_count": int(len(y_true)),
    }
    try:
        output["roc_auc"] = roc_auc_score(y_true, probability) if len(set(y_true)) > 1 else 0.0
    except Exception:
        output["roc_auc"] = 0.0
    return output


def walk_forward_validate_meta_labels(frame: pd.DataFrame, config: MetaLabelConfig | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cfg = config or MetaLabelConfig()
    labeled = trade_examples(add_meta_label_targets(frame, cfg.transaction_cost_bps))
    if labeled.empty or "date" not in labeled.columns:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    labeled["date"] = pd.to_datetime(labeled["date"], errors="coerce")
    rows = []
    predictions = []
    features = selected_meta_features(labeled)
    for split in walk_forward_meta_splits(labeled["date"], cfg.embargo_days):
        train = labeled[(labeled["date"] >= split["train_start"]) & (labeled["date"] <= split["train_end"])].copy()
        valid = labeled[(labeled["date"] >= split["validation_start"]) & (labeled["date"] <= split["validation_end"])].copy()
        if len(train) < cfg.min_training_signals or valid.empty or train["meta_label"].nunique() < 2:
            continue
        x_train, columns = build_feature_matrix(train, features)
        x_valid, _ = build_feature_matrix(valid, features, columns)
        model = HistGradientBoostingClassifier(random_state=42, max_iter=120, learning_rate=0.05)
        model.fit(x_train, train["meta_label"].astype(int))
        probability = pd.Series(model.predict_proba(x_valid)[:, 1], index=valid.index)
        accepted = probability >= cfg.min_meta_label_probability
        metric_row = {**split, **_metrics(valid["meta_label"].astype(int), probability, accepted, valid["meta_realized_gain"])}
        rows.append(metric_row)
        scored = valid.copy()
        scored["meta_label_probability"] = probability
        scored["meta_label_decision"] = accepted.map({True: "Take Trade", False: "Skip Trade"})
        predictions.append(scored)
    validation = pd.DataFrame(rows)
    pred = pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame()
    buckets = pd.DataFrame()
    if not pred.empty:
        pred["meta_label_probability_bucket"] = pd.cut(pd.to_numeric(pred["meta_label_probability"], errors="coerce"), bins=[0, 0.4, 0.5, 0.6, 0.7, 1.0], include_lowest=True)
        buckets = pred.groupby("meta_label_probability_bucket", observed=False).agg(
            row_count=("ticker", "size"),
            hit_rate=("meta_label", "mean"),
            avg_realized_gain=("meta_realized_gain", "mean"),
        ).reset_index()
        buckets["meta_label_probability_bucket"] = buckets["meta_label_probability_bucket"].astype(str)
    if not validation.empty:
        validation["config"] = str(asdict(cfg))
    return validation, pred, buckets
