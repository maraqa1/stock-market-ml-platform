from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Optional

from stockml.common.logging_utils import log
from stockml.common.paths import MODEL_OUTPUTS_DIR, ensure_data_dirs, timestamp
from stockml.models.gold_loader import load_gold_dataset
from stockml.models.meta_labeling import add_meta_label_predictions, load_meta_label_config, train_meta_label_model
from stockml.models.meta_label_validation import walk_forward_validate_meta_labels
from stockml.models.ranking_model import ModelArtifacts, model_config_json, train_predict_from_gold


def _write_artifacts(artifacts: ModelArtifacts, stamp: str) -> Dict[str, Path]:
    outputs = {
        "predictions": MODEL_OUTPUTS_DIR / f"advanced_model_latest_predictions_{stamp}.csv",
        "signal_table": MODEL_OUTPUTS_DIR / f"advanced_model_signal_table_{stamp}.csv",
        "top_long": MODEL_OUTPUTS_DIR / f"advanced_model_top_long_signals_{stamp}.csv",
        "top_short": MODEL_OUTPUTS_DIR / f"advanced_model_top_short_signals_{stamp}.csv",
        "validation_leaderboard": MODEL_OUTPUTS_DIR / f"advanced_model_validation_leaderboard_{stamp}.csv",
        "confidence_bucket_performance": MODEL_OUTPUTS_DIR / f"advanced_model_confidence_bucket_performance_{stamp}.csv",
        "feature_importance": MODEL_OUTPUTS_DIR / f"advanced_model_feature_importance_{stamp}.csv",
        "model_status": MODEL_OUTPUTS_DIR / f"advanced_model_model_status_{stamp}.csv",
        "data_dictionary": MODEL_OUTPUTS_DIR / f"advanced_model_data_dictionary_{stamp}.csv",
        "walk_forward_predictions": MODEL_OUTPUTS_DIR / f"walk_forward_predictions_{stamp}.csv",
        "fold_metrics": MODEL_OUTPUTS_DIR / f"fold_metrics_{stamp}.csv",
        "feature_audit": MODEL_OUTPUTS_DIR / f"feature_audit_{stamp}.csv",
        "rejected_features": MODEL_OUTPUTS_DIR / f"rejected_features_{stamp}.csv",
        "model_config": MODEL_OUTPUTS_DIR / f"model_config_{stamp}.json",
        "model_predictions_latest": MODEL_OUTPUTS_DIR / "model_predictions_latest.csv",
        "validation_leaderboard_latest": MODEL_OUTPUTS_DIR / "validation_leaderboard.csv",
        "feature_audit_latest": MODEL_OUTPUTS_DIR / "feature_audit.csv",
        "rejected_features_latest": MODEL_OUTPUTS_DIR / "rejected_features.csv",
    }
    artifacts.predictions.to_csv(outputs["predictions"], index=False)
    artifacts.signal_table.to_csv(outputs["signal_table"], index=False)
    artifacts.top_long.to_csv(outputs["top_long"], index=False)
    artifacts.top_short.to_csv(outputs["top_short"], index=False)
    artifacts.validation_leaderboard.to_csv(outputs["validation_leaderboard"], index=False)
    artifacts.bucket_performance.to_csv(outputs["confidence_bucket_performance"], index=False)
    artifacts.feature_importance.to_csv(outputs["feature_importance"], index=False)
    artifacts.model_status.to_csv(outputs["model_status"], index=False)
    artifacts.data_dictionary.to_csv(outputs["data_dictionary"], index=False)
    artifacts.walk_forward_predictions.to_csv(outputs["walk_forward_predictions"], index=False)
    artifacts.fold_metrics.to_csv(outputs["fold_metrics"], index=False)
    artifacts.feature_audit.to_csv(outputs["feature_audit"], index=False)
    artifacts.rejected_features.to_csv(outputs["rejected_features"], index=False)
    outputs["model_config"].write_text(model_config_json(artifacts.model_config), encoding="utf-8")
    artifacts.predictions.to_csv(outputs["model_predictions_latest"], index=False)
    artifacts.validation_leaderboard.to_csv(outputs["validation_leaderboard_latest"], index=False)
    artifacts.feature_audit.to_csv(outputs["feature_audit_latest"], index=False)
    artifacts.rejected_features.to_csv(outputs["rejected_features_latest"], index=False)
    return outputs


def _add_meta_label_artifacts(artifacts: ModelArtifacts, stamp: str) -> Dict[str, Path]:
    cfg = load_meta_label_config()
    outputs = {
        "meta_label_predictions": MODEL_OUTPUTS_DIR / f"meta_label_predictions_{stamp}.csv",
        "meta_label_validation": MODEL_OUTPUTS_DIR / f"meta_label_validation_{stamp}.csv",
        "meta_label_bucket_performance": MODEL_OUTPUTS_DIR / f"meta_label_bucket_performance_{stamp}.csv",
    }
    history = artifacts.walk_forward_predictions.copy()
    if not history.empty and "trade_action" not in history.columns and "model_score" in history.columns:
        history["rank_overall"] = history.groupby("date")["model_score"].rank(ascending=False, method="first")
        history["trade_action"] = "No Decision"
        history.loc[history["rank_overall"].le(10), "trade_action"] = "Long"
        history.loc[history["rank_overall"].gt(history.groupby("date")["rank_overall"].transform("max") - 10), "trade_action"] = "Short"
        history["confidence_score"] = history.get("confidence_score", history["rank_overall"].rank(pct=True))
        history["side_probability"] = history.get("side_probability", 0.6)
        history["probability_edge"] = history.get("probability_edge", 0.1)
        history["expected_trade_return"] = history.get("expected_trade_return", history["model_score"])
        history["risk_adjusted_score"] = history.get("risk_adjusted_score", history["model_score"])
    fit = train_meta_label_model(history, cfg)
    validation, predictions, buckets = walk_forward_validate_meta_labels(history, cfg)
    artifacts.signal_table = add_meta_label_predictions(artifacts.signal_table, fit, cfg)
    artifacts.predictions = add_meta_label_predictions(artifacts.predictions, fit, cfg)
    artifacts.top_long = artifacts.signal_table[artifacts.signal_table["trade_action"].eq("Long")].sort_values("rank_overall").head(len(artifacts.top_long) or 50)
    artifacts.top_short = artifacts.signal_table[artifacts.signal_table["trade_action"].eq("Short")].sort_values("rank_overall", ascending=False).head(len(artifacts.top_short) or 50)
    predictions.to_csv(outputs["meta_label_predictions"], index=False)
    validation.to_csv(outputs["meta_label_validation"], index=False)
    buckets.to_csv(outputs["meta_label_bucket_performance"], index=False)
    artifacts.model_config["meta_labeling"] = {
        "enabled": cfg.enabled,
        "min_meta_label_probability": cfg.min_meta_label_probability,
        "transaction_cost_bps": cfg.transaction_cost_bps,
        "embargo_days": cfg.embargo_days,
        "min_training_signals": cfg.min_training_signals,
        "fitted": fit.fitted,
        "reason": fit.reason,
        "validation_rows": len(validation),
    }
    return outputs


def build_model_outputs(gold_file: Optional[Path] = None, limit_tickers: Optional[int] = None, top_n: int = 50) -> Dict[str, Path]:
    ensure_data_dirs()
    stamp = timestamp()
    gold = load_gold_dataset(gold_file, limit_tickers=limit_tickers)
    log(f"Loaded Gold dataset for model: {len(gold):,} rows")
    artifacts = train_predict_from_gold(gold, top_n=top_n)
    meta_paths = _add_meta_label_artifacts(artifacts, stamp)
    paths = _write_artifacts(artifacts, stamp)
    paths.update(meta_paths)
    for name, path in paths.items():
        log(f"{name}: {path}")
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold-file", type=Path, default=None)
    parser.add_argument("--limit-tickers", type=int, default=None)
    parser.add_argument("--top-n", type=int, default=50)
    args = parser.parse_args()
    build_model_outputs(gold_file=args.gold_file, limit_tickers=args.limit_tickers, top_n=args.top_n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
