from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine

from portal.services.trading_api_service import _rotation_queue_items
from stockml.autopilot.rotate import RotationConfig, evaluate_rotations, record_rotation
from stockml.autopilot.rotation_selector import OVERRIDE_REASON_TEXT
from stockml.db.schema import create_all


NOW = datetime(2026, 6, 8, 14, 30, tzinfo=timezone.utc)


def _engine():
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    return db


def _allow_gate(**kwargs):
    return type("Verdict", (), {"allow": True, "tripped": []})()


def _candidate(symbol: str, score: float, side: str = "long") -> dict:
    return {"symbol": symbol, "promotion_score": score, "nightly_bias": side}


def _held(symbol: str = "BNY", score: float = 0.50, verdict: str = "healthy_hold", side: str = "long") -> dict:
    return {
        "symbol": symbol,
        "position_id": f"paper:{symbol}",
        "last_promotion_score": score,
        "side": side,
        "opened_at": (NOW - timedelta(hours=3)).isoformat(),
        "unrealized_plpc": -0.005,
        "position_health_status": verdict,
        "decision_reason": "latest_signal_fresh",
    }


def test_rotation_blocked_on_healthy_hold():
    rotations = evaluate_rotations(
        [_candidate("RXO", 0.65)],
        [_held("BNY", 0.50, "healthy_hold")],
        config=RotationConfig(min_score_delta=0.10, monitor_override_score_delta=0.20),
        now=NOW,
        kill_switch_gate=_allow_gate,
    )

    assert rotations == []


def test_rotation_allowed_on_watch_loss():
    rotations = evaluate_rotations(
        [_candidate("RXO", 0.61)],
        [_held("BNY", 0.50, "watch_loss")],
        config=RotationConfig(min_score_delta=0.10, monitor_override_score_delta=0.20),
        now=NOW,
        kill_switch_gate=_allow_gate,
    )

    assert len(rotations) == 1
    assert rotations[0].replace_symbol == "BNY"
    assert rotations[0].details["monitor_verdict"] == "watch_loss"
    assert rotations[0].details["monitor_override"] is False


def test_override_threshold_works_on_healthy_hold():
    rotations = evaluate_rotations(
        [_candidate("RXO", 0.72)],
        [_held("BNY", 0.50, "healthy_hold")],
        config=RotationConfig(min_score_delta=0.10, monitor_override_score_delta=0.20),
        now=NOW,
        kill_switch_gate=_allow_gate,
    )

    assert len(rotations) == 1
    assert rotations[0].details["monitor_verdict"] == "healthy_hold"
    assert rotations[0].details["monitor_override"] is True
    assert rotations[0].details["reason_text"] == OVERRIDE_REASON_TEXT


def test_motivating_screenshot_zero_rotations():
    rotations = evaluate_rotations(
        [_candidate("RXO", 0.55), _candidate("GENI", 0.58), _candidate("CNC", 0.60), _candidate("PGNY", 0.62)],
        [_held("BNY", 0.50, "watch_only")],
        config=RotationConfig(min_score_delta=0.10, monitor_override_score_delta=0.20),
        now=NOW,
        kill_switch_gate=_allow_gate,
    )

    assert rotations == []


def test_reason_text_reflects_override(monkeypatch):
    db = _engine()
    rotation = evaluate_rotations(
        [_candidate("RXO", 0.72)],
        [_held("BNY", 0.50, "healthy_hold")],
        config=RotationConfig(min_score_delta=0.10, monitor_override_score_delta=0.20),
        now=NOW,
        kill_switch_gate=_allow_gate,
    )[0]
    record_rotation(rotation, engine=db, now=NOW)
    monkeypatch.setattr("portal.services.trading_api_service._engine", lambda: db)

    items = _rotation_queue_items(0, held_symbols={"BNY"}, open_order_symbols=set())

    assert len(items) == 1
    assert items[0]["decision_reason"] == OVERRIDE_REASON_TEXT
