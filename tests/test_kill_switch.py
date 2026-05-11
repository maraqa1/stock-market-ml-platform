from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.exc import IntegrityError

from stockml.db.schema import create_all, kill_switch_events
from stockml.intraday import kill_switch


NOW = datetime(2026, 5, 11, 15, 0, tzinfo=timezone.utc)


def engine():
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    return db


def test_kill_switch_config_loads_versioned_thresholds():
    cfg = kill_switch.load_config()

    assert cfg.version == 1
    assert cfg.account_size_usd == 1000
    assert cfg.daily["realized_plus_unrealized_loss_usd"] == -30
    assert cfg.friction["require_manual_resume_after_trip"] is True


def test_kill_switch_events_table_registered_and_constrained():
    db = engine()
    with db.begin() as conn:
        conn.execute(
            insert(kill_switch_events).values(
                switch_name="daily.test",
                event_type="tripped",
                occurred_at=NOW,
                payload={"current": -31},
            )
        )
        with pytest.raises(IntegrityError):
            conn.execute(
                insert(kill_switch_events).values(
                    switch_name="daily.test",
                    event_type="bad",
                    occurred_at=NOW,
                    payload={},
                )
            )


def test_daily_loss_trip_blocks_and_writes_one_event():
    db = engine()
    verdict = kill_switch.gate(
        metrics={"daily_realized_plus_unrealized_loss_usd": -31},
        engine=db,
        now=NOW,
    )

    assert verdict.allow is False
    assert verdict.tripped == ["daily.realized_plus_unrealized_loss_usd"]
    assert verdict.requires_manual_resume is True
    with db.connect() as conn:
        rows = conn.execute(select(kill_switch_events)).all()
    assert len(rows) == 1


def test_repeated_gate_does_not_duplicate_active_trip_event():
    db = engine()
    metrics = {"daily_realized_plus_unrealized_loss_usd": -31}
    kill_switch.gate(metrics=metrics, engine=db, now=NOW)
    kill_switch.gate(metrics=metrics, engine=db, now=NOW)

    with db.connect() as conn:
        rows = conn.execute(select(kill_switch_events)).all()
    assert len(rows) == 1


def test_weekly_trip_persists_until_manual_resume():
    db = engine()
    kill_switch.gate(metrics={"weekly_cumulative_loss_usd": -71}, engine=db, now=NOW)

    later = kill_switch.gate(metrics={}, engine=db, now=NOW)
    assert later.allow is False
    assert later.tripped == ["weekly.cumulative_loss_usd"]

    kill_switch.resume("weekly.cumulative_loss_usd", "operator@test", "reviewed weekly loss", engine=db, now=NOW)
    resumed = kill_switch.gate(metrics={}, engine=db, now=NOW)
    assert resumed.allow is True
    with db.connect() as conn:
        events = conn.execute(select(kill_switch_events.c.event_type, kill_switch_events.c.operator_id, kill_switch_events.c.notes)).all()
    assert events[-1] == ("resumed", "operator@test", "reviewed weekly loss")


def test_total_switch_persists_indefinitely_until_resume():
    db = engine()
    kill_switch.gate(metrics={"total_equity_usd": 849}, engine=db, now=NOW)

    verdict = kill_switch.gate(metrics={}, engine=db, now=NOW)
    assert verdict.allow is False
    assert verdict.tripped == ["total.equity_floor_usd"]


def test_unauthorized_order_shape_only_applies_to_submit_order_action():
    db = engine()
    evaluate = kill_switch.gate(action="evaluate", metrics={"total_unauthorized_order_shape": True}, engine=db, now=NOW)
    assert evaluate.allow is True

    submit = kill_switch.gate(action="submit_order", metrics={"total_unauthorized_order_shape": True}, engine=db, now=NOW)
    assert submit.allow is False
    assert submit.tripped == ["total.unauthorized_order_shape"]


def test_resume_requires_operator_and_notes():
    with pytest.raises(ValueError):
        kill_switch.resume("daily.realized_plus_unrealized_loss_usd", "", "", engine=engine())


def test_state_lists_every_configured_switch_with_active_status():
    db = engine()
    kill_switch.trip("daily.consecutive_losing_trades", {"current": 3, "threshold": 3}, engine=db, now=NOW)

    payload = kill_switch.state(engine=db)
    names = {row["name"] for row in payload["switches"]}

    assert "daily.consecutive_losing_trades" in names
    assert "total.equity_floor_usd" in names
    assert payload["active"] == ["daily.consecutive_losing_trades"]


def test_intraday_log_wrapper_writes_jsonl(tmp_path: Path):
    path = kill_switch.intraday_log("pytest_event", {"symbol": "TSLA"}, root=tmp_path, now=NOW)

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "pytest_event" in text
    assert "TSLA" in text

