from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, insert, select

from portal.app import create_app
from scripts import run_rotation_recommendations
from stockml.autopilot.rotate import (
    RotationConfig,
    apply_auto_rotations,
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


def _write_edge_replacement_artifacts(root: Path, replace_symbol: str = "AGL", with_symbol: str = "SNOW") -> None:
    decisions_dir = root / "data" / "trading" / "agent_decisions"
    candidates_dir = root / "data" / "portal_outputs"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    candidates_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "symbol": replace_symbol,
                "side": "long",
                "decision": "replace",
                "recommended_action": "review_edge_replacement",
                "decision_reason": "replacement_edge_improvement",
                "replacement_symbol": with_symbol,
                "replacement_rank": 175,
                "replacement_score": 0.055,
                "replacement_edge_bps": 2225.87,
                "replacement_quality_status": "approved",
                "replacement_risk_tier": "high_quality",
                "replacement_selection_method": "edge",
            }
        ]
    ).to_csv(decisions_dir / "position_decisions_20260610_160000.csv", index=False)
    pd.DataFrame(
        [
            {
                "symbol": with_symbol,
                "trade_action": "Long",
                "side": "buy",
                "candidate_rank": 175,
                "trade_quality_status": "approved",
                "risk_tier": "high_quality",
                "order_eligible": True,
                "suggested_quantity": 10,
                "expected_trade_return": 0.222587,
                "risk_adjusted_score": 0.055647,
                "current_price": 239.66,
            }
        ]
    ).to_csv(candidates_dir / "08_alpaca_paper_candidate_pool_20260610_160000.csv", index=False)


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


def test_auto_rotations_confirm_and_size_with_injected_paths(monkeypatch):
    db = engine()
    rotation = evaluate_rotations([promoted(symbol="KNTK", score=0.75)], [position(symbol="BPOP", score=0.20)], now=NOW, kill_switch_gate=allow_gate)[0]
    rotation_id = record_rotation(rotation, engine=db, now=NOW)
    monkeypatch.setattr(
        "stockml.autopilot.rotate.load_rotation_config",
        lambda: RotationConfig(require_operator_confirm=False, max_rotations_per_day=3),
    )
    monkeypatch.setattr(
        "stockml.autopilot.rotate.latest_strong_candidates",
        lambda **kwargs: [{"symbol": "KNTK", "promotion_score": 0.75, "nightly_bias": "long", "current_price": 55, "details": {}}],
    )
    opened = []
    closed = []

    result = apply_auto_rotations(
        [position(symbol="BPOP", score=0.20)],
        engine=db,
        now=NOW,
        close_func=lambda symbol: closed.append(symbol) or {"status": "submitted", "symbol": symbol},
        open_func=lambda candidates, positions: opened.append((candidates, positions)) or {"autopilot_open_submitted": 1, "autopilot_open_notes": "KNTK:opened:order-1"},
    )

    assert result["auto_rotations_confirmed"] == 1
    assert opened[0][0][0]["details"]["rotation_replacement"] is True
    assert opened[0][1] == []
    assert closed == ["BPOP"]
    with db.connect() as conn:
        verdict = conn.execute(select(rotation_recommendation_log.c.verdict).where(rotation_recommendation_log.c.id == rotation_id)).scalar()
    assert verdict == "confirmed"


def test_auto_edge_replacement_dry_run_uses_decision_and_candidate_pool(monkeypatch, tmp_path):
    db = engine()
    _write_edge_replacement_artifacts(tmp_path)
    monkeypatch.setattr(
        "stockml.autopilot.rotate.load_rotation_config",
        lambda: RotationConfig(
            require_operator_confirm=False,
            edge_replacement_auto_enabled=True,
            edge_replacement_auto_dry_run=True,
            max_rotations_per_day=3,
        ),
    )
    monkeypatch.setattr("stockml.autopilot.rotate.latest_strong_candidates", lambda **kwargs: [])

    result = apply_auto_rotations(
        [position(symbol="AGL", score=0.20, side="long")],
        engine=db,
        now=NOW,
        root=tmp_path,
        close_func=lambda symbol: (_ for _ in ()).throw(AssertionError("dry run must not close")),
        open_func=lambda candidates, positions: (_ for _ in ()).throw(AssertionError("dry run must not open")),
    )

    assert result["auto_edge_replacements_attempted"] == 1
    assert result["auto_edge_replacements_dry_run"] == 1
    assert result["auto_edge_replacements_confirmed"] == 0
    assert "AGL->SNOW:edge_dry_run" in result["auto_rotation_notes"]


def test_auto_edge_replacement_confirms_with_injected_paths(monkeypatch, tmp_path):
    db = engine()
    _write_edge_replacement_artifacts(tmp_path)
    monkeypatch.setattr(
        "stockml.autopilot.rotate.load_rotation_config",
        lambda: RotationConfig(
            require_operator_confirm=False,
            edge_replacement_auto_enabled=True,
            edge_replacement_auto_dry_run=False,
            max_rotations_per_day=3,
        ),
    )
    monkeypatch.setattr("stockml.autopilot.rotate.latest_strong_candidates", lambda **kwargs: [])
    opened = []
    closed = []

    result = apply_auto_rotations(
        [position(symbol="AGL", score=0.20, side="long")],
        engine=db,
        now=NOW,
        root=tmp_path,
        close_func=lambda symbol: closed.append(symbol) or {"status": "submitted", "symbol": symbol},
        open_func=lambda candidates, positions: opened.append((candidates, positions)) or {"autopilot_open_submitted": 1, "autopilot_open_notes": "SNOW:opened:order-1"},
    )

    assert result["auto_rotations_confirmed"] == 1
    assert result["auto_edge_replacements_confirmed"] == 1
    assert opened[0][0][0]["symbol"] == "SNOW"
    assert opened[0][0][0]["details"]["edge_replacement"] is True
    assert opened[0][0][0]["details"]["replace_symbol"] == "AGL"
    assert opened[0][1] == []
    assert closed == ["AGL"]


def test_action_queue_surfaces_rotation_recommendations(monkeypatch, tmp_path):
    def fake_rotation_items(offset, *, held_symbols=None, open_order_symbols=None):
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


def test_rotation_runner_treats_empty_positions_file_as_flat_account(monkeypatch, tmp_path):
    positions_file = tmp_path / "08_alpaca_paper_positions_1.csv"
    positions_file.write_text("")
    monkeypatch.setattr(run_rotation_recommendations, "PORTAL_OUTPUTS_DIR", Path(tmp_path))

    assert run_rotation_recommendations._latest_positions() == []
