from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


IDENTITY_COLUMNS = {
    "date", "ticker", "company", "exchange", "sector", "industry", "country", "currency",
    "sentiment_status", "sentiment_source", "risk_on_risk_off_flag",
}
COMPOSITE_SCORE_COLUMNS = {
    "selection_score", "technical_setup_score", "momentum_score", "sector_relative_momentum_score",
    "volume_confirmation_score", "risk_score", "volatility_score", "candidate_rank_overall",
    "candidate_rank_by_sector",
}
OUTCOME_TOKENS = ("future", "forward", "realized", "outcome")
PREDICTION_PREFIXES = ("target_", "prediction_", "signal_", "model_")


@dataclass(frozen=True)
class RankingConfig:
    min_train_dates: int = 504
    validation_dates: int = 126
    folds: int = 4
    random_seed: int = 42
    icir_min: float = 1.0
    max_drawdown: float = 1.0
    max_turnover: float = 0.70
    transaction_cost_bps: float = 10.0
    long_top_n: int = 10
    short_bottom_n: int = 10
    enable_classifier: bool = True
    allow_short_selling: bool = False
    decision_objective: str = "top_k_portfolio"
    min_positive_spread_folds: int = 3
    min_long_short_spread_ir: float = 0.0
    require_positive_full_ic: bool = False


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
    walk_forward_predictions: pd.DataFrame
    fold_metrics: pd.DataFrame
    feature_audit: pd.DataFrame
    rejected_features: pd.DataFrame
    model_config: dict


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return default if not value else value in {"1", "true", "yes", "y"}


def config_from_env() -> RankingConfig:
    return RankingConfig(
        min_train_dates=int(os.environ.get("STOCKML_RANKER_MIN_TRAIN_DATES", "504")),
        validation_dates=int(os.environ.get("STOCKML_RANKER_VALIDATION_DATES", "126")),
        folds=int(os.environ.get("STOCKML_RANKER_FOLDS", "4")),
        icir_min=float(os.environ.get("STOCKML_RANKER_MIN_ICIR", "1.0")),
        max_drawdown=float(os.environ.get("STOCKML_RANKER_MAX_DRAWDOWN", "1.0")),
        max_turnover=float(os.environ.get("STOCKML_RANKER_MAX_TURNOVER", "0.70")),
        transaction_cost_bps=float(os.environ.get("STOCKML_RANKER_TRANSACTION_COST_BPS", "10")),
        long_top_n=int(os.environ.get("STOCKML_RANKER_LONG_TOP_N", "10")),
        short_bottom_n=int(os.environ.get("STOCKML_RANKER_SHORT_BOTTOM_N", "10")),
        enable_classifier=_bool_env("STOCKML_RANKER_ENABLE_CLASSIFIER", True),
        allow_short_selling=_bool_env("STOCKML_ALLOW_SHORT_SELLING", False),
        decision_objective=os.environ.get("STOCKML_RANKER_DECISION_OBJECTIVE", "top_k_portfolio").strip().lower(),
        min_positive_spread_folds=int(os.environ.get("STOCKML_RANKER_MIN_POSITIVE_SPREAD_FOLDS", "3")),
        min_long_short_spread_ir=float(os.environ.get("STOCKML_RANKER_MIN_LONG_SHORT_SPREAD_IR", "0")),
        require_positive_full_ic=_bool_env("STOCKML_RANKER_REQUIRE_POSITIVE_FULL_IC", False),
    )


def construct_ranking_targets(gold: pd.DataFrame) -> pd.DataFrame:
    frame = gold.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    price_col = "adj_close" if "adj_close" in frame.columns else "close"
    if price_col not in frame.columns:
        raise ValueError("Gold dataset requires adj_close or close to construct 5-day ranking targets.")
    frame = frame.dropna(subset=["date", "ticker"]).sort_values(["ticker", "date"]).reset_index(drop=True)
    if "target_return_5d" not in frame.columns or frame["target_return_5d"].isna().all():
        price = pd.to_numeric(frame[price_col], errors="coerce")
        frame["target_return_5d"] = frame.groupby("ticker")[price_col].shift(-5) / price - 1.0
    else:
        frame["target_return_5d"] = pd.to_numeric(frame["target_return_5d"], errors="coerce")
    frame["target_rank_pct_5d"] = frame.groupby("date")["target_return_5d"].rank(pct=True)
    frame["target_top_quintile_5d"] = (frame["target_rank_pct_5d"] >= 0.80).astype(int)
    frame["target_bottom_quintile_5d"] = (frame["target_rank_pct_5d"] <= 0.20).astype(int)
    frame["target_relevance_5d"] = 1
    frame.loc[frame["target_bottom_quintile_5d"].eq(1), "target_relevance_5d"] = 0
    frame.loc[frame["target_top_quintile_5d"].eq(1), "target_relevance_5d"] = 2
    return frame


def _exclusion_reason(column: str) -> str:
    lower = column.lower()
    if column in IDENTITY_COLUMNS:
        return "identity_column"
    if lower.startswith(PREDICTION_PREFIXES) or lower.startswith("target_"):
        return "target_or_prediction_column"
    if any(token in lower for token in OUTCOME_TOKENS):
        return "future_or_realized_outcome_column"
    if column == "trade_action":
        return "trade_action_column"
    if column in COMPOSITE_SCORE_COLUMNS:
        return "composite_score_excluded_until_audited"
    return ""


def feature_audit(gold: pd.DataFrame) -> tuple[list[str], pd.DataFrame, pd.DataFrame]:
    rows = []
    selected: list[str] = []
    for column in gold.columns:
        reason = _exclusion_reason(column)
        numeric = pd.to_numeric(gold[column], errors="coerce")
        usable_numeric = numeric.notna().any() and numeric.nunique(dropna=True) > 1
        if not reason and not usable_numeric:
            reason = "not_numeric_or_constant"
        included = not reason
        if included:
            selected.append(column)
        rows.append(
            {
                "feature_name": column,
                "dtype": str(gold[column].dtype),
                "missing_rate": float(gold[column].isna().mean()),
                "included": included,
                "exclusion_reason": reason,
                "leakage_risk_level": "low" if included else ("high" if "target" in reason or "outcome" in reason else "medium"),
            }
        )
    audit = pd.DataFrame(rows)
    return selected, audit, audit[~audit["included"]].copy()


def walk_forward_splits(dates: Iterable, config: RankingConfig) -> list[dict]:
    unique_dates = pd.Series(pd.to_datetime(pd.Series(dates).dropna().unique())).sort_values().reset_index(drop=True)
    if len(unique_dates) < config.min_train_dates + config.validation_dates:
        return []
    max_start = len(unique_dates) - config.validation_dates
    starts = np.linspace(config.min_train_dates, max_start, num=min(config.folds, max_start - config.min_train_dates + 1), dtype=int)
    folds = []
    for fold, start in enumerate(sorted(set(starts.tolist())), start=1):
        end = min(start + config.validation_dates - 1, len(unique_dates) - 1)
        folds.append(
            {
                "fold": fold,
                "train_start": unique_dates.iloc[0],
                "train_end": unique_dates.iloc[start - 1],
                "validation_start": unique_dates.iloc[start],
                "validation_end": unique_dates.iloc[end],
            }
        )
    return folds


def _load_lgbm_ranker(config: RankingConfig):
    try:
        from lightgbm import LGBMRanker  # type: ignore
    except Exception:
        return None
    return LGBMRanker(
        objective="lambdarank",
        metric="ndcg",
        n_estimators=180,
        learning_rate=0.05,
        num_leaves=31,
        random_state=config.random_seed,
        n_jobs=1,
        verbosity=-1,
    )


def _prepare_xy(frame: pd.DataFrame, features: list[str]) -> tuple[pd.DataFrame, pd.Series]:
    x = frame[features].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0)
    y = pd.to_numeric(frame["target_relevance_5d"], errors="coerce").fillna(1).astype(int)
    return x, y


def _group_sizes(frame: pd.DataFrame) -> list[int]:
    return frame.sort_values(["date", "ticker"]).groupby("date", sort=True).size().astype(int).tolist()


def _dcg(relevance: np.ndarray, k: int) -> float:
    rel = relevance[:k]
    discounts = np.log2(np.arange(2, len(rel) + 2))
    return float(((2**rel - 1) / discounts).sum()) if len(rel) else 0.0


def _ndcg(relevance: np.ndarray, scores: np.ndarray, k: int) -> float:
    order = np.argsort(scores)[::-1]
    best = np.argsort(relevance)[::-1]
    ideal = _dcg(relevance[best], k)
    return _dcg(relevance[order], k) / ideal if ideal > 0 else 0.0


def _precision_top(frame: pd.DataFrame, k: int) -> float:
    values = []
    for _, day in frame.groupby("date"):
        top = day.nlargest(min(k, len(day)), "model_score")
        values.append(float(top["target_top_quintile_5d"].mean()) if not top.empty else 0.0)
    return float(np.mean(values)) if values else 0.0


def _avg_return_top(frame: pd.DataFrame, k: int, top: bool = True) -> float:
    values = []
    for _, day in frame.groupby("date"):
        selected = day.nlargest(min(k, len(day)), "model_score") if top else day.nsmallest(min(k, len(day)), "model_score")
        values.append(float(selected["target_return_5d"].mean()) if not selected.empty else 0.0)
    return float(np.mean(values)) if values else 0.0


def _daily_long_short_returns(frame: pd.DataFrame, k: int = 25) -> pd.Series:
    rows = []
    for date, day in frame.groupby("date"):
        long_ret = day.nlargest(min(k, len(day)), "model_score")["target_return_5d"].mean()
        short_ret = day.nsmallest(min(k, len(day)), "model_score")["target_return_5d"].mean()
        rows.append((date, float(long_ret - short_ret)))
    return pd.Series(dict(rows)).sort_index()


def _max_drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    equity = (1 + returns.fillna(0)).cumprod()
    drawdown = equity / equity.cummax() - 1
    return float(abs(drawdown.min()))


def _turnover(frame: pd.DataFrame, k: int = 25) -> float:
    sets = frame.groupby("date").apply(lambda d: set(d.nlargest(min(k, len(d)), "model_score")["ticker"].astype(str))).tolist()
    values = []
    previous = None
    for current in sets:
        if previous:
            values.append(1 - len(previous & current) / max(1, len(previous)))
        previous = current
    return float(np.mean(values)) if values else 0.0


def _score_metrics(frame: pd.DataFrame, model_name: str, config: RankingConfig) -> dict:
    daily_ic = frame.groupby("date").apply(lambda d: d[["model_score", "target_return_5d"]].corr(method="spearman").iloc[0, 1])
    daily_ic = daily_ic.replace([np.inf, -np.inf], np.nan).dropna()
    ic_mean = float(daily_ic.mean()) if not daily_ic.empty else 0.0
    ic_std = float(daily_ic.std()) if len(daily_ic) > 1 else 0.0
    turnover = _turnover(frame)
    ls_returns = _daily_long_short_returns(frame)
    spread = float(ls_returns.mean()) if not ls_returns.empty else 0.0
    spread_std = float(ls_returns.std()) if len(ls_returns) > 1 else 0.0
    tc_spread = spread - turnover * config.transaction_cost_bps / 10000
    metrics = {
        "model_name": model_name,
        "daily_spearman_ic_mean": ic_mean,
        "daily_spearman_ic_std": ic_std,
        "icir_5d": ic_mean / ic_std if ic_std else 0.0,
        "positive_ic_day_fraction": float((daily_ic > 0).mean()) if not daily_ic.empty else 0.0,
        "precision_at_10": _precision_top(frame, 10),
        "precision_at_25": _precision_top(frame, 25),
        "precision_at_50": _precision_top(frame, 50),
        "ndcg_at_10": float(np.mean([_ndcg(d["target_relevance_5d"].to_numpy(), d["model_score"].to_numpy(), 10) for _, d in frame.groupby("date")])),
        "ndcg_at_25": float(np.mean([_ndcg(d["target_relevance_5d"].to_numpy(), d["model_score"].to_numpy(), 25) for _, d in frame.groupby("date")])),
        "ndcg_at_50": float(np.mean([_ndcg(d["target_relevance_5d"].to_numpy(), d["model_score"].to_numpy(), 50) for _, d in frame.groupby("date")])),
        "avg_return_top_10": _avg_return_top(frame, 10, True),
        "avg_return_top_25": _avg_return_top(frame, 25, True),
        "avg_return_top_50": _avg_return_top(frame, 50, True),
        "avg_return_bottom_10": _avg_return_top(frame, 10, False),
        "avg_return_bottom_25": _avg_return_top(frame, 25, False),
        "avg_return_bottom_50": _avg_return_top(frame, 50, False),
        "long_short_spread": spread,
        "long_short_spread_std": spread_std,
        "long_short_spread_ir": spread / spread_std if spread_std else 0.0,
        "turnover": turnover,
        "tc_adjusted_long_short_spread": tc_spread,
        "avg_realized_gain_5d": _avg_return_top(frame, 25, True),
        "turnover_adjusted_avg_gain_5d": tc_spread,
        "hit_rate": _precision_top(frame, 25),
        "max_drawdown": _max_drawdown(ls_returns),
    }
    return metrics


def _baseline_scores(frame: pd.DataFrame) -> dict[str, pd.Series]:
    values = {}
    for col in ["return_20d", "return_60d", "relative_return_vs_sector_20d"]:
        if col in frame.columns:
            values[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0)
    available = [values[col] for col in values]
    if available:
        values["equal_weight_momentum_composite"] = pd.concat(available, axis=1).mean(axis=1)
    return values


def _fit_classifier(train: pd.DataFrame, features: list[str], config: RankingConfig):
    if not config.enable_classifier:
        return None
    x, _ = _prepare_xy(train, features)
    y = pd.to_numeric(train["target_top_quintile_5d"], errors="coerce").fillna(0).astype(int)
    if y.nunique() < 2:
        return None
    model = Pipeline([
        ("scale", StandardScaler()),
        ("model", LogisticRegression(max_iter=1000, class_weight="balanced", random_state=config.random_seed)),
    ])
    model.fit(x, y)
    return model


def walk_forward_validate(gold: pd.DataFrame, features: list[str], config: RankingConfig) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str]]:
    rows = []
    scored_frames = []
    warnings = []
    lgbm_available = _load_lgbm_ranker(config) is not None
    for fold in walk_forward_splits(gold["date"], config):
        train = gold[(gold["date"] >= fold["train_start"]) & (gold["date"] <= fold["train_end"])].sort_values(["date", "ticker"]).copy()
        valid = gold[(gold["date"] >= fold["validation_start"]) & (gold["date"] <= fold["validation_end"])].sort_values(["date", "ticker"]).copy()
        if train.empty or valid.empty:
            continue
        fold_predictions = []
        for model_name, scores in _baseline_scores(valid).items():
            scored = valid.copy()
            scored["model_name"] = model_name
            scored["model_score"] = scores.to_numpy()
            fold_predictions.append(scored)
            rows.append({**fold, **_score_metrics(scored, model_name, config), "validation_rows": len(valid), "train_rows": len(train)})
        ranker = _load_lgbm_ranker(config)
        if ranker is None:
            warnings.append("lightgbm_missing")
        else:
            x_train, y_train = _prepare_xy(train, features)
            x_valid, _ = _prepare_xy(valid, features)
            ranker.fit(x_train, y_train, group=_group_sizes(train), eval_group=[_group_sizes(valid)], eval_set=[(x_valid, valid["target_relevance_5d"].astype(int))], eval_at=[10, 25, 50])
            scored = valid.copy()
            scored["model_name"] = "lightgbm_lambdarank"
            scored["model_score"] = ranker.predict(x_valid)
            classifier = _fit_classifier(train, features, config)
            if classifier is not None:
                scored["predicted_top_quintile_probability"] = classifier.predict_proba(x_valid)[:, 1]
            fold_predictions.append(scored)
            rows.append({**fold, **_score_metrics(scored, "lightgbm_lambdarank", config), "validation_rows": len(valid), "train_rows": len(train)})
        scored_frames.extend(fold_predictions)
    metrics = pd.DataFrame(rows)
    predictions = pd.concat(scored_frames, ignore_index=True) if scored_frames else pd.DataFrame()
    return metrics, predictions, metrics.copy(), sorted(set(warnings))


def _decision_gates(leaderboard: pd.DataFrame, config: RankingConfig) -> tuple[bool, str]:
    if leaderboard.empty or "lightgbm_lambdarank" not in set(leaderboard.get("model_name", [])):
        return False, "lightgbm_missing_or_no_validation"
    model = leaderboard[leaderboard["model_name"].eq("lightgbm_lambdarank")].copy()
    baseline = leaderboard[leaderboard["model_name"].isin(["return_20d", "return_60d"])]
    model_overall = model.mean(numeric_only=True).to_dict()
    reasons = []
    if not baseline.empty and model_overall.get("long_short_spread", 0) <= baseline.groupby("model_name")["long_short_spread"].mean().max():
        reasons.append("baseline_not_beaten")
    if config.require_positive_full_ic or config.decision_objective == "full_rank_ic":
        if model_overall.get("daily_spearman_ic_mean", 0) <= 0:
            reasons.append("mean_ic_not_positive")
        if model_overall.get("icir_5d", 0) < config.icir_min:
            reasons.append("icir_below_threshold")
    if model_overall.get("long_short_spread", 0) <= 0:
        reasons.append("long_short_spread_not_positive")
    if model_overall.get("tc_adjusted_long_short_spread", 0) <= 0:
        reasons.append("transaction_cost_adjusted_spread_not_positive")
    if model_overall.get("long_short_spread_ir", 0) < config.min_long_short_spread_ir:
        reasons.append("long_short_spread_ir_below_threshold")
    if int((model["long_short_spread"] > 0).sum()) < min(config.min_positive_spread_folds, len(model)):
        reasons.append("positive_spread_folds_below_threshold")
    if model_overall.get("max_drawdown", 1) > config.max_drawdown:
        reasons.append("drawdown_above_threshold")
    if model_overall.get("turnover", 1) > config.max_turnover:
        reasons.append("turnover_above_threshold")
    if reasons:
        return False, "|".join(reasons)
    if model_overall.get("daily_spearman_ic_mean", 0) <= 0:
        return True, "portfolio_validation_gates_passed|full_rank_ic_not_positive"
    if model_overall.get("icir_5d", 0) < config.icir_min:
        return True, "portfolio_validation_gates_passed|full_rank_icir_below_threshold"
    return True, "validation_gates_passed"


def _train_final_scores(gold: pd.DataFrame, latest: pd.DataFrame, features: list[str], config: RankingConfig) -> tuple[pd.Series, pd.Series | None, str]:
    ranker = _load_lgbm_ranker(config)
    if ranker is None:
        scores = _baseline_scores(latest).get("equal_weight_momentum_composite", pd.Series(0, index=latest.index))
        return scores, None, "equal_weight_momentum_composite"
    x_train, y_train = _prepare_xy(gold, features)
    ranker.fit(x_train, y_train, group=_group_sizes(gold))
    x_latest, _ = _prepare_xy(latest, features)
    probability = None
    classifier = _fit_classifier(gold, features, config)
    if classifier is not None:
        probability = pd.Series(classifier.predict_proba(x_latest)[:, 1], index=latest.index)
    return pd.Series(ranker.predict(x_latest), index=latest.index), probability, "lightgbm_lambdarank"


def _liquidity_strength(frame: pd.DataFrame) -> pd.Series:
    for col in ["liquidity_score", "avg_dollar_volume_20d", "dollar_volume"]:
        if col in frame.columns:
            values = pd.to_numeric(frame[col], errors="coerce").fillna(0)
            return values.rank(pct=True).fillna(0.5)
    return pd.Series(0.5, index=frame.index)


def train_predict_from_gold(gold: pd.DataFrame, top_n: int = 50, config: RankingConfig | None = None) -> ModelArtifacts:
    cfg = config or config_from_env()
    prepared = construct_ranking_targets(gold)
    trainable = prepared.dropna(subset=["target_return_5d", "target_rank_pct_5d"]).copy()
    features, audit, rejected = feature_audit(trainable)
    if not features:
        raise ValueError("No usable non-leaking numeric features found for ranking model.")
    fold_metrics, wf_predictions, leaderboard, warnings = walk_forward_validate(trainable, features, cfg)
    gates_passed, gate_reason = _decision_gates(leaderboard, cfg)
    latest_date = prepared["date"].max()
    latest = prepared[prepared["date"].eq(latest_date)].copy()
    scores, top_prob, selected_model = _train_final_scores(trainable, latest, features, cfg)
    latest["model_score"] = scores
    latest["rank_overall"] = latest["model_score"].rank(ascending=False, method="first")
    latest["rank_by_sector"] = latest.groupby("sector")["model_score"].rank(ascending=False, method="first") if "sector" in latest.columns else latest["rank_overall"]
    latest["predicted_rank_pct_by_date"] = latest["model_score"].rank(pct=True)
    latest["predicted_top_quintile_probability"] = top_prob if top_prob is not None else pd.NA
    latest["trade_action"] = "No Decision"
    latest["signal"] = "HOLD"
    latest["signal_reason"] = "validation_gates_passed" if gates_passed else "model_not_decision_grade"
    latest["no_decision_reason"] = "" if gates_passed else gate_reason
    if gates_passed:
        long_mask = latest["rank_overall"] <= cfg.long_top_n
        short_mask = (latest["rank_overall"] > max(len(latest) - cfg.short_bottom_n, 0)) if cfg.allow_short_selling else pd.Series(False, index=latest.index)
        latest.loc[long_mask, ["trade_action", "signal", "signal_reason", "no_decision_reason"]] = ["Long", "LONG", "rank_validation_gate_passed", ""]
        latest.loc[short_mask, ["trade_action", "signal", "signal_reason", "no_decision_reason"]] = ["Short", "SHORT", "rank_validation_gate_passed", ""]
    latest["confidence"] = (
        latest["predicted_rank_pct_by_date"].sub(0.5).abs().mul(2).fillna(0)
        * 0.5
        + _liquidity_strength(latest).mul(0.25)
        + (max(0.0, leaderboard[leaderboard["model_name"].eq("lightgbm_lambdarank")]["icir_5d"].mean()) if not leaderboard.empty else 0) / 4
    ).clip(0, 1)
    latest["confidence_score"] = latest["confidence"]
    latest["calibrated_probability_up_5d"] = latest["predicted_top_quintile_probability"]
    latest["probability_down_5d"] = 1 - pd.to_numeric(latest["predicted_top_quintile_probability"], errors="coerce") if top_prob is not None else pd.NA
    latest["side_probability"] = latest[["calibrated_probability_up_5d", "probability_down_5d"]].max(axis=1, skipna=True)
    latest["probability_edge"] = pd.to_numeric(latest["calibrated_probability_up_5d"], errors="coerce") - 0.5
    latest["reason"] = latest.apply(lambda r: f"rank={int(r['rank_overall'])}; score={r['model_score']:.4f}; gate={gate_reason}", axis=1)
    return_scale = float(trainable["target_return_5d"].std(skipna=True) or 0)
    latest["expected_trade_return"] = latest["model_score"] * return_scale
    latest["risk_adjusted_score"] = latest["expected_trade_return"] / (1 + latest["rank_overall"])
    predictions = latest.copy()
    top_long = latest[latest["trade_action"].eq("Long")].sort_values("rank_overall").head(top_n)
    top_short = latest[latest["trade_action"].eq("Short")].sort_values("rank_overall", ascending=False).head(top_n)
    model_rows = leaderboard.groupby("model_name", as_index=False).mean(numeric_only=True) if not leaderboard.empty else pd.DataFrame()
    model_rows["decision_grade"] = "decision_grade" if gates_passed else "diagnostic_only"
    model_rows["reason"] = gate_reason
    if model_rows.empty:
        model_rows = pd.DataFrame([{"model_name": selected_model, "decision_grade": "diagnostic_only", "reason": gate_reason}])
    status = pd.DataFrame([{
        "decision_grade": "decision_grade" if gates_passed else "diagnostic_only",
        "diagnostic_only": not gates_passed,
        "selected_model": selected_model,
        "model_version": "cross_sectional_lambdarank_v2",
        "validation_window": "expanding_walk_forward",
        "folds_completed": int(fold_metrics["fold"].nunique()) if not fold_metrics.empty else 0,
        "beats_baseline": gates_passed,
        "reason": gate_reason,
        "gold_input_rows": len(gold),
        "eligible_training_rows": len(trainable),
        "feature_count": len(features),
        "warnings": "|".join(warnings),
    }])
    buckets = pd.DataFrame()
    if not wf_predictions.empty:
        wf_predictions["confidence_bucket"] = pd.qcut(wf_predictions["model_score"].rank(method="first"), q=5, labels=False, duplicates="drop")
        buckets = wf_predictions.groupby(["model_name", "confidence_bucket"], as_index=False).agg(
            row_count=("ticker", "size"),
            hit_rate=("target_top_quintile_5d", "mean"),
            avg_realized_gain_5d=("target_return_5d", "mean"),
        )
    importance = pd.DataFrame({"feature": features, "importance": 0.0})
    dictionary = pd.DataFrame({"column": predictions.columns, "source": "ranking_model_prediction"})
    model_config = {**cfg.__dict__, "selected_model": selected_model, "feature_count": len(features), "lightgbm_available": "lightgbm_missing" not in warnings}
    return ModelArtifacts(
        predictions=predictions,
        signal_table=latest,
        top_long=top_long,
        top_short=top_short,
        validation_leaderboard=model_rows,
        bucket_performance=buckets,
        feature_importance=importance,
        model_status=status,
        data_dictionary=dictionary,
        walk_forward_predictions=wf_predictions,
        fold_metrics=fold_metrics,
        feature_audit=audit,
        rejected_features=rejected,
        model_config=model_config,
    )


def model_config_json(config: dict) -> str:
    return json.dumps(config, indent=2, sort_keys=True, default=str)
