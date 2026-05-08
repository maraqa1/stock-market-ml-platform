from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Dict, List

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from stockml.models.gold_loader import build_model_matrix, build_prediction_matrix


@dataclass
class ModelArtifacts:
    predictions: pd.DataFrame
    signal_table: pd.DataFrame
    top_long: pd.DataFrame
    top_short: pd.DataFrame
    validation_leaderboard: pd.DataFrame
    bucket_performance: pd.DataFrame
    feature_importance: pd.DataFrame
    model_status: pd.DataFrame
    data_dictionary: pd.DataFrame


def _model_candidates(random_state: int = 42) -> Dict[str, object]:
    return {
        "logistic_regression": Pipeline(
            [
                ("scale", StandardScaler()),
                ("model", LogisticRegression(max_iter=1000, class_weight="balanced", random_state=random_state)),
            ]
        ),
        "random_forest": RandomForestClassifier(
            n_estimators=120,
            min_samples_leaf=10,
            class_weight="balanced",
            random_state=random_state,
            n_jobs=1,
        ),
    }


def _time_folds(dates: pd.Series, folds: int = 4, min_train_dates: int = 252, validation_dates: int = 63) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    unique_dates = pd.Series(pd.to_datetime(dates).dropna().sort_values().unique())
    if len(unique_dates) < min_train_dates + validation_dates:
        return []
    out = []
    max_start = len(unique_dates) - validation_dates
    starts = np.linspace(min_train_dates, max_start, num=min(folds, max_start - min_train_dates + 1), dtype=int)
    for start in sorted(set(starts.tolist())):
        out.append((unique_dates.iloc[start], unique_dates.iloc[min(start + validation_dates - 1, len(unique_dates) - 1)]))
    return out


def _score_fold(model, x_train, y_train, x_valid, valid: pd.DataFrame) -> dict:
    model.fit(x_train, y_train)
    probability = model.predict_proba(x_valid)[:, 1]
    scored = valid.copy()
    scored["predicted_probability_top_quintile_5d"] = probability
    scored["predicted_rank_pct"] = scored.groupby("date")["predicted_probability_top_quintile_5d"].rank(pct=True)
    top = scored[scored["predicted_rank_pct"] >= 0.8].copy()
    bottom = scored[scored["predicted_rank_pct"] <= 0.2].copy()
    baseline = valid["target_top_quintile_5d"].astype(int).mean()
    hit_rate = top["target_top_quintile_5d"].astype(int).mean() if not top.empty else 0.0
    short_hit_rate = bottom["target_bottom_quintile_5d"].astype(int).mean() if "target_bottom_quintile_5d" in bottom.columns and not bottom.empty else 0.0
    avg_gain = top["target_return_5d"].mean() if not top.empty else 0.0
    avg_short_gain = -bottom["target_return_5d"].mean() if not bottom.empty else 0.0
    ic = scored[["predicted_probability_top_quintile_5d", "target_return_5d"]].corr(method="spearman").iloc[0, 1]
    daily_ic = scored.groupby("date").apply(
        lambda d: d[["predicted_probability_top_quintile_5d", "target_return_5d"]].corr(method="spearman").iloc[0, 1]
    ).replace([np.inf, -np.inf], np.nan).dropna()
    icir = daily_ic.mean() / daily_ic.std() if len(daily_ic) > 1 and daily_ic.std() else 0.0
    turnover = _signal_turnover(scored)
    net_avg_gain = avg_gain - turnover * 0.001
    return {
        "validation_rows": len(valid),
        "signal_count": len(top),
        "hit_rate": float(hit_rate or 0),
        "short_hit_rate": float(short_hit_rate or 0),
        "baseline_top_quintile_rate": float(baseline or 0),
        "avg_realized_gain_5d": float(avg_gain or 0),
        "avg_realized_short_gain_5d": float(avg_short_gain or 0),
        "turnover_rate_5d": float(turnover or 0),
        "turnover_adjusted_avg_gain_5d": float(net_avg_gain or 0),
        "spearman_ic_5d": float(ic if pd.notna(ic) else 0),
        "icir_5d": float(icir if pd.notna(icir) else 0),
        "beats_baseline": bool(hit_rate > baseline and avg_gain > 0 and net_avg_gain > 0),
        "scored": scored,
    }


def _signal_turnover(scored: pd.DataFrame) -> float:
    frame = scored[scored["predicted_rank_pct"] >= 0.8].copy()
    if frame.empty:
        return 0.0
    date_sets = frame.groupby("date")["ticker"].apply(lambda values: set(values.astype(str)))
    turnovers = []
    previous = None
    for current in date_sets:
        if previous is not None and previous:
            turnovers.append(1 - len(previous & current) / len(previous))
        previous = current
    return float(np.mean(turnovers)) if turnovers else 0.0


def walk_forward_validate(gold: pd.DataFrame, folds: int = 4) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    x, y, feature_cols = build_model_matrix(gold)
    trainable = gold.dropna(subset=["target_return_5d"]).copy().reset_index(drop=True)
    fold_windows = _time_folds(trainable["date"], folds=folds)
    rows: List[dict] = []
    scored_frames = []

    if not fold_windows:
        return pd.DataFrame(), pd.DataFrame(), feature_cols

    for model_name, model in _model_candidates().items():
        for fold_no, (start, end) in enumerate(fold_windows, start=1):
            train_mask = trainable["date"] < start
            valid_mask = trainable["date"].between(start, end)
            if train_mask.sum() < 100 or valid_mask.sum() < 20 or y[train_mask].nunique() < 2:
                continue
            result = _score_fold(model, x.loc[train_mask], y.loc[train_mask], x.loc[valid_mask], trainable.loc[valid_mask])
            row = {k: v for k, v in result.items() if k != "scored"}
            row.update({"model_name": model_name, "fold": fold_no, "validation_start": start, "validation_end": end})
            rows.append(row)
            scored = result["scored"]
            scored["model_name"] = model_name
            scored["fold"] = fold_no
            scored_frames.append(scored)

    leaderboard = pd.DataFrame(rows)
    if leaderboard.empty:
        return leaderboard, pd.DataFrame(), feature_cols
    leaderboard["score"] = (
        leaderboard["spearman_ic_5d"].fillna(0)
        + leaderboard["hit_rate"].fillna(0)
        + leaderboard["avg_realized_gain_5d"].fillna(0)
    )
    return leaderboard.sort_values("score", ascending=False), pd.concat(scored_frames, ignore_index=True), feature_cols


def _bucket_performance(scored: pd.DataFrame, model_name: str) -> pd.DataFrame:
    if scored.empty:
        return pd.DataFrame()
    frame = scored[scored["model_name"].eq(model_name)].copy()
    frame["confidence_bucket"] = pd.qcut(
        frame["predicted_probability_top_quintile_5d"].rank(method="first"),
        q=min(5, max(1, frame["date"].nunique())),
        labels=False,
        duplicates="drop",
    )
    return frame.groupby("confidence_bucket", as_index=False).agg(
        row_count=("ticker", "size"),
        hit_rate=("target_top_quintile_5d", "mean"),
        avg_realized_gain_5d=("target_return_5d", "mean"),
    )


def _decision_status(leaderboard: pd.DataFrame) -> tuple[str, str]:
    if leaderboard.empty:
        return "diagnostic_only", "insufficient_validation_samples"
    best = leaderboard.iloc[0]
    if not bool(best.get("beats_baseline", False)):
        return "diagnostic_only", "model_not_decision_grade|validated_hit_rate_below_threshold"
    if float(best.get("icir_5d", 0)) < 1.0:
        return "diagnostic_only", "model_not_decision_grade|icir_below_threshold"
    if float(leaderboard.groupby("fold")["hit_rate"].max().min() if "fold" in leaderboard.columns else 0) < 0.48:
        return "diagnostic_only", "model_not_decision_grade|fold_hit_rate_below_floor"
    if float(best.get("avg_realized_gain_5d", 0)) <= 0:
        return "diagnostic_only", "expected_trade_return_below_threshold"
    if float(best.get("turnover_adjusted_avg_gain_5d", 0)) <= 0:
        return "diagnostic_only", "turnover_adjusted_return_below_threshold"
    return "decision_grade", "validation_gates_passed"


def _diagnostic_paper_mode_enabled() -> bool:
    value = os.environ.get("STOCKML_ALLOW_DIAGNOSTIC_PAPER_TRADES", "").strip().lower()
    return value in {"1", "true", "yes", "y"}


def train_predict_from_gold(gold: pd.DataFrame, top_n: int = 50) -> ModelArtifacts:
    leaderboard, scored_validation, feature_cols = walk_forward_validate(gold)
    model_name = leaderboard.iloc[0]["model_name"] if not leaderboard.empty else "logistic_regression"
    model = _model_candidates()[model_name]

    x, y, _ = build_model_matrix(gold)
    trainable = gold.dropna(subset=["target_return_5d"]).copy()
    if y.nunique() < 2:
        raise ValueError("Gold dataset target has fewer than two classes; cannot train prediction model.")
    model.fit(x, y)

    predict_x = build_prediction_matrix(gold, feature_cols)
    predictions = gold[[
        "date", "ticker", "company", "exchange", "sector", "industry", "close", "volume",
        "selection_score", "candidate_rank_overall", "candidate_rank_by_sector",
    ]].copy()
    predictions["model_name"] = model_name
    predictions["model_version"] = "gold_ranker_v1"
    predictions["calibrated_probability_up_5d"] = model.predict_proba(predict_x)[:, 1]
    predictions["probability_down_5d"] = 1 - predictions["calibrated_probability_up_5d"]
    predictions["probability_neutral_5d"] = (1 - (predictions["calibrated_probability_up_5d"] - 0.5).abs() * 2).clip(0, 1)
    predictions["probability_edge"] = predictions["calibrated_probability_up_5d"] - 0.5
    predictions["side_probability"] = predictions[["calibrated_probability_up_5d", "probability_down_5d"]].max(axis=1)
    predictions["predicted_rank_pct_by_date"] = predictions.groupby("date")["calibrated_probability_up_5d"].rank(pct=True)

    decision_grade, reason = _decision_status(leaderboard)
    latest_date = predictions["date"].max()
    latest = predictions[predictions["date"].eq(latest_date)].copy()
    latest["trade_action"] = "No Decision"
    latest["signal_reason"] = "model_not_decision_grade" if decision_grade != "decision_grade" else ""
    latest["no_decision_reason"] = reason if decision_grade != "decision_grade" else "not_in_top_ranked_long_or_short_candidates"
    latest["model_status"] = decision_grade
    latest["diagnostic_only"] = decision_grade != "decision_grade"

    diagnostic_paper_mode = decision_grade != "decision_grade" and _diagnostic_paper_mode_enabled()
    if decision_grade == "decision_grade" or diagnostic_paper_mode:
        long_mask = (latest["predicted_rank_pct_by_date"] >= 0.9) & (latest["probability_edge"] > 0.05)
        short_mask = (latest["predicted_rank_pct_by_date"] <= 0.1) & (latest["probability_edge"] < -0.05)
        latest.loc[long_mask, "trade_action"] = "Long"
        latest.loc[long_mask, "signal_reason"] = "validated_probability_and_rank_gate_passed" if decision_grade == "decision_grade" else "diagnostic_paper_candidate_model_not_decision_grade"
        latest.loc[long_mask, "no_decision_reason"] = "" if decision_grade == "decision_grade" else reason
        latest.loc[short_mask, "trade_action"] = "Short"
        latest.loc[short_mask, "signal_reason"] = "validated_probability_and_rank_gate_passed" if decision_grade == "decision_grade" else "diagnostic_paper_candidate_model_not_decision_grade"
        latest.loc[short_mask, "no_decision_reason"] = "" if decision_grade == "decision_grade" else reason

    latest["expected_trade_return"] = latest["probability_edge"] * latest["selection_score"].fillna(0)
    latest["risk_adjusted_score"] = latest["expected_trade_return"] / (1 + latest["candidate_rank_overall"].fillna(999))
    latest["bucket_hit_rate"] = leaderboard.iloc[0]["hit_rate"] if not leaderboard.empty else 0
    latest["bucket_average_gain"] = leaderboard.iloc[0]["avg_realized_gain_5d"] if not leaderboard.empty else 0

    top_long = latest[latest["trade_action"].eq("Long")].sort_values("risk_adjusted_score", ascending=False).head(top_n)
    top_short = latest[latest["trade_action"].eq("Short")].sort_values("risk_adjusted_score", ascending=True).head(top_n)

    try:
        importance = permutation_importance(model, x, y, n_repeats=3, random_state=42, n_jobs=1)
        feature_importance = pd.DataFrame(
            {"feature": feature_cols, "importance": importance.importances_mean}
        ).sort_values("importance", ascending=False)
    except Exception:
        feature_importance = pd.DataFrame({"feature": feature_cols, "importance": 0.0})

    status = pd.DataFrame(
        [{
            "decision_grade": decision_grade,
            "diagnostic_only": decision_grade != "decision_grade",
            "selected_model": model_name,
            "model_version": "gold_ranker_v1",
            "validation_window": "walk_forward",
            "folds_completed": int(leaderboard["fold"].nunique()) if not leaderboard.empty else 0,
            "beats_baseline": bool(leaderboard.iloc[0]["beats_baseline"]) if not leaderboard.empty else False,
            "reason": reason,
            "diagnostic_paper_mode": diagnostic_paper_mode,
            "gold_input_rows": len(gold),
            "feature_count": len(feature_cols),
        }]
    )
    dictionary = pd.DataFrame({"column": predictions.columns, "source": "gold_model_prediction"})
    buckets = _bucket_performance(scored_validation, model_name)

    return ModelArtifacts(
        predictions=predictions,
        signal_table=latest,
        top_long=top_long,
        top_short=top_short,
        validation_leaderboard=leaderboard,
        bucket_performance=buckets,
        feature_importance=feature_importance,
        model_status=status,
        data_dictionary=dictionary,
    )
