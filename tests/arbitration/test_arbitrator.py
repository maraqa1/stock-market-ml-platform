from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, select

from stockml.arbitration.arbitrator import arbitrate_streams
from stockml.db.schema import arbitration_conflicts, create_all


NOW = datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc)


def _engine():
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    return db


def md(symbol: str, action: str = "Long", **extra):
    row = {"symbol": symbol, "trade_action": action, "strategy_stream": "multi_day_forecast", "risk_adjusted_score": 1.0}
    row.update(extra)
    return row


def sd(symbol: str, action: str = "long", **extra):
    row = {"symbol": symbol, "direction": action, "continuation_probability": 0.72, "strategy_stream": "same_day_momentum"}
    row.update(extra)
    return row


def test_arbitration_held_by_multi_day():
    db = _engine()
    result = arbitrate_streams(
        same_day_candidates=pd.DataFrame([sd("AAA")]),
        open_positions=pd.DataFrame([{"symbol": "AAA", "strategy_stream": "multi_day_forecast"}]),
        multi_day_candidate_pool=pd.DataFrame(),
        engine=db,
        now=NOW,
    )

    with db.connect() as conn:
        conflicts = conn.execute(select(arbitration_conflicts.c.resolution)).all()

    assert result.empty
    assert conflicts == [("BLOCKED_HELD_BY_MULTI_DAY",)]


def test_arbitration_multi_day_wins_on_long_long():
    result = arbitrate_streams(
        same_day_candidates=pd.DataFrame([sd("AAA", "long")]),
        open_positions=pd.DataFrame(),
        multi_day_candidate_pool=pd.DataFrame([md("AAA", "Long")]),
        now=NOW,
    )

    assert len(result) == 1
    assert result.iloc[0]["strategy_stream"] == "multi_day_forecast"
    assert result.iloc[0]["arbitration_resolution"] == "MULTI_DAY_WINS_ALIGNED"


def test_arbitration_conflict_abstains():
    db = _engine()
    result = arbitrate_streams(
        same_day_candidates=pd.DataFrame([sd("AAA", "short")]),
        open_positions=pd.DataFrame(),
        multi_day_candidate_pool=pd.DataFrame([md("AAA", "Long")]),
        engine=db,
        now=NOW,
    )

    with db.connect() as conn:
        rows = conn.execute(select(arbitration_conflicts.c.symbol, arbitration_conflicts.c.resolution)).all()

    assert result.empty
    assert rows == [("AAA", "CONFLICT_ABSTAIN")]


def test_arbitration_same_day_fills_no_decision():
    result = arbitrate_streams(
        same_day_candidates=pd.DataFrame([sd("AAA", "long")]),
        open_positions=pd.DataFrame(),
        multi_day_candidate_pool=pd.DataFrame([md("AAA", "No Decision")]),
        now=NOW,
    )

    assert len(result) == 1
    assert result.iloc[0]["strategy_stream"] == "same_day_momentum"
    assert bool(result.iloc[0]["must_flatten_at_eod"]) is True
    assert result.iloc[0]["arbitration_resolution"] == "SAME_DAY_FILLS_NO_DECISION"


def test_arbitration_same_day_only_emits():
    result = arbitrate_streams(
        same_day_candidates=pd.DataFrame([sd("AAA", "long")]),
        open_positions=pd.DataFrame(),
        multi_day_candidate_pool=pd.DataFrame(),
        now=NOW,
    )

    assert len(result) == 1
    assert result.iloc[0]["strategy_stream"] == "same_day_momentum"
    assert result.iloc[0]["arbitration_resolution"] == "SAME_DAY_ONLY"


def test_existing_multi_day_behavior_unchanged():
    source = pd.DataFrame([md("AAA", "Long"), md("BBB", "Short")])

    result = arbitrate_streams(
        same_day_candidates=pd.DataFrame(),
        open_positions=pd.DataFrame(),
        multi_day_candidate_pool=source,
        now=NOW,
    )

    assert list(result["symbol"]) == ["AAA", "BBB"]
    assert set(result["strategy_stream"]) == {"multi_day_forecast"}
    assert list(result["trade_action"]) == ["Long", "Short"]
