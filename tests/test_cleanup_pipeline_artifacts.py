from pathlib import Path

from scripts.cleanup_pipeline_artifacts import PROTECTED_NAMES, RetentionPattern, stale_files


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_cleanup_selects_old_generated_artifacts_and_preserves_latest(tmp_path):
    for idx in range(5):
        path = tmp_path / "data" / "gold" / f"06_us_gold_ml_dataset_2026052{idx}_000000.csv"
        _write(path, str(idx))

    selected = stale_files([RetentionPattern("data/gold", "06_us_gold_ml_dataset_*.csv", keep=2)], root=tmp_path)

    assert len(selected) == 3
    assert all("20260523" not in path.name and "20260524" not in path.name for path in selected)


def test_cleanup_never_selects_protected_canonical_files(tmp_path):
    for name in PROTECTED_NAMES:
        _write(tmp_path / "data" / "model_outputs" / name, "keep")
    _write(tmp_path / "data" / "model_outputs" / "advanced_model_latest_predictions_20260501_000000.csv", "old")
    _write(tmp_path / "data" / "model_outputs" / "advanced_model_latest_predictions_20260502_000000.csv", "new")

    selected = stale_files([RetentionPattern("data/model_outputs", "*.csv", keep=1)], root=tmp_path)

    assert [path.name for path in selected] == ["advanced_model_latest_predictions_20260501_000000.csv"]


def test_cleanup_preserves_configured_number_of_gold_files(tmp_path):
    for idx in range(4):
        _write(tmp_path / "data" / "gold" / f"06_us_gold_ml_dataset_2026052{idx}_000000.csv", str(idx))

    selected = stale_files([RetentionPattern("data/gold", "06_us_gold_ml_dataset_*.csv", keep=1)], root=tmp_path)

    assert len(selected) == 3
    assert "20260523" not in {path.name for path in selected}


def test_cleanup_retains_critical_interim_families_independently(tmp_path):
    for idx in range(8):
        stamp = f"2026052{idx}_000000"
        _write(tmp_path / "data" / "interim" / f"02_us_tradable_universe_{stamp}.csv", str(idx))
        _write(tmp_path / "data" / "interim" / f"03_us_price_validated_universe_{stamp}.csv", str(idx))
        _write(tmp_path / "data" / "interim" / f"04_us_metadata_enriched_{stamp}.csv", str(idx))
        _write(tmp_path / "data" / "interim" / f"00_candidate_funnel_summary_{stamp}.csv", str(idx))

    patterns = [
        RetentionPattern("data/interim", "02_us_tradable_universe_*.csv", 5),
        RetentionPattern("data/interim", "03_us_price_validated_universe_*.csv", 5),
        RetentionPattern("data/interim", "04_us_metadata_enriched_*.csv", 5),
        RetentionPattern("data/interim", "00_candidate_funnel_summary_*.csv", 10),
    ]
    selected = stale_files(patterns, root=tmp_path)
    names = {path.name for path in selected}

    assert sum(name.startswith("02_us_tradable_universe_") for name in names) == 3
    assert sum(name.startswith("03_us_price_validated_universe_") for name in names) == 3
    assert sum(name.startswith("04_us_metadata_enriched_") for name in names) == 3
    assert not any(name.startswith("00_candidate_funnel_summary_") for name in names)
