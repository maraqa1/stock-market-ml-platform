from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, insert, select

from portal.app import create_app
from stockml.autopilot.rotate import (
    RotationConfig,
    confirm_rotation,
    evaluate_rotations,
    expire_old_rotations,
    override_rotation,
    record_rotation,
)
from stockml.db.schema import create_all, rotation_recommendation_log


NOW = datetime(2026, 5, 11, 15, 0, tzinfo=timezone.utc)


def engine():
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    return db


def allow_gate(**kwargs):
    return type("Verdict", (), {"allow": True, "tripped": []})()


def block_gate(**kwargs):
    return type("Verdict", (), {"allow": False, "tripped": ["daily.realized_plus_unrealized_loss_usd"]})()


def promoted(symbol="ADMA", score=0.75, bias="long"):
    return {"symbol": symbol, "promotion_score": score, "nightly_bias": bias}


def position(symbol="FRMI", score=0.60, side="long", opened_at=None, pnl=0.0, reason="signal_stale"):
    return {
        "symbol": symbol,
        "position_id": f"paper:{symbol}",
        "last_promotion_score": score,
        "side": side,
        "opened_at": opened_at or (NOW - timedelta(hours=2)).isoformat(),
        "unrealized_plpc": pnl,
        "decision_reason": reason,
    }


def test_rotation_recommendation_requires_score_delta():
    rotations = evaluate_rotations(
        [promoted(score=0.69)],
        [position(score=0.60)],
        config=RotationConfig(min_score_delta=0.10),
        now=NOW,
        kill_switch_gate=allow_gate,
    )

    assert rotations == []


def test_rotation_recommendation_selects_weaker_same_side_position():
    rotations = evaluate_rotations(
        [promoted(score=0.75)],
        [position("FRMI", score=0.60), position("FWRD", score=0.70)],
        config=RotationConfig(min_score_delta=0.10),
        now=NOW,
        kill_switch_gate=allow_gate,
    )

    assert len(rotations) == 1
    assert rotations[0].replace_symbol == "FRMI"
    assert rotations[0].with_symbol == "ADMA"
    assert round(rotations[0].score_delta, 2) == 0.15
    assert rotations[0].reason.value == "HELD_SIGNAL_STALE"


def test_rotation_recommendation_respects_min_hold_minutes():
    rotations = evaluate_rotations(
        [promoted(score=0.75)],
        [position(score=0.40, opened_at=(NOW - timedelta(minutes=10)).isoformat())],
        config=RotationConfig(min_score_delta=0.10, min_hold_minutes=60),
        now=NOW,
        kill_switch_gate=allow_gate,
    )

    assert rotations == []


def test_rotation_recommendation_skips_candidate_already_held_and_kill_switch():
    assert evaluate_rotations([promoted("FRMI", 0.9)], [position("FRMI", 0.1)], now=NOW, kill_switch_gate=allow_gate) == []
    assert evaluate_rotations([promoted("ADMA", 0.9)], [position("FRMI", 0.1)], now=NOW, kill_switch_gate=block_gate) == []


def test_rotation_record_override_and_expiry():
    db = engine()
    rotation = evaluate_rotations([promoted(score=0.75)], [position(score=0.60)], now=NOW, kill_switch_gate=allow_gate)[0]
    rotation_id = record_rotation(rotation, engine=db, now=NOW - timedelta(minutes=40))

    assert rotation_id is not None
    assert expire_old_rotations(engine=db, now=NOW) == 1
    with db.connect() as conn:
        verdict = conn.execute(select(rotation_recommendation_log.c.verdict)).scalar()
    assert verdict == "expired"

    rotation_id = record_rotation(rotation, engine=db, now=NOW)
    assert override_rotation(rotation_id, engine=db, now=NOW)
    with db.connect() as conn:
        verdict = conn.execute(select(rotation_recommendation_log.c.verdict).where(rotation_recommendation_log.c.id == rotation_id)).scalar()
    assert verdict == "overridden"


def test_confirm_rotation_requires_explicit_paper_open_path_and_can_confirm_with_injected_paths():
    db = engine()
    rotation = evaluate_rotations([promoted(score=0.75)], [position(score=0.60)], now=NOW, kill_switch_gate=allow_gate)[0]
    blocked_id = record_rotation(rotation, engine=db, now=NOW)

    blocked = confirm_rotation(blocked_id, engine=db, now=NOW)

    assert blocked["status"] == "blocked"
    assert blocked["message"] == "paper_open_path_not_supplied"

    confirmed_id = record_rotation(rotation, engine=db, now=NOW)
    result = confirm_rotation(
        confirmed_id,
        engine=db,
        now=NOW,
        close_func=lambda symbol: {"status": "submitted", "symbol": symbol},
        open_func=lambda symbol: {"status": "submitted", "symbol": symbol},
    )

    assert result["status"] == "confirmed"


def test_action_queue_surfaces_rotation_recommendations(monkeypatch, tmp_path):
    def fake_rotation_items(offset):
        return [
            {
                "event_id": "rotation-7",
                "symbol": "FRMI -> ADMA",
                "side": "long",
                "unrealized_pl": "",
                "unrealized_plpc": 0.15,
                "signal_age_minutes": "",
                "decision": "rotate",
                "recommended_action": "apply_rotation",
                "decision_reason": "HIGHER_PROMOTION_SCORE",
                "replacement_symbol": "ADMA",
                "position_id": "paper:FRMI",
                "operator_call": "warning",
                "operator_call_label": "Apply rotation",
                "operator_call_reason": "Paper Assist proposes FRMI -> ADMA. Operator confirmation required.",
                "operator_apply_enabled": True,
                "generated_at": NOW,
            }
        ]

    monkeypatch.setattr("portal.services.trading_api_service._rotation_queue_items", fake_rotation_items)
    app = create_app(tmp_path)
    app.config.update(TESTING=True)
    response = app.test_client().get("/trading")

    assert response.status_code == 200
    assert b"FRMI -&gt; ADMA" in response.data
    assert b"Apply rotation" in response.data


def test_rotation_apply_route_blocks_without_open_path(monkeypatch, tmp_path):
    app = create_app(tmp_path)
    app.config.update(TESTING=True)
    monkeypatch.setattr("portal.app.confirm_rotation", lambda rotation_id: {"status": "blocked", "message": "paper_open_path_not_supplied"})

    response = app.test_client().post("/trading/queue/rotation-7/apply", json={"symbol": "FRMI -> ADMA", "decision": "rotate"})

    assert response.status_code == 200
    assert response.get_json()["status"] == "blocked"
