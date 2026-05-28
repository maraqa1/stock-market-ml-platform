import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stockml.pipeline.doctor import audit_latest_pipeline
from stockml.reports.pipeline_quality_checks import PipelineQualityThresholds, build_pipeline_quality_report


def _write_manifest(root: Path, run_id: str, payload: dict) -> Path:
    path = root / "data" / "pipeline_runs" / run_id / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ok_manifest(root: Path) -> dict:
    stages = {}
    artifact_dir = root / "data" / "artifact"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "universe": ("tradable_universe", "symbol\nAAA\n"),
        "price": ("validated_universe", "yahoo_ticker\nAAA\n"),
        "metadata": ("metadata_enriched", "ticker,market_cap\nAAA,1000000000\n"),
        "features": ("feature_panel", "ticker\nAAA\n"),
        "gold": ("gold_dataset", "date,ticker,market_cap\n2026-05-28,AAA,1000000000\n"),
        "model": ("predictions", "ticker\nAAA\n"),
        "trading_day_readiness": ("plan_path", "symbol\nAAA\n"),
    }
    for stage, (key, content) in files.items():
        artifact = artifact_dir / f"{stage}.csv"
        artifact.write_text(content, encoding="utf-8")
        stages[stage] = {"status": "ok", "outputs": {key: str(artifact)}, "finished_at": "2026-05-28T09:00:00"}
    return {
        "run_id": "run-ok",
        "profile": "us_full",
        "status": "ok",
        "started_at": "2026-05-28T08:00:00",
        "finished_at": "2026-05-28T09:00:00",
        "stages": stages,
    }


def test_pipeline_doctor_accepts_complete_manifest(tmp_path: Path):
    _write_manifest(tmp_path, "run-ok", _ok_manifest(tmp_path))

    result = audit_latest_pipeline(tmp_path)

    assert result["status"] == "ok"
    assert result["missing_stages"] == []
    assert result["missing_outputs"] == []


def test_pipeline_doctor_rejects_stale_running_manifest(tmp_path: Path):
    stale_start = (datetime.now(timezone.utc) - timedelta(minutes=120)).isoformat()
    manifest = {
        "run_id": "run-stale",
        "profile": "us_full",
        "status": "running",
        "started_at": stale_start,
        "finished_at": "",
        "stages": {"universe": {"status": "ok", "outputs": {}, "finished_at": stale_start}},
    }
    _write_manifest(tmp_path, "run-stale", manifest)

    result = audit_latest_pipeline(tmp_path, stale_after_minutes=30)

    assert result["status"] == "failed"
    assert result["reason"] == "pipeline_stale_running"


def test_pipeline_doctor_rejects_missing_required_stage(tmp_path: Path):
    manifest = _ok_manifest(tmp_path)
    del manifest["stages"]["model"]
    _write_manifest(tmp_path, "run-missing", manifest)

    result = audit_latest_pipeline(tmp_path)

    assert result["status"] == "failed"
    assert result["reason"] == "required_stage_missing"
    assert "model" in result["missing_stages"]


def test_quality_report_can_validate_running_manifest_artifacts(tmp_path: Path):
    manifest = _ok_manifest(tmp_path)
    manifest["status"] = "running"
    thresholds = PipelineQualityThresholds(
        min_universe_rows=1,
        min_validated_rows=1,
        min_validated_universe_coverage=0.0,
        min_metadata_validated_coverage=0.0,
        min_metadata_market_cap_coverage=0.0,
        min_gold_rows=1,
        min_gold_validated_coverage=0.0,
        max_gold_missing_market_cap_rate=1.0,
    )

    result = build_pipeline_quality_report(tmp_path, thresholds=thresholds, profile_name=None, manifest=manifest)

    assert result["failed_checks"] == 0
