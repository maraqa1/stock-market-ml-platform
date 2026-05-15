from __future__ import annotations

from pathlib import Path

from stockml.autopilot.candidate_arbitration import arbitrate_candidates, hard_block_reason


def _candidate(symbol: str, score: float = 0.0, source_flag: str = "") -> dict:
    details = {"is_first_15_min": False, "is_last_30_min": False}
    if source_flag:
        details[source_flag] = True
    return {
        "symbol": symbol,
        "promotion_score": score,
        "nightly_bias": "long",
        "is_held": False,
        "details": details,
    }


def test_arbitration_prefers_best_forecast_over_weaker_near_miss():
    forecast = _candidate("BEST", 1, "per_symbol_forecast_fallback")
    forecast["details"].update(
        {
            "expected_profitability_score": 120,
            "confirmation_score": 92,
            "expected_move_bps": 160,
        }
    )
    near_miss = _candidate("CLOSE", 0.005, "near_miss_fallback")
    near_miss["details"].update({"distance_pct": 0.001, "severity": "near_miss"})

    ranked = arbitrate_candidates(
        [
            ("per_symbol_forecast", [forecast]),
            ("near_miss", [near_miss]),
        ]
    )

    assert [row["symbol"] for row in ranked] == ["BEST", "CLOSE"]
    assert ranked[0]["details"]["arbitration_status"] == "selected"
    assert ranked[0]["details"]["arbitration_components"]["profitability"] == 120


def test_arbitration_keeps_strongest_source_per_symbol():
    weak_forecast = _candidate("SAME", 1, "per_symbol_forecast_fallback")
    weak_forecast["details"].update({"expected_profitability_score": 1, "confirmation_score": 80})
    near_miss = _candidate("SAME", 0.005, "near_miss_fallback")
    near_miss["details"].update({"distance_pct": 0.001, "severity": "near_miss"})

    ranked = arbitrate_candidates(
        [
            ("per_symbol_forecast", [weak_forecast]),
            ("near_miss", [near_miss]),
        ]
    )

    assert len(ranked) == 1
    assert ranked[0]["details"]["candidate_source"] == "near_miss"


def test_arbitration_filters_hard_blocks_and_held_symbols():
    price_fail = _candidate("LOWP", 1, "near_miss_fallback")
    price_fail["details"].update({"failed_gate": "price_below_minimum", "severity": "near_miss"})
    held = _candidate("HELD", 100, "per_symbol_forecast_fallback")
    selected = _candidate("OPEN", 100, "per_symbol_forecast_fallback")
    selected["details"].update({"expected_profitability_score": 100, "confirmation_score": 90})

    ranked = arbitrate_candidates(
        [
            ("per_symbol_forecast", [held, selected]),
            ("near_miss", [price_fail]),
        ],
        held_symbols={"HELD"},
    )

    assert [row["symbol"] for row in ranked] == ["OPEN"]
    assert hard_block_reason(price_fail) == "price_below_minimum"


def test_candidate_arbitration_has_no_broker_imports():
    path = Path("src/stockml/autopilot/candidate_arbitration.py")
    text = path.read_text(encoding="utf-8")

    assert "alpaca" not in text.lower()
    assert "submit_order" not in text.lower()
    assert "broker" not in text.lower()
