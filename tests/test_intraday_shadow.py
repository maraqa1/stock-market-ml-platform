from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, insert, select

from stockml.db.schema import create_all, intraday_decisions, price_history, shadow_outcomes, shadow_would_trades
from stockml.intraday.features import IntradayFeatures, NightlySignal
from stockml.intraday.gates import GATE_VERSION, GateDecision, next_five_minute_boundary
from stockml.intraday.decisions import record_decision
from stockml.intraday.shadow import add_trading_days, evaluate_pending_outcomes, estimated_entry_slippage_bps, mark_superseded_for_position


NOW = datetime(2026, 5, 11, 15, 0, tzinfo=timezone.utc)


def engine():
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    return db


def allow_verdict(verdict="allow_long"):
    return GateDecision(verdict, None, next_five_minute_boundary(NOW), GATE_VERSION, ["pytest"])


def features(**overrides):
    payload = {
        "mid_price": 100.0,
        "spread_bps": 12.0,
        "decided_at": NOW,
    }
    payload.update(overrides)
    return IntradayFeatures(**payload)


def kill_allow(**kwargs):
    return type("KillVerdict", (), {"allow": True, "tripped": []})()


def kill_block(**kwargs):
    return type("KillVerdict", (), {"allow": False, "tripped": ["daily.realized_plus_unrealized_loss_usd"]})()


def test_allow_decision_creates_shadow_would_trade_in_same_transaction(monkeypatch):
    db = engine()
    monkeypatch.setattr("stockml.intraday.shadow.kill_switch_gate", kill_allow)

    row = record_decision("TSLA", features(), allow_verdict(), NightlySignal("TSLA", "long", score=0.71), engine=db, decided_at=NOW)

    with db.connect() as conn:
        decision_rows = conn.execute(select(intraday_decisions)).all()
        shadow_rows = conn.execute(select(shadow_would_trades)).all()

    assert len(decision_rows) == 1
    assert len(shadow_rows) == 1
    assert row["shadow_would_trade_id"] == shadow_rows[0]._mapping["id"]
    assert shadow_rows[0]._mapping["decision_id"] == decision_rows[0]._mapping["id"]
    assert shadow_rows[0]._mapping["side"] == "long"
    assert shadow_rows[0]._mapping["entry_price"] == 100.0
    assert shadow_rows[0]._mapping["estimated_entry_slippage_bps"] == 11.0
    assert shadow_rows[0]._mapping["nightly_score"] == 0.71
    assert shadow_rows[0]._mapping["evaluation_date"] == add_trading_days(NOW.date(), 20)


def test_hold_decision_does_not_create_shadow_would_trade(monkeypatch):
    db = engine()
    monkeypatch.setattr("stockml.intraday.shadow.kill_switch_gate", kill_allow)

    record_decision("TSLA", features(), allow_verdict("hold"), NightlySignal("TSLA", "long"), engine=db, decided_at=NOW)

    with db.connect() as conn:
        assert len(conn.execute(select(intraday_decisions)).all()) == 1
        assert conn.execute(select(shadow_would_trades)).all() == []


def test_kill_switch_blocks_shadow_would_trade_but_keeps_decision(monkeypatch):
    db = engine()
    monkeypatch.setattr("stockml.intraday.shadow.kill_switch_gate", kill_block)

    record_decision("TSLA", features(), allow_verdict(), NightlySignal("TSLA", "long"), engine=db, decided_at=NOW)

    with db.connect() as conn:
        assert len(conn.execute(select(intraday_decisions)).all()) == 1
        assert conn.execute(select(shadow_would_trades)).all() == []


def test_shadow_cooloff_prevents_duplicate_pending_would_trade(monkeypatch):
    db = engine()
    monkeypatch.setattr("stockml.intraday.shadow.kill_switch_gate", kill_allow)

    record_decision("TSLA", features(decided_at=NOW), allow_verdict(), NightlySignal("TSLA", "long"), engine=db, decided_at=NOW)
    record_decision(
        "TSLA",
        features(decided_at=NOW + timedelta(minutes=30)),
        allow_verdict(),
        NightlySignal("TSLA", "long"),
        engine=db,
        decided_at=NOW + timedelta(minutes=30),
    )

    with db.connect() as conn:
        assert len(conn.execute(select(intraday_decisions)).all()) == 2
        assert len(conn.execute(select(shadow_would_trades)).all()) == 1


def test_opposite_side_can_create_separate_shadow_trade(monkeypatch):
    db = engine()
    monkeypatch.setattr("stockml.intraday.shadow.kill_switch_gate", kill_allow)

    record_decision("TSLA", features(), allow_verdict("allow_long"), NightlySignal("TSLA", "long"), engine=db, decided_at=NOW)
    record_decision("TSLA", features(), allow_verdict("allow_short"), NightlySignal("TSLA", "short"), engine=db, decided_at=NOW + timedelta(minutes=5))

    with db.connect() as conn:
        rows = conn.execute(select(shadow_would_trades.c.side).order_by(shadow_would_trades.c.side)).all()
    assert rows == [("long",), ("short",)]


def test_mark_superseded_for_position_updates_pending_same_symbol_side(monkeypatch):
    db = engine()
    monkeypatch.setattr("stockml.intraday.shadow.kill_switch_gate", kill_allow)
    record_decision("TSLA", features(), allow_verdict("allow_long"), NightlySignal("TSLA", "long"), engine=db, decided_at=NOW)
    record_decision("NVDA", features(), allow_verdict("allow_long"), NightlySignal("NVDA", "long"), engine=db, decided_at=NOW)

    updated = mark_superseded_for_position("TSLA", "long", NOW + timedelta(minutes=15), engine=db)

    with db.connect() as conn:
        rows = conn.execute(select(shadow_would_trades.c.symbol, shadow_would_trades.c.status).order_by(shadow_would_trades.c.symbol)).all()
    assert updated == 1
    assert rows == [("NVDA", "pending"), ("TSLA", "superseded")]


def test_estimated_entry_cost_is_half_spread_plus_market_impact():
    assert estimated_entry_slippage_bps(features(spread_bps=20)) == 15.0
    assert estimated_entry_slippage_bps(features(spread_bps=None)) == 5.0


def _seed_price(conn, selected_date: date, ticker: str, close: float):
    conn.execute(
        insert(price_history).values(
            date=selected_date,
            ticker=ticker,
            open=close,
            high=close,
            low=close,
            close=close,
            adj_close=close,
            volume=1_000_000,
            source="pytest",
        )
    )


def test_evaluate_pending_outcomes_writes_net_of_cost_result_idempotently(monkeypatch):
    db = engine()
    monkeypatch.setattr("stockml.intraday.shadow.kill_switch_gate", kill_allow)
    row = record_decision("TSLA", features(mid_price=100, spread_bps=20), allow_verdict("allow_long"), NightlySignal("TSLA", "long"), engine=db, decided_at=NOW)
    eval_date = add_trading_days(NOW.date(), 20)
    with db.begin() as conn:
        _seed_price(conn, NOW.date(), "SPY", 400)
        _seed_price(conn, eval_date, "SPY", 404)
        _seed_price(conn, eval_date, "TSLA", 112)

    first = evaluate_pending_outcomes(as_of_date=eval_date, evaluated_at=NOW + timedelta(days=30), engine=db)
    second = evaluate_pending_outcomes(as_of_date=eval_date, evaluated_at=NOW + timedelta(days=31), engine=db)

    with db.connect() as conn:
        outcomes = conn.execute(select(shadow_outcomes)).mappings().all()
        trade_status = conn.execute(select(shadow_would_trades.c.status).where(shadow_would_trades.c.id == row["shadow_would_trade_id"])).scalar_one()

    assert first == {"evaluated": 1, "skipped_missing_price": 0}
    assert second == {"evaluated": 0, "skipped_missing_price": 0}
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome["raw_return_pct"] == pytest.approx(0.12)
    assert outcome["cost_bps"] == pytest.approx(30.0)
    assert outcome["net_return_pct"] == pytest.approx(0.117)
    assert outcome["spy_return_pct"] == pytest.approx(0.01)
    assert outcome["net_excess_pct"] == pytest.approx(0.107)
    assert outcome["outperformed"] is True
    assert trade_status == "evaluated"


def test_evaluate_pending_outcomes_handles_short_return(monkeypatch):
    db = engine()
    monkeypatch.setattr("stockml.intraday.shadow.kill_switch_gate", kill_allow)
    record_decision("TSLA", features(mid_price=100, spread_bps=10), allow_verdict("allow_short"), NightlySignal("TSLA", "short"), engine=db, decided_at=NOW)
    eval_date = add_trading_days(NOW.date(), 20)
    with db.begin() as conn:
        _seed_price(conn, NOW.date(), "SPY", 400)
        _seed_price(conn, eval_date, "SPY", 400)
        _seed_price(conn, eval_date, "TSLA", 90)

    result = evaluate_pending_outcomes(as_of_date=eval_date, engine=db)

    with db.connect() as conn:
        outcome = conn.execute(select(shadow_outcomes)).mappings().one()
    assert result["evaluated"] == 1
    assert outcome["raw_return_pct"] == pytest.approx(0.10)
    assert outcome["cost_bps"] == pytest.approx(20.0)
    assert outcome["net_return_pct"] == pytest.approx(0.098)


def test_evaluate_pending_outcomes_skips_missing_prices(monkeypatch):
    db = engine()
    monkeypatch.setattr("stockml.intraday.shadow.kill_switch_gate", kill_allow)
    record_decision("TSLA", features(), allow_verdict(), NightlySignal("TSLA", "long"), engine=db, decided_at=NOW)

    result = evaluate_pending_outcomes(as_of_date=add_trading_days(NOW.date(), 20), engine=db)

    with db.connect() as conn:
        assert conn.execute(select(shadow_outcomes)).all() == []
        assert conn.execute(select(shadow_would_trades.c.status)).scalar_one() == "pending"
    assert result == {"evaluated": 0, "skipped_missing_price": 1}
