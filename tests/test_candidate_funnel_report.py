from pathlib import Path

import pandas as pd

from stockml.reports.candidate_funnel_report import build_candidate_funnel_report


def test_candidate_funnel_report_writes_summary_and_artifact_manifest(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("stockml.reports.symbol_coverage_audit.ensure_data_dirs", lambda: None)
    monkeypatch.setattr("stockml.reports.candidate_funnel_report.ensure_data_dirs", lambda: None)

    interim = tmp_path / "data" / "interim"
    raw = tmp_path / "data" / "raw"
    gold = tmp_path / "data" / "gold"
    model = tmp_path / "data" / "model_outputs"
    portal = tmp_path / "data" / "portal_outputs"
    processed = tmp_path / "data" / "processed"
    for path in [interim, raw, processed, gold, model, portal]:
        path.mkdir(parents=True)

    pd.DataFrame(
        [
            {"symbol": "AAA", "listing_exchange": "NYSE"},
            {"symbol": "BBB", "listing_exchange": "NYSE"},
        ]
    ).to_csv(interim / "02_us_tradable_universe_20260522_000000.csv", index=False)
    pd.DataFrame([{"ticker": "AAA", "date": "2026-05-22", "source": "eodhd"}]).to_csv(
        raw / "03_us_price_history_store.csv", index=False
    )
    pd.DataFrame([{"yahoo_ticker": "AAA"}]).to_csv(
        interim / "03_us_price_validated_universe_20260522_000000.csv", index=False
    )

    result = build_candidate_funnel_report(tmp_path, provider_name="eodhd", stamp="20260522_000000")

    summary = pd.read_csv(result["summary_path"])
    assert result["symbols"] == 2
    assert {"price", "gold"}.issubset(set(summary["stage"]))
    assert Path(result["artifact_path"]).exists()


def test_candidate_funnel_artifacts_flag_stale_downstream_files(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("stockml.reports.symbol_coverage_audit.ensure_data_dirs", lambda: None)
    monkeypatch.setattr("stockml.reports.candidate_funnel_report.ensure_data_dirs", lambda: None)

    interim = tmp_path / "data" / "interim"
    raw = tmp_path / "data" / "raw"
    processed = tmp_path / "data" / "processed"
    gold = tmp_path / "data" / "gold"
    model = tmp_path / "data" / "model_outputs"
    portal = tmp_path / "data" / "portal_outputs"
    for path in [interim, raw, processed, gold, model, portal]:
        path.mkdir(parents=True)

    files = [
        interim / "02_us_tradable_universe_20260522_000000.csv",
        raw / "03_us_price_history_store.csv",
        interim / "03_us_price_validated_universe_20260522_000000.csv",
        interim / "04_us_metadata_enriched_20260522_000000.csv",
        processed / "05_us_feature_panel_20260522_000000.csv",
        gold / "06_us_gold_ml_dataset_20260522_000000.csv",
        model / "model_predictions_latest.csv",
        portal / "08_alpaca_paper_candidate_pool_20260522_000000.csv",
        portal / "08_alpaca_paper_order_plan_20260522_000000.csv",
    ]
    for index, path in enumerate(files, start=1):
        pd.DataFrame([{"symbol": "AAA", "ticker": "AAA", "yahoo_ticker": "AAA", "date": "2026-05-22", "source": "eodhd"}]).to_csv(path, index=False)
        path.touch()
        if path.name.startswith("06_us_gold"):
            # Simulate stale Gold after a repaired validated universe.
            path.touch(times=(1, 1))
        else:
            path.touch(times=(index + 10, index + 10))

    result = build_candidate_funnel_report(tmp_path, provider_name="eodhd", stamp="20260522_000000")
    artifacts = pd.read_csv(result["artifact_path"])
    stale = artifacts.set_index("artifact")["stale_vs_upstream"].astype(str).str.lower().to_dict()

    assert stale["gold"] == "true"
