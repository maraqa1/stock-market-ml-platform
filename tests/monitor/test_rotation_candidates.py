from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from portal.app import create_app
from stockml.agents.position_decision_engine import build_position_decisions, find_replacement


NOW = datetime(2026, 5, 11, 15, 30, tzinfo=timezone.utc)


def _candidate(symbol: str, rank: int, action: str = "Long", score: float = 0.75) -> dict:
    return {
        "symbol": symbol,
        "trade_action": action,
        "side": "buy" if action == "Long" else "sell",
        "candidate_rank": rank,
        "score": score,
        "confidence_score": score,
        "trade_quality_status": "reduced",
        "order_eligible": True,
        "suggested_quantity": 10,
    }


def test_find_replacement_skips_held_top_ranked_candidate():
    shortlist = pd.DataFrame(
        [
            _candidate("FWRD", 1, score=0.82),
            _candidate("ADMA", 2, score=0.78),
            _candidate("NEXT", 3, score=0.77),
        ]
    )
    open_positions = pd.DataFrame([{"symbol": "FRMI", "status": "open"}, {"symbol": "FWRD", "status": "open"}])

    replacement = find_replacement(
        "FRMI",
        shortlist,
        open_positions,
        position_bias="Long",
        current_rank=10,
        current_score=0.70,
        min_rank_improvement=3,
        min_score_delta=0.02,
    )

    assert replacement is not None
    assert replacement["symbol"] == "ADMA"


def test_find_replacement_returns_none_when_every_eligible_candidate_is_held():
    shortlist = pd.DataFrame([_candidate("FWRD", 1), _candidate("ADMA", 2)])
    open_positions = pd.DataFrame(
        [{"symbol": "FRMI", "status": "open"}, {"symbol": "FWRD", "status": "open"}, {"symbol": "ADMA", "status": "open"}]
    )

    replacement = find_replacement("FRMI", shortlist, open_positions, position_bias="Long", current_rank=10, current_score=0.70)

    assert replacement is None


def test_engine_emits_watch_when_rank_rotation_has_no_eligible_replacement():
    positions = pd.DataFrame([{"symbol": "FRMI", "qty": 94, "current_price": 5.42, "side": "long", "status": "open"}])
    plan = pd.DataFrame([{"symbol": "FRMI", "trade_action": "Long", "signal_generated_at": "2026-05-11T15:29:00Z"}])
    candidate_pool = pd.DataFrame([_candidate("FRMI", 10, score=0.70), _candidate("FWRD", 2, score=0.82)])
    open_positions = pd.DataFrame(
        [
            {"symbol": "FRMI", "qty": 94, "current_price": 5.42, "side": "long", "status": "open"},
            {"symbol": "FWRD", "qty": 50, "current_price": 10.17, "side": "long", "status": "open"},
        ]
    )

    decisions = build_position_decisions(open_positions, plan, candidate_pool=candidate_pool, now=NOW)
    row = decisions[decisions["symbol"] == "FRMI"].iloc[0]

    assert row["decision"] == "watch"
    assert row["replacement_symbol"] == ""
    assert "no_eligible_replacement_available" in row["decision_reason"]
    assert "replacement_rank_improvement" not in row["decision_reason"]


def test_rank_improvement_threshold_rejects_one_rank_better_candidate():
    shortlist = pd.DataFrame([_candidate("ADMA", 9, score=0.80)])
    replacement = find_replacement(
        "FRMI",
        shortlist,
        pd.DataFrame([{"symbol": "FRMI", "status": "open"}]),
        position_bias="Long",
        current_rank=10,
        current_score=0.70,
        min_rank_improvement=3,
        min_score_delta=0.02,
    )

    assert replacement is None


def test_score_delta_threshold_rejects_noise_rotation():
    shortlist = pd.DataFrame([_candidate("ADMA", 2, score=0.71)])
    replacement = find_replacement(
        "FRMI",
        shortlist,
        pd.DataFrame([{"symbol": "FRMI", "status": "open"}]),
        position_bias="Long",
        current_rank=10,
        current_score=0.70,
        min_rank_improvement=3,
        min_score_delta=0.02,
    )

    assert replacement is None


def test_cross_side_replacement_is_never_returned():
    shortlist = pd.DataFrame([_candidate("SRTY", 1, action="Short", score=0.90)])
    replacement = find_replacement(
        "FRMI",
        shortlist,
        pd.DataFrame([{"symbol": "FRMI", "status": "open"}]),
        position_bias="Long",
        current_rank=10,
        current_score=0.70,
    )

    assert replacement is None


def test_action_queue_frmi_fixture_renders_watch_not_replace(tmp_path: Path):
    positions = pd.DataFrame(
        [
            {"symbol": "FRMI", "qty": 94, "current_price": 5.42, "side": "long", "status": "open"},
            {"symbol": "FWRD", "qty": 50, "current_price": 10.17, "side": "long", "status": "open"},
        ]
    )
    plan = pd.DataFrame([{"symbol": "FRMI", "trade_action": "Long", "signal_generated_at": "2026-05-11T15:29:00Z"}])
    candidate_pool = pd.DataFrame([_candidate("FRMI", 10, score=0.70), _candidate("FWRD", 2, score=0.82)])
    decisions = build_position_decisions(positions, plan, candidate_pool=candidate_pool, now=NOW)
    path = tmp_path / "data" / "trading" / "agent_decisions" / "position_decisions_1.csv"
    path.parent.mkdir(parents=True)
    decisions.to_csv(path, index=False)

    app = create_app(tmp_path)
    app.config.update(TESTING=True)
    response = app.test_client().get("/trading")

    assert response.status_code == 200
    assert b"FRMI" in response.data
    assert b"Watch only" in response.data
    assert b"Review concentration" not in response.data


def test_replacement_selection_static_guard_is_visible():
    source = Path("src/stockml/agents/position_decision_engine.py").read_text(encoding="utf-8")

    assert "Rotation candidates must exclude names already held in open positions" in source
    assert "_best_replacement" not in source
