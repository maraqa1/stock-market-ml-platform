from pathlib import Path

import pandas as pd

from stockml.trading_brain_v2.autopilot.ap_b01_gold_dataset_intake import GoldDatasetIntakeBlock
from stockml.trading_brain_v2.autopilot.ap_b02_candidate_normalizer import CandidateNormalizerBlock
from stockml.trading_brain_v2.autopilot.ap_b03_candidate_validity_gate import CandidateValidityGateBlock


def _row(**overrides):
    row = {
        "symbol": "ATRC",
        "side": "LONG",
        "execution_rank": 1,
        "candidate_status": "executable",
        "Decision": "Proceed candidate",
        "approved_notional": 250,
        "suggested_quantity": 6,
        "risk_tier": "medium",
        "latest_eod_date": "2026-08-06",
        "close": 39.49,
        "validated_expected_return_bps": 31.8,
        "return_1d": 0.0038,
        "return_5d": -0.0164,
        "volatility_20d": 0.032,
        "volume": 753722,
        "warning_codes": "price_checks_clear",
        "signal_id": "sig-1",
        "candidate_id": "cand-1",
        "event_id": "evt-1",
    }
    row.update(overrides)
    return row


def test_ap_b01_loads_explicit_csv_and_preserves_source_file(tmp_path: Path):
    path = tmp_path / "ai2_candidate_input_20260806_092244.shortlist.csv"
    pd.DataFrame([_row()]).to_csv(path, index=False)

    result = GoldDatasetIntakeBlock().load_candidate_file(path=path)

    assert result.status == "ok"
    assert result.row_count == 1
    assert result.records[0]["source_file"] == str(path)


def test_ap_b01_loads_latest_candidate_file_from_pipeline_location(tmp_path: Path):
    out = tmp_path / "data" / "portal_outputs"
    out.mkdir(parents=True)
    older = out / "execution_ranked_candidates_20260806_010000.csv"
    newer = out / "ai2_candidate_input_20260806_092244.shortlist.csv"
    pd.DataFrame([_row(symbol="OLD")]).to_csv(older, index=False)
    pd.DataFrame([_row(symbol="NEW")]).to_csv(newer, index=False)

    result = GoldDatasetIntakeBlock().load_candidate_file(root=tmp_path)

    assert result.status == "ok"
    assert result.records[0]["symbol"] == "NEW"


def test_ap_b02_normalizes_proceed_review_and_refresh_candidates():
    normalizer = CandidateNormalizerBlock()
    result = normalizer.normalize_records(
        [
            _row(symbol="ATRC", **{"Decision": "Proceed candidate"}),
            _row(symbol="ATAI", candidate_id="cand-2", event_id="evt-2", **{"Decision": "Review before execution"}),
            _row(symbol="FRPT", candidate_id="cand-3", event_id="evt-3", **{"Decision": "Do not execute until refreshed"}),
        ]
    )

    assert [candidate.ai2_status for candidate in result.candidates] == ["proceed", "review", "refresh_required"]
    assert [candidate.side for candidate in result.candidates] == ["LONG", "LONG", "LONG"]


def test_ap_b02_accepts_real_ai2_execution_decision_column():
    row = _row()
    row.pop("Decision")
    row["execution_decision"] = "Proceed candidate"

    candidate = CandidateNormalizerBlock().normalize_record(row)

    assert candidate.ai2_status == "proceed"
    assert candidate.decision_label == "Proceed candidate"


def test_ap_b03_validates_fixture_candidates():
    normalizer = CandidateNormalizerBlock()
    records = [
        _row(symbol="ATRC"),
        _row(symbol="", candidate_id="cand-2", event_id="evt-2"),
        _row(symbol="NOPRICE", close=0, candidate_id="cand-3", event_id="evt-3"),
        _row(symbol="BLOCKED", candidate_status="blocked", candidate_id="cand-4", event_id="evt-4"),
    ]
    normalized = normalizer.normalize_records(records)
    gate = CandidateValidityGateBlock()
    result = gate.validate_normalization_result(normalized)

    assert [candidate.symbol for candidate in result.valid_candidates] == ["ATRC"]
    reason_map = {issue.candidate.symbol: issue.reasons for issue in result.invalid_candidates if issue.candidate is not None}
    assert any("symbol_missing" in issue.reasons for issue in result.invalid_candidates)
    assert "close_price_missing_or_non_positive" in reason_map["NOPRICE"]
    assert "candidate_not_executable" in reason_map["BLOCKED"]


def test_ap_b03_rejects_missing_price_check_status():
    candidate = CandidateNormalizerBlock().normalize_record(_row(warning_codes="", price_check_clear=False))

    reasons = CandidateValidityGateBlock().validate_candidate(candidate)

    assert "price_check_status_missing_or_failed" in reasons
