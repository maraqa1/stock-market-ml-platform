from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.diagnostics.approved_market_cap_coverage import (
    build_approved_market_cap_coverage_report,
    write_approved_market_cap_coverage_report,
)


def _write(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_provider_uncovered_market_cap_is_reported(tmp_path: Path):
    candidate = _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260730_000000.csv",
        [{"symbol": "AAA", "source_trade_action": "Long", "trade_action": "Long", "candidate_rank": 1, "market_cap": None}],
    )
    metadata = _write(
        tmp_path / "data" / "interim" / "04_us_metadata_enriched_20260730_000000.csv",
        [{"ticker": "AAA", "market_cap": None, "metadata_status": "ok", "metadata_error": ""}],
    )
    validated = _write(
        tmp_path / "data" / "interim" / "03_us_price_validated_universe_20260730_000000.csv",
        [{"ticker": "AAA"}],
    )
    gold = _write(
        tmp_path / "data" / "gold" / "06_us_gold_ml_dataset_20260730_000000.csv",
        [{"ticker": "AAA", "date": "2026-07-29", "market_cap": None}],
    )

    report = build_approved_market_cap_coverage_report(
        root=tmp_path,
        candidate_file=candidate,
        metadata_file=metadata,
        validated_file=validated,
        gold_file=gold,
    )

    assert len(report) == 1
    row = report.iloc[0]
    assert row["symbol"] == "AAA"
    assert row["missing_market_cap_root_cause"] == "provider_uncovered_market_cap"
    assert row["diagnostic_decision"] == "provider_coverage_gap"


def test_candidate_join_failure_when_metadata_has_market_cap(tmp_path: Path):
    candidate = _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260730_000000.csv",
        [{"symbol": "BBB", "source_trade_action": "Long", "trade_action": "Long", "candidate_rank": 1, "market_cap": None}],
    )
    metadata = _write(
        tmp_path / "data" / "interim" / "04_us_metadata_enriched_20260730_000000.csv",
        [{"ticker": "BBB", "market_cap": 2_000_000_000, "metadata_status": "ok", "metadata_error": ""}],
    )
    validated = _write(
        tmp_path / "data" / "interim" / "03_us_price_validated_universe_20260730_000000.csv",
        [{"ticker": "BBB"}],
    )
    gold = _write(
        tmp_path / "data" / "gold" / "06_us_gold_ml_dataset_20260730_000000.csv",
        [{"ticker": "BBB", "date": "2026-07-29", "market_cap": None}],
    )

    report = build_approved_market_cap_coverage_report(
        root=tmp_path,
        candidate_file=candidate,
        metadata_file=metadata,
        validated_file=validated,
        gold_file=gold,
    )

    assert report.iloc[0]["missing_market_cap_root_cause"] == "candidate_metadata_join_failure"
    assert report.iloc[0]["diagnostic_decision"] == "pipeline_join_or_fetch_bug"


def test_metadata_fetch_gap_when_validated_but_absent_from_metadata(tmp_path: Path):
    candidate = _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260730_000000.csv",
        [{"symbol": "CCC", "source_trade_action": "Long", "trade_action": "Long", "candidate_rank": 1, "market_cap": None}],
    )
    metadata = _write(
        tmp_path / "data" / "interim" / "04_us_metadata_enriched_20260730_000000.csv",
        [{"ticker": "OTHER", "market_cap": 1_000_000_000}],
    )
    validated = _write(
        tmp_path / "data" / "interim" / "03_us_price_validated_universe_20260730_000000.csv",
        [{"ticker": "CCC"}],
    )
    gold = _write(
        tmp_path / "data" / "gold" / "06_us_gold_ml_dataset_20260730_000000.csv",
        [{"ticker": "CCC", "date": "2026-07-29", "market_cap": None}],
    )

    report = build_approved_market_cap_coverage_report(
        root=tmp_path,
        candidate_file=candidate,
        metadata_file=metadata,
        validated_file=validated,
        gold_file=gold,
    )

    assert report.iloc[0]["missing_market_cap_root_cause"] == "metadata_fetch_or_join_gap"


def test_no_decision_rows_are_not_reported(tmp_path: Path):
    candidate = _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260730_000000.csv",
        [
            {"symbol": "AAA", "source_trade_action": "No Decision", "trade_action": "Long", "candidate_rank": 1, "market_cap": None},
            {"symbol": "BBB", "source_trade_action": "Long", "trade_action": "Long", "candidate_rank": 2, "market_cap": 1_000_000_000},
        ],
    )

    report = build_approved_market_cap_coverage_report(root=tmp_path, candidate_file=candidate)

    assert report.empty


def test_write_report_outputs_csv_and_markdown(tmp_path: Path):
    candidate = _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260730_000000.csv",
        [{"symbol": "AAA", "source_trade_action": "Long", "trade_action": "Long", "candidate_rank": 1, "market_cap": None}],
    )
    metadata = _write(
        tmp_path / "data" / "interim" / "04_us_metadata_enriched_20260730_000000.csv",
        [{"ticker": "AAA", "market_cap": None}],
    )

    result = write_approved_market_cap_coverage_report(
        root=tmp_path,
        candidate_file=candidate,
        metadata_file=metadata,
        output_dir=tmp_path / "out",
        stamp="20260730_120000",
    )

    assert result["rows"] == 1
    assert Path(result["path"]).exists()
    assert Path(result["summary_path"]).exists()
