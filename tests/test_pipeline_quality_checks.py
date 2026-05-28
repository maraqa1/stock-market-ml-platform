from pathlib import Path

import json
import os
import pandas as pd

from stockml.reports.pipeline_quality_checks import PipelineQualityThresholds, build_pipeline_quality_report


def _write(path: Path, rows: list[dict[str, object]], mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    os.utime(path, (mtime, mtime))


def _thresholds() -> PipelineQualityThresholds:
    return PipelineQualityThresholds(
        min_universe_rows=3,
        min_validated_rows=2,
        min_validated_universe_coverage=0.50,
        min_metadata_validated_coverage=0.75,
        min_metadata_market_cap_coverage=0.75,
        min_gold_rows=4,
        min_gold_validated_coverage=0.75,
        max_gold_missing_market_cap_rate=0.25,
        max_gold_duplicate_key_rate=0.0,
    )


def _healthy_artifacts(root: Path) -> None:
    _write(
        root / "data" / "interim" / "02_us_tradable_universe_20260523_000000.csv",
        [{"symbol": "AAA"}, {"symbol": "BBB"}, {"symbol": "CCC"}],
        1,
    )
    _write(
        root / "data" / "interim" / "03_us_price_validated_universe_20260523_000000.csv",
        [{"yahoo_ticker": "AAA"}, {"yahoo_ticker": "BBB"}],
        2,
    )
    _write(
        root / "data" / "interim" / "04_us_metadata_enriched_20260523_000000.csv",
        [{"ticker": "AAA", "market_cap": 1_000_000_000}, {"ticker": "BBB", "market_cap": 2_000_000_000}],
        3,
    )
    _write(
        root / "data" / "processed" / "05_us_feature_panel_20260523_000000.csv",
        [{"ticker": "AAA", "date": "2026-05-20"}, {"ticker": "BBB", "date": "2026-05-20"}],
        4,
    )
    _write(
        root / "data" / "gold" / "06_us_gold_ml_dataset_20260523_000000.csv",
        [
            {"ticker": "AAA", "date": "2026-05-20", "market_cap": 1_000_000_000},
            {"ticker": "AAA", "date": "2026-05-21", "market_cap": 1_000_000_000},
            {"ticker": "BBB", "date": "2026-05-20", "market_cap": 2_000_000_000},
            {"ticker": "BBB", "date": "2026-05-21", "market_cap": 2_000_000_000},
        ],
        5,
    )
    _write(root / "data" / "model_outputs" / "model_predictions_latest.csv", [{"ticker": "AAA"}], 6)


def _write_manifest(root: Path, run_id: str, profile: str, outputs: dict[str, dict[str, str]], mtime: int) -> None:
    manifest = {
        "run_id": run_id,
        "profile": profile,
        "status": "ok",
        "stages": {stage: {"status": "ok", "outputs": stage_outputs} for stage, stage_outputs in outputs.items()},
    }
    path = root / "data" / "pipeline_runs" / run_id / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_pipeline_quality_report_passes_for_coherent_artifacts(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("stockml.reports.pipeline_quality_checks.ensure_data_dirs", lambda: None)
    _healthy_artifacts(tmp_path)

    result = build_pipeline_quality_report(tmp_path, thresholds=_thresholds(), stamp="20260523_000000")
    report = pd.read_csv(result["path"])

    assert result["status"] == "ok"
    assert result["failed_checks"] == 0
    assert set(report["status"]) == {"pass"}


def test_pipeline_quality_report_prefers_successful_us_full_manifest(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("stockml.reports.pipeline_quality_checks.ensure_data_dirs", lambda: None)
    _healthy_artifacts(tmp_path)
    model_timestamped = tmp_path / "data" / "model_outputs" / "advanced_model_latest_predictions_20260523_000000.csv"
    _write(model_timestamped, [{"ticker": "AAA"}], 6)
    _write(
        tmp_path / "data" / "gold" / "06_us_gold_ml_dataset_20260523_010000.csv",
        [{"ticker": "AAA", "date": "2026-05-20", "market_cap": pd.NA}],
        10,
    )
    _write_manifest(
        tmp_path,
        "20260523_000000",
        "us_full",
        {
            "universe": {"tradable_universe": str(tmp_path / "data" / "interim" / "02_us_tradable_universe_20260523_000000.csv")},
            "price": {"validated_universe": str(tmp_path / "data" / "interim" / "03_us_price_validated_universe_20260523_000000.csv")},
            "metadata": {"metadata_enriched": str(tmp_path / "data" / "interim" / "04_us_metadata_enriched_20260523_000000.csv")},
            "features": {"feature_panel": str(tmp_path / "data" / "processed" / "05_us_feature_panel_20260523_000000.csv")},
            "gold": {"gold_dataset": str(tmp_path / "data" / "gold" / "06_us_gold_ml_dataset_20260523_000000.csv")},
            "model": {"predictions": str(model_timestamped)},
        },
        7,
    )
    _write_manifest(
        tmp_path,
        "20260523_010000",
        "nasdaq_500",
        {
            "gold": {"gold_dataset": str(tmp_path / "data" / "gold" / "06_us_gold_ml_dataset_20260523_010000.csv")},
        },
        11,
    )

    result = build_pipeline_quality_report(tmp_path, thresholds=_thresholds(), stamp="20260523_030000")

    assert result["status"] == "ok"
    assert result["failed_checks"] == 0


def test_pipeline_quality_report_fails_stale_gold_and_missing_market_caps(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("stockml.reports.pipeline_quality_checks.ensure_data_dirs", lambda: None)
    _healthy_artifacts(tmp_path)
    (tmp_path / "data" / "gold" / "06_us_gold_ml_dataset_20260523_000000.csv").unlink()
    _write(
        tmp_path / "data" / "gold" / "06_us_gold_ml_dataset_20260523_010000.csv",
        [
            {"ticker": "AAA", "date": "2026-05-20", "market_cap": pd.NA},
            {"ticker": "AAA", "date": "2026-05-20", "market_cap": pd.NA},
            {"ticker": "BBB", "date": "2026-05-20", "market_cap": 2_000_000_000},
            {"ticker": "BBB", "date": "2026-05-21", "market_cap": 2_000_000_000},
        ],
        2,
    )

    result = build_pipeline_quality_report(tmp_path, thresholds=_thresholds(), stamp="20260523_010000")
    failures = {row["check"] for row in result["failures"]}

    assert result["status"] == "failed"
    assert "artifact_gold_exists_and_fresh" in failures
    assert "gold_missing_market_cap_rate" in failures
    assert "gold_duplicate_ticker_date_rate" in failures


def test_pipeline_quality_report_fails_when_validated_universe_collapses(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("stockml.reports.pipeline_quality_checks.ensure_data_dirs", lambda: None)
    _healthy_artifacts(tmp_path)
    _write(
        tmp_path / "data" / "interim" / "03_us_price_validated_universe_20260523_010000.csv",
        [{"yahoo_ticker": "AAA"}],
        7,
    )

    result = build_pipeline_quality_report(tmp_path, thresholds=_thresholds(), stamp="20260523_020000")
    failures = {row["check"] for row in result["failures"]}

    assert result["status"] == "failed"
    assert "validated_row_count" in failures
    assert "validated_universe_coverage" in failures
