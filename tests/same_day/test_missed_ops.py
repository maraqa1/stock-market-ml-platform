from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, insert

from portal.services import same_day_view
from stockml.db.schema import create_all, same_day_candidates
from stockml.same_day.missed_ops import build_missed_opportunities


SESSION = date(2026, 5, 12)


def _bars(symbol: str, open_price: float = 10.0, high: float = 11.0, low: float = 9.8, close: float = 10.8) -> list[dict]:
    return [
        {"symbol": symbol, "timestamp": "2026-05-12T14:30:00+00:00", "open": open_price, "high": open_price, "low": open_price, "close": open_price, "volume": 1000},
        {"symbol": symbol, "timestamp": "2026-05-12T15:00:00+00:00", "open": open_price, "high": high, "low": low, "close": close, "volume": 1000},
    ]


def test_missed_opp_excludes_traded_symbols():
    bars = pd.DataFrame(_bars("MOVE") + _bars("HELD"))

    report = build_missed_opportunities(
        session_date=SESSION,
        intraday_bars=bars,
        universe=pd.DataFrame([{"symbol": "MOVE"}, {"symbol": "HELD"}]),
        traded_symbols={"HELD"},
    )

    assert [row["symbol"] for row in report.rows] == ["MOVE"]


def test_missed_opp_records_blocking_gate():
    logs = pd.DataFrame(
        [
            {
                "symbol": "MOVE",
                "decision_time": "2026-05-12T15:00:00+00:00",
                "continuation_probability": 0.62,
                "block_reason": "REJECTED_WIDE_SPREAD",
            }
        ]
    )

    report = build_missed_opportunities(
        session_date=SESSION,
        intraday_bars=pd.DataFrame(_bars("MOVE")),
        universe=pd.DataFrame([{"symbol": "MOVE"}]),
        signal_log=logs,
    )

    assert report.rows[0]["first_blocking_gate"] == "REJECTED_WIDE_SPREAD"
    assert report.rows[0]["signal_log_count"] == 1
    assert report.rows[0]["max_continuation_probability"] == 0.62


def test_paper_assist_open_uses_correct_stream(monkeypatch):
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    with db.begin() as conn:
        result = conn.execute(
            insert(same_day_candidates).values(
                generated_at=datetime(2026, 5, 12, 15, tzinfo=timezone.utc),
                decision_time=datetime(2026, 5, 12, 15, tzinfo=timezone.utc),
                symbol="MOVE",
                direction="long",
                continuation_probability=0.7,
                reversal_probability=0.3,
                model_id="pytest",
                features_id=1,
                same_day_reason="pytest",
                strategy_stream="same_day_momentum",
                max_hold_days=1,
                must_flatten_eod=True,
                arbitration_outcome="emit",
            )
        )
        candidate_id = result.inserted_primary_key[0]
    monkeypatch.setattr(same_day_view, "get_engine", lambda required=False: db)

    result = same_day_view.record_same_day_operator_decision(candidate_id, "confirm")

    assert result["status"] == "recorded"
    assert result["strategy_stream"] == "same_day_momentum"
    assert result["must_flatten_at_eod"] is True


def test_zone_renders_only_emit_candidates(monkeypatch):
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    with db.begin() as conn:
        for symbol, outcome in [("EMIT", "emit"), ("SKIP", "suppressed")]:
            conn.execute(
                insert(same_day_candidates).values(
                    generated_at=datetime(2026, 5, 12, 15, tzinfo=timezone.utc),
                    decision_time=datetime(2026, 5, 12, 15, tzinfo=timezone.utc),
                    symbol=symbol,
                    direction="long",
                    continuation_probability=0.7,
                    reversal_probability=0.3,
                    model_id="pytest",
                    features_id=1,
                    same_day_reason="pytest",
                    strategy_stream="same_day_momentum",
                    max_hold_days=1,
                    must_flatten_eod=True,
                    arbitration_outcome=outcome,
                )
            )
    monkeypatch.setattr(same_day_view, "get_engine", lambda required=False: db)

    context = same_day_view.same_day_panel_context()

    assert [row["symbol"] for row in context["rows"]] == ["EMIT"]
