from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Optional

from stockml.common.logging_utils import log
from stockml.common.paths import MODEL_OUTPUTS_DIR, ensure_data_dirs, timestamp
from stockml.models.gold_loader import load_gold_dataset
from stockml.models.ranking_model import ModelArtifacts, train_predict_from_gold


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
    return outputs


def build_model_outputs(gold_file: Optional[Path] = None, limit_tickers: Optional[int] = None, top_n: int = 50) -> Dict[str, Path]:
    ensure_data_dirs()
    stamp = timestamp()
    gold = load_gold_dataset(gold_file, limit_tickers=limit_tickers)
    log(f"Loaded Gold dataset for model: {len(gold):,} rows")
    artifacts = train_predict_from_gold(gold, top_n=top_n)
    paths = _write_artifacts(artifacts, stamp)
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

