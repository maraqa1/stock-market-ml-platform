from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select

from stockml.db.schema import create_all, intraday_decisions, shadow_would_trades
from stockml.intraday.features import IntradayFeatures, NightlySignal
from stockml.intraday.gates import GATE_VERSION, GateDecision, next_five_minute_boundary
from stockml.intraday.decisions import record_decision
from stockml.intraday.shadow import add_trading_days, estimated_entry_slippage_bps, mark_superseded_for_position


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
