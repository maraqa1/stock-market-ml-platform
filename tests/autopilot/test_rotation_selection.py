from __future__ import annotations

from datetime import datetime, timedelta, timezone

from stockml.autopilot.rotate import RotationConfig, evaluate_rotations
from stockml.autopilot.rotation_selector import find_best_replacement, select_rotation_replacements


NOW = datetime(2026, 6, 8, 14, 30, tzinfo=timezone.utc)


def _held(symbol: str = "BNY", score: float = 0.50, side: str = "long") -> dict:
    return {
        "symbol": symbol,
        "position_id": f"paper:{symbol}",
        "last_promotion_score": score,
        "side": side,
        "opened_at": (NOW - timedelta(hours=3)).isoformat(),
        "unrealized_plpc": -0.005,
        "decision_reason": "signal_stale",
    }


def _candidate(symbol: str, score: float, side: str = "long") -> dict:
    return {"symbol": symbol, "promotion_score": score, "nightly_bias": side}


def _allow_gate(**kwargs):
    return type("Verdict", (), {"allow": True, "tripped": []})()


def test_one_proposal_per_held_position():
    rotations = evaluate_rotations(
        [_candidate("RXO", 0.61), _candidate("GENI", 0.62), _candidate("CNC", 0.63), _candidate("PGNY", 0.64)],
        [_held("BNY", 0.50)],
        config=RotationConfig(min_score_delta=0.10, max_rotations_per_day=10),
        now=NOW,
        kill_switch_gate=_allow_gate,
    )

    assert len(rotations) == 1
    assert rotations[0].replace_symbol == "BNY"


def test_highest_score_candidate_selected():
    selection = find_best_replacement(
        _held("BNY", 0.50),
        [_candidate("RXO", 0.61), _candidate("GENI", 0.62), _candidate("CNC", 0.63), _candidate("PGNY", 0.64)],
        held_symbols={"BNY"},
        min_score_delta=0.10,
    )

    assert selection is not None
    assert selection.candidate["symbol"] == "PGNY"
    assert round(selection.score_delta, 2) == 0.14


def test_no_eligible_returns_none():
    selection = find_best_replacement(_held("BNY", 0.60), [_candidate("RXO", 0.65)], held_symbols={"BNY"}, min_score_delta=0.10)

    assert selection is None


def test_held_symbol_excluded():
    selection = find_best_replacement(
        _held("BNY", 0.50),
        [_candidate("BNY", 0.95), _candidate("RXO", 0.61)],
        held_symbols={"BNY"},
        min_score_delta=0.10,
    )

    assert selection is not None
    assert selection.candidate["symbol"] == "RXO"


def test_tie_break_deterministic():
    selection = find_best_replacement(
        _held("BNY", 0.50),
        [_candidate("ZZZ", 0.70), _candidate("AAA", 0.70)],
        held_symbols={"BNY"},
        min_score_delta=0.10,
    )

    assert selection is not None
    assert selection.candidate["symbol"] == "AAA"


def test_selector_returns_at_most_one_candidate_for_each_held_position():
    selections = select_rotation_replacements(
        [_held("BNY", 0.50), _held("FRMI", 0.55)],
        [_candidate("RXO", 0.66), _candidate("PGNY", 0.70), _candidate("GENI", 0.68)],
        open_positions=[_held("BNY", 0.50), _held("FRMI", 0.55)],
        min_score_delta=0.10,
        now=NOW,
    )

    assert len(selections) == 2
    assert [selection.held["symbol"] for selection in selections] == ["BNY", "FRMI"]
    assert [selection.candidate["symbol"] for selection in selections] == ["PGNY", "PGNY"]
