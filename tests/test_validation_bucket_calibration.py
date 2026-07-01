from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.diagnostics.validation_bucket_calibration import (
    HISTORICAL_GOLD_WARNING,
    build_gold_fallback_calibration,
    build_validation_bucket_calibration,
    map_candidates_to_calibration,
    prepare_gold_historical_rows,
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


def _gold_rows(count: int = 120, *, target_col: str = "target_return_5d", target: float = 0.02) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=count)
    return pd.DataFrame(
        {
            "date": dates,
            "ticker": [f"G{i:03d}" for i in range(count)],
            "sector": ["Technology"] * count,
            "model_score": [1.0 - (i / max(count, 1)) for i in range(count)],
            "rank_overall": list(range(1, count + 1)),
            target_col: [target] * count,
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


def test_empty_walk_forward_outputs_trigger_gold_fallback(tmp_path: Path):
    outputs = write_validation_bucket_calibration(
        pd.DataFrame(columns=["model_score", "forward_5d_return"]),
        output_dir=tmp_path,
        stamp="20260101_000000",
        gold_panel=_gold_rows(1200),
        gold_path=tmp_path / "gold.csv",
    )

    assert outputs.calibration_source == "gold_historical_targets"
    assert outputs.validation_rows_used > 0
    assert outputs.usable_buckets > 0
    assert HISTORICAL_GOLD_WARNING in outputs.summary_path.read_text(encoding="utf-8")


def test_gold_fallback_accepts_target_return_5d():
    calibration, rows, metadata = build_gold_fallback_calibration(_gold_rows(1200, target_col="target_return_5d"))

    assert rows > 0
    assert metadata["label_column_used"] == "target_return_5d"
    assert calibration["calibration_source"].eq("gold_historical_targets").all()


def test_gold_fallback_prefers_sector_relative_target():
    frame = _gold_rows(1200, target_col="target_return_5d")
    frame["target_sector_relative_return_5d"] = 0.03

    calibration, rows, metadata = build_gold_fallback_calibration(frame)

    assert rows > 0
    assert metadata["label_column_used"] == "target_sector_relative_return_5d"
    assert calibration["validation_warning"].eq(HISTORICAL_GOLD_WARNING).all()


def test_gold_latest_horizon_rows_are_excluded():
    frame = _gold_rows(40, target_col="target_return_5d")

    prepared, metadata = prepare_gold_historical_rows(frame, horizon_days=5)

    assert metadata["excluded_recent_rows"] == 5
    assert pd.to_datetime(prepared["date"]).max().date().isoformat() == metadata["max_label_date_used"]


def test_gold_missing_target_rows_are_excluded():
    frame = _gold_rows(40, target_col="target_return_5d")
    frame.loc[0:4, "target_return_5d"] = pd.NA

    prepared, _ = prepare_gold_historical_rows(frame, horizon_days=5)

    assert len(prepared) == (40 - 5 - 5) * 2


def test_gold_long_and_short_bps_calculation_and_costs():
    frame = _gold_rows(40, target_col="target_return_5d", target=0.02)

    prepared, _ = prepare_gold_historical_rows(frame, horizon_days=5, estimated_spread_cost_bps=3, estimated_slippage_bps=5, borrow_cost_estimate_bps=2)
    long_value = prepared[prepared["side"].eq("Long")]["forward_return_bps"].iloc[0]
    short_value = prepared[prepared["side"].eq("Short")]["forward_return_bps"].iloc[0]

    assert long_value == 192.0
    assert short_value == -210.0


def test_missing_gold_target_returns_insufficient_data():
    frame = _gold_rows(40, target_col="target_return_5d").drop(columns=["target_return_5d"])

    calibration, rows, metadata = build_gold_fallback_calibration(frame)

    assert rows == 0
    assert calibration.empty
    assert metadata["label_column_used"] == ""


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
