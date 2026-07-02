from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from stockml.common.logging_utils import log
from stockml.common.paths import MODEL_OUTPUTS_DIR, ensure_data_dirs, timestamp
from stockml.models.gold_direction_memory import enrich_gold_direction_memory_fields
from stockml.models.gold_loader import load_gold_dataset
from stockml.models.meta_labeling import add_meta_label_predictions, load_meta_label_config, train_meta_label_model
from stockml.models.meta_label_validation import walk_forward_validate_meta_labels
from stockml.models.ranking_model import (
    ModelArtifacts,
    apply_directional_signal_fields,
    config_from_env,
    model_config_json,
    train_predict_from_gold,
)


def _enrich_artifact_direction_memory(artifacts: ModelArtifacts) -> ModelArtifacts:
    artifacts.predictions = enrich_gold_direction_memory_fields(artifacts.predictions)
    artifacts.signal_table = enrich_gold_direction_memory_fields(artifacts.signal_table)
    artifacts.top_long = enrich_gold_direction_memory_fields(artifacts.top_long)
    artifacts.top_short = enrich_gold_direction_memory_fields(artifacts.top_short)
    return artifacts


def _write_artifacts(artifacts: ModelArtifacts, stamp: str, *, publish_latest: bool = True) -> Dict[str, Path]:
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
    }
    latest_outputs = {
        "model_predictions_latest": MODEL_OUTPUTS_DIR / "model_predictions_latest.csv",
        "validation_leaderboard_latest": MODEL_OUTPUTS_DIR / "validation_leaderboard.csv",
        "feature_audit_latest": MODEL_OUTPUTS_DIR / "feature_audit.csv",
        "rejected_features_latest": MODEL_OUTPUTS_DIR / "rejected_features.csv",
    }
    if publish_latest:
        outputs.update(latest_outputs)
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
    if publish_latest:
        artifacts.predictions.to_csv(outputs["model_predictions_latest"], index=False)
        artifacts.validation_leaderboard.to_csv(outputs["validation_leaderboard_latest"], index=False)
        artifacts.feature_audit.to_csv(outputs["feature_audit_latest"], index=False)
        artifacts.rejected_features.to_csv(outputs["rejected_features_latest"], index=False)
    return outputs


def _with_shard(frame: pd.DataFrame, shard_index: int) -> pd.DataFrame:
    out = frame.copy()
    out["model_shard"] = shard_index
    return out


def _combine_shard_artifacts(shards: list[ModelArtifacts], *, top_n: int) -> ModelArtifacts:
    if not shards:
        raise ValueError("No model shards were produced.")
    base = shards[0]

    predictions = pd.concat([_with_shard(artifact.predictions, i) for i, artifact in enumerate(shards)], ignore_index=True)
    signal_table = pd.concat([_with_shard(artifact.signal_table, i) for i, artifact in enumerate(shards)], ignore_index=True)
    validation = pd.concat([_with_shard(artifact.validation_leaderboard, i) for i, artifact in enumerate(shards)], ignore_index=True)
    buckets = pd.concat([_with_shard(artifact.bucket_performance, i) for i, artifact in enumerate(shards)], ignore_index=True)
    importance = pd.concat([_with_shard(artifact.feature_importance, i) for i, artifact in enumerate(shards)], ignore_index=True)
    status = pd.concat([_with_shard(artifact.model_status, i) for i, artifact in enumerate(shards)], ignore_index=True)
    dictionary = pd.concat([_with_shard(artifact.data_dictionary, i) for i, artifact in enumerate(shards)], ignore_index=True).drop_duplicates("column", keep="first")
    walk_forward = pd.concat([_with_shard(artifact.walk_forward_predictions, i) for i, artifact in enumerate(shards)], ignore_index=True)
    fold_metrics = pd.concat([_with_shard(artifact.fold_metrics, i) for i, artifact in enumerate(shards)], ignore_index=True)
    audit = pd.concat([_with_shard(artifact.feature_audit, i) for i, artifact in enumerate(shards)], ignore_index=True)
    rejected = pd.concat([_with_shard(artifact.rejected_features, i) for i, artifact in enumerate(shards)], ignore_index=True)

    if not signal_table.empty and "model_score" in signal_table.columns:
        signal_table = signal_table.sort_values("model_score", ascending=False).reset_index(drop=True)
        signal_table["rank_overall"] = range(1, len(signal_table) + 1)
        signal_table["predicted_rank_pct_by_date"] = signal_table["model_score"].rank(pct=True)
        decision_grade = status.get("decision_grade", pd.Series(dtype=str)).astype(str).eq("decision_grade").any()
        signal_table["trade_action"] = "No Decision"
        signal_table["signal"] = "HOLD"
        signal_table["signal_reason"] = "model_not_decision_grade"
        signal_table["no_decision_reason"] = "sharded_model_diagnostic"
        if decision_grade:
            signal_table["signal_reason"] = "validation_gates_passed"
            signal_table["no_decision_reason"] = ""
            signal_table.loc[signal_table["rank_overall"].le(10), ["trade_action", "signal", "signal_reason"]] = ["Long", "LONG", "rank_validation_gate_passed"]
            signal_table.loc[signal_table["rank_overall"].gt(max(len(signal_table) - 10, 0)), ["trade_action", "signal", "signal_reason"]] = ["Short", "SHORT", "rank_validation_gate_passed"]
        signal_table = apply_directional_signal_fields(signal_table, config_from_env(), gates_passed=decision_grade)
        predictions = signal_table.copy()

    top_long = signal_table[signal_table["trade_action"].eq("Long")].sort_values("rank_overall").head(top_n) if "trade_action" in signal_table.columns else pd.DataFrame()
    top_short = signal_table[signal_table["trade_action"].eq("Short")].sort_values("rank_overall", ascending=False).head(top_n) if "trade_action" in signal_table.columns else pd.DataFrame()

    config = dict(base.model_config)
    config.update({"model_shards": len(shards), "sharded_model": True})
    return ModelArtifacts(
        predictions=predictions,
        signal_table=signal_table,
        top_long=top_long,
        top_short=top_short,
        validation_leaderboard=validation,
        bucket_performance=buckets,
        feature_importance=importance,
        model_status=status,
        data_dictionary=dictionary,
        walk_forward_predictions=walk_forward,
        fold_metrics=fold_metrics,
        feature_audit=audit,
        rejected_features=rejected,
        model_config=config,
    )


def _add_meta_label_artifacts(artifacts: ModelArtifacts, stamp: str, *, skip_validation: bool = False) -> Dict[str, Path]:
    cfg = load_meta_label_config()
    outputs = {
        "meta_label_predictions": MODEL_OUTPUTS_DIR / f"meta_label_predictions_{stamp}.csv",
        "meta_label_validation": MODEL_OUTPUTS_DIR / f"meta_label_validation_{stamp}.csv",
        "meta_label_bucket_performance": MODEL_OUTPUTS_DIR / f"meta_label_bucket_performance_{stamp}.csv",
    }
    history = artifacts.walk_forward_predictions.copy()
    if skip_validation:
        artifacts.signal_table["meta_label_probability"] = pd.NA
        artifacts.signal_table["meta_label_decision"] = "Take Trade"
        artifacts.signal_table["meta_label_reason"] = "live_signal_mode_meta_label_skipped"
        artifacts.predictions = artifacts.signal_table.copy()
        artifacts.top_long = artifacts.signal_table[artifacts.signal_table["trade_action"].eq("Long")].sort_values("rank_overall").head(len(artifacts.top_long) or 50)
        artifacts.top_short = artifacts.signal_table[artifacts.signal_table["trade_action"].eq("Short")].sort_values("rank_overall", ascending=False).head(len(artifacts.top_short) or 50)
        pd.DataFrame().to_csv(outputs["meta_label_predictions"], index=False)
        pd.DataFrame().to_csv(outputs["meta_label_validation"], index=False)
        pd.DataFrame().to_csv(outputs["meta_label_bucket_performance"], index=False)
        artifacts.model_config["meta_labeling"] = {
            "enabled": cfg.enabled,
            "fitted": False,
            "reason": "live_signal_mode_meta_label_skipped",
            "validation_rows": 0,
        }
        return outputs
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


def build_model_outputs(
    gold_file: Optional[Path] = None,
    limit_tickers: Optional[int] = None,
    top_n: int = 50,
    model_shards: int = 1,
    live_signal_mode: bool = False,
    baseline_only: bool = False,
    publish_latest: bool = True,
) -> Dict[str, Path]:
    ensure_data_dirs()
    stamp = timestamp()
    meta_paths: Dict[str, Path] = {}
    if model_shards > 1:
        shard_artifacts = []
        for shard_index in range(model_shards):
            gold = load_gold_dataset(gold_file, limit_tickers=limit_tickers, shard_count=model_shards, shard_index=shard_index)
            log(f"Loaded Gold dataset for model shard {shard_index + 1}/{model_shards}: {len(gold):,} rows")
            artifact = train_predict_from_gold(
                gold,
                top_n=top_n,
                live_signal_mode=live_signal_mode,
                baseline_only=baseline_only,
            )
            shard_meta_paths = _add_meta_label_artifacts(
                artifact,
                f"{stamp}_shard{shard_index + 1}",
                skip_validation=live_signal_mode,
            )
            meta_paths.update({f"shard_{shard_index + 1}_{name}": path for name, path in shard_meta_paths.items()})

            # The full NYSE walk-forward history is several GB when all shards
            # are concatenated. Keep shard-level meta outputs on disk and only
            # combine the lightweight live signal artifacts in memory.
            artifact.walk_forward_predictions = pd.DataFrame(columns=artifact.walk_forward_predictions.columns)
            shard_artifacts.append(artifact)
        artifacts = _combine_shard_artifacts(shard_artifacts, top_n=top_n)
        artifacts.model_config.setdefault("meta_labeling", {})
        artifacts.model_config["meta_labeling"].update({"mode": "per_shard", "global_validation_skipped": True})
        artifacts.model_config["live_signal_mode"] = live_signal_mode
        artifacts.model_config["baseline_only"] = baseline_only
    else:
        gold = load_gold_dataset(gold_file, limit_tickers=limit_tickers)
        log(f"Loaded Gold dataset for model: {len(gold):,} rows")
        artifacts = train_predict_from_gold(gold, top_n=top_n, live_signal_mode=live_signal_mode, baseline_only=baseline_only)
        meta_paths = _add_meta_label_artifacts(artifacts, stamp, skip_validation=live_signal_mode)
    artifacts = _enrich_artifact_direction_memory(artifacts)
    paths = _write_artifacts(artifacts, stamp, publish_latest=publish_latest)
    paths.update(meta_paths)
    for name, path in paths.items():
        log(f"{name}: {path}")
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold-file", type=Path, default=None)
    parser.add_argument("--limit-tickers", type=int, default=None)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--model-shards", type=int, default=1)
    parser.add_argument("--live-signal-mode", action="store_true", help="Skip expensive walk-forward/meta validation and produce live rankings.")
    parser.add_argument("--baseline-only", action="store_true", help="Use baseline feature ranking instead of fitting LightGBM.")
    parser.add_argument("--no-publish-latest", action="store_true", help="Write timestamped model outputs without updating canonical latest files.")
    args = parser.parse_args()
    build_model_outputs(
        gold_file=args.gold_file,
        limit_tickers=args.limit_tickers,
        top_n=args.top_n,
        model_shards=args.model_shards,
        live_signal_mode=args.live_signal_mode,
        baseline_only=args.baseline_only,
        publish_latest=not args.no_publish_latest and args.limit_tickers is None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
