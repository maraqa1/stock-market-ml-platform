from __future__ import annotations

import pandas as pd

from scripts.check_trading_snapshot_quality import FAIL, PASS, WARN, check_cross_stage_symbol_overlap, check_duplicate_symbol_direction, check_exact_duplicate_near_miss, check_ghost_rows, check_null_outcome, check_score_raw_score_mismatch, check_short_scores_and_ranks, check_stale_data, run_checks


def test_ghost_rows_fail():
    frame = pd.DataFrame([{"symbol": "AAA", "raw_score": None, "outcome": None}])

    result = check_ghost_rows(frame)

    assert result.status == FAIL
    assert "AAA" in result.details[0]


def test_short_negative_score_and_missing_rank_warn():
    frame = pd.DataFrame([{"symbol": "SHRT", "direction": "short", "raw_score": -0.1, "rank": None}])

    result = check_short_scores_and_ranks(frame)

    assert result.status == WARN
    assert any("negative raw_score" in detail for detail in result.details)
    assert any("unranked shorts" in detail for detail in result.details)


def test_score_raw_score_mismatch_reports_offset():
    frame = pd.DataFrame([{"symbol": "AAA", "direction": "long", "outcome": "accepted", "raw_score": 1.0, "score": 1.25}])

    result = check_score_raw_score_mismatch(frame)

    assert result.status == WARN
    assert "diff=0.25" in result.details[0]


def test_score_raw_score_mismatch_is_pass_when_promotion_adjustment_explains_delta():
    frame = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "direction": "neutral",
                "outcome": "accepted",
                "raw_score": 1.0,
                "score": 1.03,
                "raw_json": '{"promotion_adjustment": 0.03}',
            }
        ]
    )

    result = check_score_raw_score_mismatch(frame)

    assert result.status == PASS


def test_duplicate_symbol_direction_only_flags_same_pool_duplicates():
    frame = pd.DataFrame(
        [
            {"pool": "model_shortlist", "symbol": "AAA", "direction": "long"},
            {"pool": "per_symbol_forecast", "symbol": "AAA", "direction": "long"},
        ]
    )

    result = check_duplicate_symbol_direction(frame)

    assert result.status == PASS


def test_cross_stage_symbol_overlap_warns_for_expected_funnel_overlap():
    frame = pd.DataFrame(
        [
            {"pool": "model_shortlist", "symbol": "AAA", "direction": "long"},
            {"pool": "per_symbol_forecast", "symbol": "AAA", "direction": "long"},
        ]
    )

    result = check_cross_stage_symbol_overlap(frame)

    assert result.status == WARN


def test_null_outcome_suggests_stage_fill_value():
    frame = pd.DataFrame([{"symbol": "AAA", "outcome": None, "funnel_stage": "scored"}])

    result = check_null_outcome(frame)

    assert result.status == WARN
    assert "suggested outcome='scored'" in result.details[0]


def test_exact_duplicate_near_miss_fails():
    frame = pd.DataFrame(
        [
            {"pool": "near_miss", "symbol": "AAA", "direction": "long", "outcome": "near_miss", "raw_score": 0.5},
            {"pool": "near_miss", "symbol": "AAA", "direction": "long", "outcome": "near_miss", "raw_score": 0.5},
        ]
    )

    result = check_exact_duplicate_near_miss(frame)

    assert result.status == FAIL
    assert "AAA" in result.details[0]


def test_stale_data_warns_above_threshold():
    frame = pd.DataFrame([{"pool": "model_shortlist", "symbol": "AAA", "data_age_seconds": 7200}])

    result = check_stale_data(frame, threshold_seconds=3600)

    assert result.status == WARN
    assert "7200s" in result.details[0]


def test_run_checks_passes_clean_minimal_snapshot():
    frame = pd.DataFrame(
        [
            {
                "pool": "model_shortlist",
                "symbol": "AAA",
                "direction": "long",
                "rank": 1,
                "raw_score": 0.5,
                "score": 0.5,
                "outcome": "accepted",
                "funnel_stage": "selected",
                "data_age_seconds": 10,
            }
        ]
    )

    results = run_checks(frame)

    assert all(result.status in {PASS, WARN} for result in results)
    assert not any(result.status == FAIL for result in results)
