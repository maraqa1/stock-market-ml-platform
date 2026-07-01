from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.diagnostics.calibration_coverage_debug import (
    build_calibration_coverage_debug,
    candidate_mapping_debug,
    locate_validation_inputs,
    write_calibration_coverage_debug,
)
from stockml.diagnostics.expected_return_calibration import build_expected_return_calibration
from stockml.diagnostics.validation_bucket_calibration import (
    CALIBRATION_COLUMNS,
    build_validation_bucket_calibration,
)


def _predictions(count: int = 1000, *, side: str = "Long", model_version: str = "m1") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": [f"T{i:04d}" for i in range(count)],
            "side": [side] * count,
            "model_version": [model_version] * count,
            "model_score": [1.0 - i / max(count, 1) for i in range(count)],
            "rank_overall": list(range(1, count + 1)),
            "forward_5d_return": [0.02] * count,
            "split": ["validation"] * count,
        }
    )


def _candidate(symbol: str = "LIVE", *, side: str = "Long", model_version: str = "m1", rank: int = 1) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": symbol,
                "side": side,
                "model_version": model_version,
                "model_score": 0.99,
                "candidate_rank": rank,
                "expected_trade_return": 1961.05,
            }
        ]
    )


def test_missing_calibration_file_is_detected(tmp_path: Path):
    inputs, _ = locate_validation_inputs(tmp_path)
    row = inputs[inputs["name"].eq("expected_return_bucket_calibration_latest")].iloc[0]

    assert bool(row["exists"]) is False


def test_missing_forward_returns_are_detected():
    validation = _predictions(10).drop(columns=["forward_5d_return"])

    _, summary = build_calibration_coverage_debug(validation_frame=validation, candidates=_candidate(), calibration=pd.DataFrame())

    assert summary["root_cause"] == "missing_forward_return_labels"
    assert summary["forward_label_coverage"]["forward_5d_return"] == 0.0


def test_model_version_mismatch_is_detected():
    calibration, _ = build_validation_bucket_calibration(_predictions(model_version="m1"))

    mapping = candidate_mapping_debug(_candidate(model_version="m2"), calibration)

    assert mapping.iloc[0]["match_failure_reason"] == "model_version_not_found"


def test_side_specific_bucket_missing_is_detected():
    calibration, _ = build_validation_bucket_calibration(_predictions(side="Long"))

    mapping = candidate_mapping_debug(_candidate(side="Short"), calibration)

    assert mapping.iloc[0]["match_failure_reason"] == "side_not_found"


def test_insufficient_sample_count_is_detected():
    validation = _predictions(20)

    debug, summary = build_calibration_coverage_debug(validation_frame=validation, candidates=_candidate(), calibration=None)

    assert summary["root_cause"] == "insufficient_bucket_sample_count"
    assert debug["calibration_quality"].fillna("").str.contains("insufficient_data").any()


def test_candidate_rank_maps_to_correct_bucket_when_data_exists():
    calibration, _ = build_validation_bucket_calibration(_predictions())

    mapping = candidate_mapping_debug(_candidate(rank=1), calibration)

    assert mapping.iloc[0]["match_failure_reason"] == ""
    assert mapping.iloc[0]["matched_calibration_bucket"]


def test_uncalibrated_candidate_remains_rejected_with_empty_calibration():
    report = build_expected_return_calibration(_candidate(), pd.DataFrame(columns=CALIBRATION_COLUMNS))

    assert report.iloc[0]["execution_allowed"] is False or report.iloc[0]["execution_allowed"] == False
    assert report.iloc[0]["execution_block_reason"] == "expected_return_uncalibrated"


def test_raw_expected_trade_return_is_not_used_as_fallback():
    report = build_expected_return_calibration(_candidate(), pd.DataFrame(columns=CALIBRATION_COLUMNS))

    assert report.iloc[0]["expected_return_source"] == "unknown"
    assert report.iloc[0]["validated_expected_return_bps"] is pd.NA or pd.isna(report.iloc[0]["validated_expected_return_bps"])


def test_writer_outputs_debug_and_summary(tmp_path: Path):
    outputs = write_calibration_coverage_debug(root=tmp_path, output_dir=tmp_path, stamp="20260101_000000")

    assert outputs.diagnostic_path.exists()
    assert outputs.summary_path.exists()
    assert outputs.root_cause == "missing_forward_return_labels"
