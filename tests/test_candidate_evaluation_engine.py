from __future__ import annotations

import pandas as pd

from stockml.agents.candidate_evaluation_engine import evaluate_candidates


def quote(symbol: str) -> dict:
    prices = {"AAA": 10.0, "BBB": 20.0, "CCC": 30.0, "HELD": 40.0}
    price = prices[symbol]
    return {"bid": price - 0.01, "ask": price + 0.01, "last_price": price}


def candidate(symbol: str, rank: int, score: float = 0.8, action: str = "Long") -> dict:
    return {
        "symbol": symbol,
        "trade_action": action,
        "candidate_rank": rank,
        "score": score,
        "trade_quality_status": "reduced",
        "order_eligible": True,
        "suggested_quantity": 10,
    }


def test_candidate_evaluation_marks_open_candidate_when_slot_available():
    candidates = pd.DataFrame([candidate("AAA", 1)])
    positions = pd.DataFrame([])

    evaluated = evaluate_candidates(candidates, positions, quote_loader=quote, max_open_positions=5)

    row = evaluated.iloc[0]
    assert row["decision"] == "open_candidate"
    assert row["current_price"] == 10.0
    assert row["spread_bps"] > 0


def test_candidate_evaluation_marks_held_names_as_watch():
    candidates = pd.DataFrame([candidate("HELD", 1)])
    positions = pd.DataFrame([{"symbol": "HELD", "status": "open"}])

    evaluated = evaluate_candidates(candidates, positions, quote_loader=quote, max_open_positions=5)

    row = evaluated.iloc[0]
    assert row["decision"] == "watch"
    assert row["decision_reason"] == "already_held"


def test_candidate_evaluation_flags_better_candidate_when_portfolio_full():
    candidates = pd.DataFrame([candidate("AAA", 1, 0.86), candidate("HELD", 10, 0.70)])
    positions = pd.DataFrame([{"symbol": "HELD", "status": "open"}])

    evaluated = evaluate_candidates(candidates, positions, quote_loader=quote, max_open_positions=1)

    row = evaluated[evaluated["symbol"] == "AAA"].iloc[0]
    assert row["decision"] == "replace_candidate"
    assert row["held_symbol_to_compare"] == "HELD"
    assert row["rank_delta_vs_worst_held"] == 9


def test_candidate_evaluation_skips_rejected_or_unpriced_candidate():
    candidates = pd.DataFrame(
        [
            {**candidate("AAA", 1), "trade_quality_status": "rejected"},
            candidate("ZZZ", 2),
        ]
    )

    evaluated = evaluate_candidates(candidates, pd.DataFrame([]), quote_loader=lambda symbol: None)

    assert set(evaluated["decision"]) == {"skip"}


def test_candidate_evaluation_skips_wide_spread_candidates():
    candidates = pd.DataFrame([candidate("AAA", 1)])

    evaluated = evaluate_candidates(
        candidates,
        pd.DataFrame([]),
        quote_loader=lambda symbol: {"bid": 9.0, "ask": 11.0, "last_price": 10.0},
        max_spread_bps=50,
    )

    row = evaluated.iloc[0]
    assert row["decision"] == "skip"
    assert row["decision_reason"] == "wide_spread"


def test_candidate_evaluation_requires_reliable_spread_for_action_queue():
    candidates = pd.DataFrame([candidate("AAA", 1)])

    evaluated = evaluate_candidates(
        candidates,
        pd.DataFrame([]),
        quote_loader=lambda symbol: {"last_price": 10.0},
        max_spread_bps=50,
    )

    row = evaluated.iloc[0]
    assert row["decision"] == "skip"
    assert row["decision_reason"] == "spread_unavailable"
