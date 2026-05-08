from __future__ import annotations

from pathlib import Path
from typing import Optional

from portal.services.database_reader import model_artifacts
from portal.services.latest_file_reader import file_status, latest_file, safe_read_csv


def model_validation_context(root: Optional[Path] = None) -> dict:
    leaderboard = latest_file(root, "model_outputs", "advanced_model_validation_leaderboard_*.csv", fallback_keys=["portal_outputs"])
    buckets = latest_file(root, "model_outputs", "advanced_model_confidence_bucket_performance_*.csv", fallback_keys=["portal_outputs"])
    importance = latest_file(root, "model_outputs", "advanced_model_feature_importance_*.csv", fallback_keys=["portal_outputs"])
    status = latest_file(root, "model_outputs", "advanced_model_model_status_*.csv", fallback_keys=["portal_outputs"])
    status_db = model_artifacts("model_status", limit=5)
    leaderboard_db = model_artifacts("validation_leaderboard", limit=100)
    buckets_db = model_artifacts("confidence_bucket_performance", limit=100)
    importance_db = model_artifacts("feature_importance", limit=100)
    status_df = status_db if not status_db.empty else safe_read_csv(status, nrows=5)
    row = status_df.iloc[0].to_dict() if not status_df.empty else {}
    return {
        "status": row or {"decision_grade": "diagnostic_only", "reason": "No model status file found"},
        "leaderboard": (leaderboard_db if not leaderboard_db.empty else safe_read_csv(leaderboard, nrows=100)).to_dict("records"),
        "buckets": (buckets_db if not buckets_db.empty else safe_read_csv(buckets, nrows=100)).to_dict("records"),
        "importance": (importance_db if not importance_db.empty else safe_read_csv(importance, nrows=100)).to_dict("records"),
        "files": [file_status(leaderboard, "Validation leaderboard"), file_status(buckets, "Confidence buckets"), file_status(importance, "Feature importance"), file_status(status, "Model status")],
        "data_source": "PostgreSQL" if not status_db.empty else "CSV",
    }
