from pathlib import Path

import pandas as pd

from stockml.reports.symbol_coverage_audit import build_symbol_coverage_audit


def test_symbol_coverage_audit_traces_provider_scoped_drop_stage(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("stockml.reports.symbol_coverage_audit.ensure_data_dirs", lambda: None)

    interim = tmp_path / "data" / "interim"
    raw = tmp_path / "data" / "raw"
    gold = tmp_path / "data" / "gold"
    model = tmp_path / "data" / "model_outputs"
    portal = tmp_path / "data" / "portal_outputs"
    for path in [interim, raw, gold, model, portal]:
        path.mkdir(parents=True)

    pd.DataFrame(
        [
            {"symbol": "MRVL", "listing_exchange": "NASDAQ", "company": "Marvell"},
            {"symbol": "KR", "listing_exchange": "NYSE", "company": "Kroger"},
        ]
    ).to_csv(interim / "02_us_tradable_universe_20260519_000000.csv", index=False)

    pd.DataFrame(
        [
            {"ticker": "MRVL", "date": "2026-05-18", "source": "yahoo_legacy"},
            {"ticker": "KR", "date": "2026-05-18", "source": "eodhd"},
        ]
    ).to_csv(raw / "03_us_price_history_store.csv", index=False)

    pd.DataFrame([{"yahoo_ticker": "KR"}]).to_csv(
        interim / "03_us_price_validated_universe_20260519_000000.csv", index=False
    )
    pd.DataFrame([{"ticker": "KR", "metadata_status": "ok", "metadata_error": ""}]).to_csv(
        interim / "04_us_metadata_enriched_20260519_000000.csv", index=False
    )
    pd.DataFrame([{"ticker": "KR", "date": "2026-05-18"}]).to_csv(
        gold / "06_us_gold_ml_dataset_20260519_000000.csv", index=False
    )
    pd.DataFrame(
        [
            {
                "ticker": "KR",
                "trade_action": "No Decision",
                "signal": "HOLD",
                "model_score": -0.5,
                "rank_overall": 100,
                "meta_label_probability": 0.4,
                "meta_label_decision": "Skip Trade",
            }
        ]
    ).to_csv(model / "model_predictions_latest.csv", index=False)
    pd.DataFrame([{"symbol": "KR", "candidate_rank": 10, "candidate_status": "watch"}]).to_csv(
        portal / "08_alpaca_paper_candidate_pool_20260519_000000.csv", index=False
    )

    result = build_symbol_coverage_audit(
        tmp_path,
        symbols=["MRVL", "KR", "ARM"],
        provider_name="eodhd",
        stamp="20260519_000000",
    )
    report = pd.read_csv(result["path"])

    mrvl = report.set_index("symbol").loc["MRVL"]
    kr = report.set_index("symbol").loc["KR"]
    arm = report.set_index("symbol").loc["ARM"]

    assert mrvl["in_universe"]
    assert not mrvl["has_price"]
    assert mrvl["drop_stage"] == "price"
    assert mrvl["drop_reason"] == "missing_provider_price_history"
    assert kr["has_model_prediction"]
    assert kr["drop_stage"] == "order_plan"
    assert arm["drop_stage"] == "universe"


def test_symbol_coverage_audit_does_not_treat_missing_metadata_as_blocking_stage(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("stockml.reports.symbol_coverage_audit.ensure_data_dirs", lambda: None)

    interim = tmp_path / "data" / "interim"
    raw = tmp_path / "data" / "raw"
    gold = tmp_path / "data" / "gold"
    model = tmp_path / "data" / "model_outputs"
    portal = tmp_path / "data" / "portal_outputs"
    for path in [interim, raw, gold, model, portal]:
        path.mkdir(parents=True)

    pd.DataFrame([{"symbol": "BB", "listing_exchange": "NYSE", "company": "BlackBerry"}]).to_csv(
        interim / "02_us_tradable_universe_20260522_000000.csv", index=False
    )
    pd.DataFrame([{"ticker": "BB", "date": "2026-05-21", "source": "eodhd"}]).to_csv(
        raw / "03_us_price_history_store.csv", index=False
    )
    pd.DataFrame([{"yahoo_ticker": "BB"}]).to_csv(
        interim / "03_us_price_validated_universe_20260522_000000.csv", index=False
    )
    pd.DataFrame([{"ticker": "OTHER", "metadata_status": "ok"}]).to_csv(
        interim / "04_us_metadata_enriched_20260522_000000.csv", index=False
    )
    pd.DataFrame([{"ticker": "BB", "date": "2026-05-21"}]).to_csv(
        gold / "06_us_gold_ml_dataset_20260522_000000.csv", index=False
    )

    result = build_symbol_coverage_audit(tmp_path, symbols=["BB"], provider_name="eodhd", stamp="20260522_000000")
    report = pd.read_csv(result["path"])
    row = report.set_index("symbol").loc["BB"]

    assert not row["has_metadata"]
    assert row["has_gold_rows"]
    assert row["drop_stage"] == "model"
    assert row["drop_reason"] == "missing_model_prediction"


def test_symbol_coverage_audit_reports_gold_missing_after_validated_without_metadata(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("stockml.reports.symbol_coverage_audit.ensure_data_dirs", lambda: None)

    interim = tmp_path / "data" / "interim"
    raw = tmp_path / "data" / "raw"
    gold = tmp_path / "data" / "gold"
    model = tmp_path / "data" / "model_outputs"
    portal = tmp_path / "data" / "portal_outputs"
    for path in [interim, raw, gold, model, portal]:
        path.mkdir(parents=True)

    pd.DataFrame([{"symbol": "HPQ", "listing_exchange": "NYSE", "company": "HP"}]).to_csv(
        interim / "02_us_tradable_universe_20260522_000000.csv", index=False
    )
    pd.DataFrame([{"ticker": "HPQ", "date": "2026-05-22", "source": "eodhd"}]).to_csv(
        raw / "03_us_price_history_store.csv", index=False
    )
    pd.DataFrame([{"yahoo_ticker": "HPQ"}]).to_csv(
        interim / "03_us_price_validated_universe_20260522_000000.csv", index=False
    )
    pd.DataFrame([{"ticker": "OTHER", "metadata_status": "ok"}]).to_csv(
        interim / "04_us_metadata_enriched_20260522_000000.csv", index=False
    )

    result = build_symbol_coverage_audit(tmp_path, symbols=["HPQ"], provider_name="eodhd", stamp="20260522_000000")
    report = pd.read_csv(result["path"])
    row = report.set_index("symbol").loc["HPQ"]

    assert not row["has_metadata"]
    assert not row["has_gold_rows"]
    assert row["drop_stage"] == "gold"
    assert row["drop_reason"] == "missing_gold_rows"
