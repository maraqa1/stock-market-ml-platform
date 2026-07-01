from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.diagnostics.validation_bucket_calibration import (
    build_validation_bucket_calibration,
    map_candidates_to_calibration,
    prepare_validation_rows,
    write_validation_bucket_calibration,
)


def _prediction_rows(count: int = 120, *, side: str = "Long", forward: float = 0.02) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": [f"T{i:03d}" for i in range(count)],
            "side": [side] * count,
            "model_score": [1.0 - (i / max(count, 1)) for i in range(count)],
            "rank_overall": list(range(1, count + 1)),
            "forward_5d_return": [forward] * count,
            "split": ["validation"] * count,
            "sector": ["Technology"] * count,
        }
    )


def test_calibration_excludes_rows_without_future_returns():
    frame = _prediction_rows(35)
    frame.loc[0, "forward_5d_return"] = pd.NA

    prepared = prepare_validation_rows(frame)

    assert len(prepared) == 34
    assert prepared["forward_return_bps"].notna().all()


def test_no_usable_forward_returns_writes_empty_calibration():
    frame = _prediction_rows(35)
    frame["forward_5d_return"] = pd.NA

    calibration, validation_rows = build_validation_bucket_calibration(frame)

    assert validation_rows == 0
    assert list(calibration.columns)
    assert calibration.empty


def test_latest_unlabelled_rows_are_not_used_for_calibration():
    frame = _prediction_rows(35)
    frame.loc[34, "split"] = "latest"
    frame.loc[34, "forward_5d_return"] = pd.NA

    calibration, validation_rows = build_validation_bucket_calibration(frame)

    assert validation_rows == 34
    assert not calibration.empty


def test_long_net_forward_return_uses_cost_adjusted_bps():
    frame = _prediction_rows(30, side="Long", forward=0.02)

    prepared = prepare_validation_rows(frame, estimated_spread_cost_bps=3, estimated_slippage_bps=5)

    assert prepared["forward_return_bps"].iloc[0] == 192.0


def test_short_net_forward_return_inverts_future_price_move():
    frame = _prediction_rows(30, side="Short", forward=0.01)

    prepared = prepare_validation_rows(frame, estimated_spread_cost_bps=3, estimated_slippage_bps=5, borrow_cost_estimate_bps=2)

    assert prepared["forward_return_bps"].iloc[0] == -110.0


def test_bucket_sample_count_and_insufficient_quality_are_recorded():
    calibration, validation_rows = build_validation_bucket_calibration(_prediction_rows(20))

    assert validation_rows == 20
    assert set(calibration["calibration_quality"]) == {"insufficient_data"}


def test_usable_side_specific_bucket_maps_to_candidate_expected_return():
    calibration, _ = build_validation_bucket_calibration(_prediction_rows(1000, side="Long", forward=0.02))
    candidate = pd.DataFrame(
        [{"ticker": "LIVE", "side": "Long", "candidate_rank": 1, "model_score": 999.0, "expected_trade_return": 1961.05}]
    )

    mapped = map_candidates_to_calibration(candidate, calibration)

    assert mapped.iloc[0]["expected_return_quality"] == "usable"
    assert mapped.iloc[0]["execution_block_reason"] == ""
    assert mapped.iloc[0]["validated_expected_return_bps"] != 1961.05


def test_missing_calibration_keeps_expected_return_uncalibrated():
    candidate = pd.DataFrame([{"ticker": "LIVE", "side": "Long", "candidate_rank": 1}])

    mapped = map_candidates_to_calibration(candidate, pd.DataFrame())

    assert mapped.iloc[0]["expected_return_quality"] == "invalid"
    assert mapped.iloc[0]["execution_block_reason"] == "expected_return_uncalibrated"


def test_side_specific_calibration_is_required_for_execution_mapping():
    calibration, _ = build_validation_bucket_calibration(_prediction_rows(1000, side="Long", forward=0.02))
    combined_only = calibration[calibration["side"].eq("combined")]
    candidate = pd.DataFrame([{"ticker": "LIVE", "side": "Long", "candidate_rank": 1}])

    mapped = map_candidates_to_calibration(candidate, combined_only)

    assert mapped.iloc[0]["execution_block_reason"] == "expected_return_uncalibrated"


def test_weak_calibration_rejected_by_default_and_allowed_only_by_config():
    calibration, _ = build_validation_bucket_calibration(_prediction_rows(500, side="Long", forward=0.02))
    candidate = pd.DataFrame([{"ticker": "LIVE", "side": "Long", "candidate_rank": 1}])

    default = map_candidates_to_calibration(candidate, calibration)
    allowed = map_candidates_to_calibration(candidate, calibration, weak_allowed_by_config=True, min_sample_count=30)

    assert default.iloc[0]["execution_block_reason"] == "expected_return_uncalibrated"
    assert allowed.iloc[0]["expected_return_quality"] == "weak_allowed_by_config"


def test_writer_creates_latest_calibration_and_summary(tmp_path: Path):
    outputs = write_validation_bucket_calibration(_prediction_rows(120), output_dir=tmp_path, stamp="20260101_000000")

    assert outputs.validation_rows_used == 120
    assert outputs.latest_path.exists()
    assert outputs.calibration_path.exists()
    assert outputs.summary_path.exists()


def test_diagnostic_module_adds_no_live_trading_path():
    source = Path("src/stockml/diagnostics/validation_bucket_calibration.py").read_text(encoding="utf-8")

    assert "submit_order" not in source
    assert "live_trading_enabled" not in source
