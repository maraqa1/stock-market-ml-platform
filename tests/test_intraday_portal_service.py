from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, insert

from portal.services.intraday import decisions_payload, intraday_context, shadow_track_record
from stockml.db.schema import create_all, intraday_decisions, shadow_outcomes, shadow_would_trades


NOW = datetime(2026, 5, 11, 15, 0, tzinfo=timezone.utc)


def engine():
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    return db


def seed(db):
    with db.begin() as conn:
        decision_id = conn.execute(
            insert(intraday_decisions).values(
                decided_at=NOW,
                symbol="TSLA",
                bar_close_at=NOW,
                verdict="allow_long",
                gate_version="v1.0.0",
                valid_until=NOW,
                nightly_signal={"bias": "long", "score": 0.71},
                features={"mid_price": 100},
                contributing=["trend_5m_positive", "trend_15m_positive"],
            )
        ).inserted_primary_key[0]
        trade_id = conn.execute(
            insert(shadow_would_trades).values(
                decision_id=decision_id,
                decided_at=NOW,
                symbol="TSLA",
                side="long",
                entry_price=100,
                estimated_entry_slippage_bps=10,
                nightly_score=0.71,
                gate_version="v1.0.0",
                evaluation_date=date(2026, 6, 8),
                status="evaluated",
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(shadow_outcomes).values(
                would_trade_id=trade_id,
                evaluated_at=NOW,
                exit_price=112,
                raw_return_pct=0.12,
                cost_bps=20,
                net_return_pct=0.118,
                spy_return_pct=0.01,
                net_excess_pct=0.108,
                outperformed=True,
            )
        )


def test_intraday_decisions_payload_summarizes_flow():
    db = engine()
    seed(db)

    payload = decisions_payload({"limit": 10}, db)

    assert payload["summary"]["decisions"] == 1
    assert payload["summary"]["would_trades"] == 1
    assert payload["summary"]["blocks"] == 0
    assert payload["rows"][0]["symbol"] == "TSLA"
    assert payload["rows"][0]["nightly_signal"]["score"] == 0.71


def test_shadow_track_record_summarizes_evaluated_outcomes():
    db = engine()
    seed(db)

    payload = shadow_track_record(db)

    assert payload["summary"]["n_evaluated"] == 1
    assert payload["summary"]["mean_net_excess_pct"] == pytest.approx(0.108)
    assert payload["summary"]["hit_rate"] == pytest.approx(1.0)
    assert payload["rows"][0]["net_return_pct"] == pytest.approx(0.118)


def test_intraday_context_composes_all_operator_zones():
    db = engine()
    seed(db)

    payload = intraday_context(target=db)

    assert payload["flow"]["summary"]["decisions"] == 1
    assert payload["track_record"]["summary"]["n_evaluated"] == 1
    assert payload["kill_switches"]["switches"]
    assert payload["promotion"]["criteria"]
