from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from stockml.trading.snapshot_schema import (
    CANONICAL_COLUMNS,
    DEPRECATED_SHIM_COLUMNS,
    Direction,
    FunnelStage,
    Pool,
    ScoreBasis,
    ScoreState,
    SnapshotRow,
    validate_snapshot_row,
)
from stockml.trading.snapshot_writer import build_snapshot_row, write_snapshot_csv


SNAPSHOT_AT = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)


def _csv_rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_every_pool_writer_returns_canonical_rows():
    fixtures = [
        ("model_shortlist", {"symbol": "AAA", "side": "buy", "candidate_rank": 1, "risk_adjusted_score": 0.8}, Pool.MODEL_SHORTLIST),
        ("per_symbol_forecast", {"symbol": "BBB", "side": "sell", "candidate_rank": 2, "volatility_adjusted_score": 1.2}, Pool.PER_SYMBOL_FORECAST),
        ("near_miss", {"symbol": "CCC", "side": "buy", "failed_gate": "risk_adjusted_score_below_threshold"}, Pool.NEAR_MISS),
        ("intraday_promotion", {"symbol": "DDD", "side": "sell", "verdict": "block", "block_reason": "wide_spread"}, Pool.INTRADAY_PROMOTION),
        ("todays_basket", {"symbol": "EEE", "side": "buy", "order_eligible": True, "risk_adjusted_score": 0.4}, Pool.TODAYS_BASKET),
        ("rejected_trimmed", {"symbol": "FFF", "side": "sell", "trade_quality_reason": "Price below minimum"}, Pool.REJECTED_TRIMMED),
        ("action_queue", {"symbol": "GGG", "side": "long", "decision": "open_candidate"}, Pool.ACTION_QUEUE),
        ("open_positions", {"symbol": "HHH", "side": "long", "qty": 2, "market_value": 100}, Pool.OPEN_POSITIONS),
    ]

    for pool, row, expected_pool in fixtures:
        snapshot_row = build_snapshot_row(pool, row, snapshot_at=SNAPSHOT_AT)
        assert validate_snapshot_row(snapshot_row).pool == expected_pool


def test_canonical_columns_present_in_csv():
    text = write_snapshot_csv([("model_shortlist", [{"symbol": "AAA", "side": "buy", "candidate_rank": 1}], "", "fixture")], snapshot_at=SNAPSHOT_AT)
    header = text.splitlines()[0].split(",")

    for column in CANONICAL_COLUMNS:
        assert column in header


def test_backward_compat_columns_present():
    text = write_snapshot_csv([("model_shortlist", [{"symbol": "AAA", "side": "buy", "candidate_rank": 1, "risk_adjusted_score": 0.7}], "", "fixture")], snapshot_at=SNAPSHOT_AT)
    row = _csv_rows(text)[0]

    for column in DEPRECATED_SHIM_COLUMNS:
        assert column in row
    assert row["side"] == "buy"
    assert row["action"] == "Long"
    assert row["score"] == "0.7"
    assert row["score_state"] == ScoreState.AVAILABLE.value


def test_score_basis_matches_pool_convention():
    rows = _csv_rows(
        write_snapshot_csv(
            [
                ("model_shortlist", [{"symbol": "AAA", "side": "buy", "risk_adjusted_score": 0.7}], "", "fixture"),
                ("intraday_promotion", [{"symbol": "BBB", "side": "buy", "promotion_score": 0.9}], "", "fixture"),
                ("per_symbol_forecast", [{"symbol": "CCC", "side": "buy", "volatility_adjusted_score": 2.0}], "", "fixture"),
                ("action_queue", [{"symbol": "DDD", "side": "long", "details": {"candidate_source": "per_symbol_forecast"}}], "", "fixture"),
            ],
            snapshot_at=SNAPSHOT_AT,
        )
    )

    by_pool = {row["pool"]: row["score_basis"] for row in rows}
    assert by_pool["model_shortlist"] == ScoreBasis.RAW_RANK.value
    assert by_pool["intraday_promotion"] == ScoreBasis.PROMOTION.value
    assert by_pool["per_symbol_forecast"] == ScoreBasis.NONE.value
    assert by_pool["action_queue"] == ScoreBasis.VOLATILITY_ADJUSTED.value


def test_action_queue_direction_normalization():
    row = _csv_rows(write_snapshot_csv([("action_queue", [{"symbol": "AAA", "side": "long"}], "", "fixture")], snapshot_at=SNAPSHOT_AT))[0]

    assert row["direction"] == Direction.LONG.value
    assert row["side"] == "buy"
    assert row["action"] == "open_candidate"


def test_validate_rejects_invalid_rows():
    valid = SnapshotRow(
        snapshot_at=SNAPSHOT_AT,
        pool=Pool.MODEL_SHORTLIST,
        symbol="AAA",
        generated_at=SNAPSHOT_AT,
        direction=Direction.LONG,
        funnel_stage=FunnelStage.SCORED,
        rank=1,
        raw_score=0.1,
        display_score=0.1,
        score_basis=ScoreBasis.RAW_RANK,
        score_state=ScoreState.AVAILABLE,
        outcome=None,
        outcome_reason=None,
    )

    invalids = [
        {**valid.__dict__, "direction": "buy"},
        {**valid.__dict__, "funnel_stage": "done"},
        {**valid.__dict__, "score_basis": "score"},
        {**valid.__dict__, "outcome_reason": "reason"},
        {**valid.__dict__, "rank": 0},
        {**valid.__dict__, "data_age_seconds": -1},
        {**valid.__dict__, "pool": Pool.PER_SYMBOL_FORECAST, "display_score": 1.0},
        {**valid.__dict__, "raw_json": {"symbol": "AAA"}},
    ]
    for payload in invalids:
        with pytest.raises(ValueError):
            validate_snapshot_row(SnapshotRow(**payload))


def test_funnel_stage_is_terminal_per_row():
    rejected = build_snapshot_row("rejected_trimmed", {"symbol": "AAA", "side": "buy", "reason": "Price below minimum"}, snapshot_at=SNAPSHOT_AT)
    selected = build_snapshot_row("todays_basket", {"symbol": "BBB", "side": "buy", "order_eligible": True}, snapshot_at=SNAPSHOT_AT)
    filled = build_snapshot_row("open_positions", {"symbol": "CCC", "side": "long", "qty": 1}, snapshot_at=SNAPSHOT_AT)

    assert rejected.funnel_stage == FunnelStage.REJECTED
    assert selected.funnel_stage == FunnelStage.SELECTED
    assert filled.funnel_stage == FunnelStage.FILLED


def test_raw_json_does_not_duplicate_canonical():
    text = write_snapshot_csv([("model_shortlist", [{"symbol": "AAA", "side": "buy", "candidate_rank": 1, "risk_tier": "low"}], "", "fixture")], snapshot_at=SNAPSHOT_AT)
    row = _csv_rows(text)[0]
    raw = json.loads(row["raw_json"])

    assert "symbol" not in raw
    assert "rank" not in raw
    assert raw["risk_tier"] == "low"


def test_data_age_seconds_uses_generated_at():
    generated = SNAPSHOT_AT - timedelta(minutes=5)
    row = build_snapshot_row("model_shortlist", {"symbol": "AAA", "side": "buy"}, snapshot_at=SNAPSHOT_AT, generated_at=generated)

    assert row.data_age_seconds == 300


def test_blank_display_scores_have_explicit_score_state():
    rows = _csv_rows(
        write_snapshot_csv(
            [
                ("per_symbol_forecast", [{"symbol": "AAA", "side": "buy", "volatility_adjusted_score": 2.0}], "", "fixture"),
                ("open_positions", [{"symbol": "BBB", "side": "long", "qty": 1}], "", "fixture"),
                ("model_shortlist", [{"symbol": "CCC", "side": "buy"}], "", "fixture"),
            ],
            snapshot_at=SNAPSHOT_AT,
        )
    )
    by_symbol = {row["symbol"]: row for row in rows}

    assert by_symbol["AAA"]["display_score"] == ""
    assert by_symbol["AAA"]["score_state"] == ScoreState.SUPPRESSED_DIAGNOSTIC.value
    assert by_symbol["BBB"]["display_score"] == ""
    assert by_symbol["BBB"]["score_state"] == ScoreState.NOT_APPLICABLE.value
    assert by_symbol["CCC"]["display_score"] == ""
    assert by_symbol["CCC"]["score_state"] == ScoreState.MISSING_SOURCE.value
