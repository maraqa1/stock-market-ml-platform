from pathlib import Path

import pandas as pd
import pytest

from stockml.trading_brain_v2.autopilot.ap_b01_gold_dataset_intake import GoldDatasetIntakeBlock
from stockml.trading_brain_v2.autopilot.ap_b02_candidate_normalizer import CandidateNormalizerBlock
from stockml.trading_brain_v2.autopilot.ap_b03_candidate_validity_gate import CandidateValidityGateBlock
from stockml.trading_brain_v2.autopilot.ap_b05_warning_interpreter import WarningInterpreterBlock
from stockml.trading_brain_v2.autopilot.ap_b10_entry_decision_engine import EntryDecisionEngineBlock
from stockml.trading_brain_v2.shared.models import EntryAction


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


def _real_ai2_row(**overrides):
    row = {
        "shortlist_rank": 1,
        "source_rank": 1,
        "symbol": "DXCM",
        "execution_decision": "Proceed candidate",
        "candidate_status": "executable",
        "side_action": "LONG",
        "approved_notional": 500,
        "latest_eod_date": "2026-08-06",
        "latest_eod_close": 84.75,
        "latest_intraday_price": 83.019996,
        "one_day_return_pct": 1.39,
        "five_day_return_pct": 8.0,
        "eod_volume": 4_236_000,
        "volatility_20d_pct": 3.91,
        "notes": "execution_candidate; risk medium; ok:price_checks_clear",
    }
    row.update(overrides)
    return row


def _ai2_enriched_bridge_row(**overrides):
    row = {
        "execution_rank": 1,
        "raw_rank": 1,
        "symbol": "CDNA",
        "side": "buy",
        "status": "executable",
        "execution_domain": "execution_candidate",
        "executable": True,
        "order_eligible": True,
        "final_execution_side": "LONG",
        "approved_notional": 250,
        "suggested_quantity": 5,
        "risk_tier": "medium",
        "validated_expected_return_bps": 31.8,
        "signal_id": "sig-cdna",
        "candidate_id": "cand-cdna",
        "event_key": "evt-cdna",
        "ai2_decision": "Proceed candidate",
        "ai2_price_check_status": "clean",
        "ai2_latest_eod_date": "2026-08-13",
        "ai2_latest_eod_close": 47.31,
        "ai2_latest_intraday_price": 47.25,
        "ai2_return_1d_pct": 0.17,
        "ai2_return_5d_pct": 3.98,
        "ai2_eod_volume": 1_234_567,
        "ai2_volatility_20d_pct": 4.64,
        "ai2_realtime_price": 47.4,
        "ai2_quote_timestamp": "2026-08-13T12:34:00Z",
        "ai2_quote_age_seconds": 45,
        "ai2_sma_20": 45.9,
        "ai2_sma_50": 43.8,
        "ai2_rsi_14": 61.5,
        "ai2_atr_14": 1.2,
        "ai2_news_count": 3,
        "ai2_sentiment_score": 0.31,
        "ai2_news_attention_score": 0.42,
        "ai2_exchange": "NASDAQ",
        "ai2_currency": "USD",
        "ai2_security_type": "Common Stock",
        "ai2_notes": "execution_candidate; risk medium; ok: price_checks_clear",
    }
    row.update(overrides)
    return row


def _real_ai2_fixture_rows():
    return [
        _real_ai2_row(symbol="DXCM", shortlist_rank=1, source_rank=6, execution_decision="Proceed candidate", candidate_status="executable", side_action="LONG", approved_notional=500, latest_eod_close=84.75, latest_intraday_price=83.019996, volatility_20d_pct=3.91, notes="execution_candidate; risk medium; ok:price_checks_clear"),
        _real_ai2_row(symbol="ATAI", shortlist_rank=2, source_rank=1, execution_decision="Review before execution", candidate_status="executable", side_action="LONG", approved_notional=250, latest_eod_close=7.25, latest_intraday_price=7.18, volatility_20d_pct=8.11, notes="execution_candidate; risk medium; warning:high_volatility"),
        _real_ai2_row(symbol="AMLX", shortlist_rank=3, source_rank=5, execution_decision="Review before execution", candidate_status="executable", side_action="LONG", approved_notional=250, latest_eod_close=22.82, latest_intraday_price=24.19, volatility_20d_pct=3.82, notes="execution_candidate; risk medium; warning:large_intraday_move"),
        _real_ai2_row(symbol="AVAV", shortlist_rank=4, source_rank=8, execution_decision="Do not execute until refreshed", candidate_status="executable", side_action="LONG", approved_notional=500, latest_eod_close=186.73, latest_intraday_price=171.119995, one_day_return_pct=9.12, five_day_return_pct=25.01, volatility_20d_pct=4.0, notes="execution_candidate; risk medium; warning:large_1d_move"),
        _real_ai2_row(symbol="CHCO", shortlist_rank=5, source_rank=20, execution_decision="Research only", candidate_status="research_only", side_action="NONE", approved_notional=0, latest_eod_close=120.0, latest_intraday_price=120.0, notes="shadow observation; ok:price_checks_clear"),
        _real_ai2_row(symbol="DSGR", shortlist_rank=6, source_rank=21, execution_decision="Not execution-ready", candidate_status="blocked", side_action="NONE", approved_notional=0, latest_eod_close=80.0, latest_intraday_price=80.0, notes="blocked; warning:price_check_failed"),
    ]


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
    newer = out / "ai2_enriched_execution_ranked_candidates_20260806_092244.csv"
    pd.DataFrame([_row(symbol="OLD")]).to_csv(older, index=False)
    pd.DataFrame([_ai2_enriched_bridge_row(symbol="NEW")]).to_csv(newer, index=False)

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
    assert [candidate.symbol for candidate in result.non_tradable_candidates] == ["BLOCKED"]


def test_ap_b03_rejects_missing_price_check_status():
    candidate = CandidateNormalizerBlock().normalize_record(_row(warning_codes="", price_check_clear=False))

    reasons = CandidateValidityGateBlock().validate_candidate(candidate)

    assert "price_check_status_missing_or_failed" in reasons


def test_real_ai2_schema_intake_loads_all_rows_and_preserves_source_fields(tmp_path: Path):
    path = tmp_path / "ai2_candidate_input_20260807_175619.shortlist.csv"
    rows = _real_ai2_fixture_rows()
    pd.DataFrame(rows).to_csv(path, index=False)

    result = GoldDatasetIntakeBlock().load_candidate_file(path=path)

    assert result.status == "ok"
    assert result.row_count == 6
    assert result.records[0]["shortlist_rank"] == 1
    assert result.records[0]["source_file"] == str(path)


def test_real_ai2_schema_normalizes_statuses_fields_and_warnings():
    candidates = CandidateNormalizerBlock().normalize_records(_real_ai2_fixture_rows()).candidates
    by_symbol = {candidate.symbol: candidate for candidate in candidates}

    assert by_symbol["DXCM"].rank == 1
    assert by_symbol["DXCM"].source_rank == 6
    assert by_symbol["DXCM"].side == "LONG"
    assert by_symbol["DXCM"].ai2_status == "proceed"
    assert by_symbol["DXCM"].close_price == 84.75
    assert by_symbol["DXCM"].intraday_price == 83.019996
    assert by_symbol["DXCM"].one_day_return == 0.0139
    assert by_symbol["DXCM"].five_day_return == 0.08
    assert by_symbol["DXCM"].twenty_day_volatility == 0.0391
    assert "price_checks_clear" in by_symbol["DXCM"].warning_codes
    assert by_symbol["ATAI"].ai2_status == "review"
    assert "high_volatility" in by_symbol["ATAI"].warning_codes
    assert by_symbol["AMLX"].ai2_status == "review"
    assert "large_intraday_move" in by_symbol["AMLX"].warning_codes
    assert by_symbol["AVAV"].ai2_status == "refresh_required"
    assert "large_1d_move" in by_symbol["AVAV"].warning_codes
    assert by_symbol["CHCO"].ai2_status == "research_only"
    assert by_symbol["DSGR"].ai2_status == "blocked"
    assert by_symbol["DXCM"].signal_id.startswith("sig-")
    assert by_symbol["DXCM"].candidate_id.startswith("cand-")
    assert by_symbol["DXCM"].event_id.startswith("evt-")


def test_real_ai2_schema_validity_separates_executable_non_tradable_and_invalid():
    rows = _real_ai2_fixture_rows()
    rows.append(_real_ai2_row(symbol="", shortlist_rank=7, source_rank=22))
    rows.append(_real_ai2_row(symbol="NOPRICE", shortlist_rank=8, source_rank=23, latest_eod_close=0))
    normalized = CandidateNormalizerBlock().normalize_records(rows)

    result = CandidateValidityGateBlock().validate_normalization_result(normalized)

    assert [candidate.symbol for candidate in result.valid_candidates] == ["DXCM", "ATAI", "AMLX", "AVAV"]
    assert [candidate.symbol for candidate in result.non_tradable_candidates] == ["CHCO", "DSGR"]
    issue_reasons = [reason for issue in result.invalid_candidates for reason in issue.reasons]
    assert "symbol_missing" in issue_reasons
    assert "latest_eod_close_missing_or_non_positive" in issue_reasons


def test_real_ai2_schema_warning_and_entry_decisions_are_deterministic():
    candidates = CandidateNormalizerBlock().normalize_records(_real_ai2_fixture_rows()).candidates
    by_symbol = {candidate.symbol: candidate for candidate in candidates}
    warning = WarningInterpreterBlock()
    engine = EntryDecisionEngineBlock()

    assert warning.interpret_candidate(by_symbol["DXCM"]).reason == "price_checks_clear_continue"
    assert engine.decide(by_symbol["DXCM"], live_price=84.75).action in {EntryAction.ENTER, EntryAction.ENTER_REDUCED}
    assert engine.decide(by_symbol["ATAI"], live_price=7.25).action is EntryAction.ENTER_REDUCED
    assert engine.decide(by_symbol["AMLX"], live_price=24.19).action is EntryAction.REFRESH_AND_RECHECK
    assert engine.decide(by_symbol["AVAV"], live_price=171.119995).action is EntryAction.REFRESH_AND_RECHECK
    assert engine.decide(by_symbol["CHCO"], live_price=120.0).action is EntryAction.BLOCK
    assert engine.decide(by_symbol["DSGR"], live_price=80.0).action is EntryAction.BLOCK


def test_ai2_enriched_bridge_schema_is_tradable_when_clean():
    rows = [
        _ai2_enriched_bridge_row(),
        _ai2_enriched_bridge_row(
            symbol="AEHR",
            execution_rank=2,
            ai2_decision="Do not execute until refreshed",
            ai2_price_check_status="warning",
            ai2_notes="execution_candidate; warning: large_1d_move",
        ),
    ]

    candidates = CandidateNormalizerBlock().normalize_records(rows).candidates
    result = CandidateValidityGateBlock().validate_normalization_result(CandidateNormalizerBlock().normalize_records(rows))
    by_symbol = {candidate.symbol: candidate for candidate in candidates}

    assert by_symbol["CDNA"].ai2_status == "proceed"
    assert by_symbol["CDNA"].decision_label == "Proceed candidate"
    assert by_symbol["CDNA"].latest_eod_date == "2026-08-13"
    assert by_symbol["CDNA"].close_price == 47.31
    assert by_symbol["CDNA"].intraday_price == 47.25
    assert by_symbol["CDNA"].realtime_price == 47.4
    assert by_symbol["CDNA"].quote_timestamp == "2026-08-13T12:34:00Z"
    assert by_symbol["CDNA"].quote_age_seconds == 45
    assert by_symbol["CDNA"].sma_20 == 45.9
    assert by_symbol["CDNA"].sma_50 == 43.8
    assert by_symbol["CDNA"].rsi_14 == 61.5
    assert by_symbol["CDNA"].atr_14 == 1.2
    assert by_symbol["CDNA"].news_count == 3
    assert by_symbol["CDNA"].sentiment_score == 0.31
    assert by_symbol["CDNA"].news_attention_score == 0.42
    assert by_symbol["CDNA"].exchange == "NASDAQ"
    assert by_symbol["CDNA"].currency == "USD"
    assert by_symbol["CDNA"].security_type == "Common Stock"
    assert by_symbol["CDNA"].one_day_return == pytest.approx(0.0017)
    assert by_symbol["CDNA"].five_day_return == pytest.approx(0.0398)
    assert by_symbol["CDNA"].twenty_day_volatility == pytest.approx(0.0464)
    assert by_symbol["CDNA"].eod_volume == 1_234_567
    assert by_symbol["CDNA"].price_check_clear is True
    assert by_symbol["AEHR"].ai2_status == "refresh_required"
    assert [candidate.symbol for candidate in result.valid_candidates] == ["CDNA", "AEHR"]
