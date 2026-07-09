from __future__ import annotations

import pandas as pd

from stockml.candidates.execution_ranker import build_execution_ranked_candidates


def test_side_probability_is_not_promoted_to_calibrated_probability_win():
    ranked = build_execution_ranked_candidates(
        pd.DataFrame(
            [
                {
                    "symbol": "AAA",
                    "side": "buy",
                    "source_trade_action": "Long",
                    "trade_action": "Long",
                    "directional_action": "Long",
                    "ticker_direction_bias": "trust_long",
                    "trade_quality_status": "approved",
                    "order_eligible": True,
                    "approved_notional": 100,
                    "suggested_quantity": 1,
                    "expected_return_quality": "calibrated",
                    "calibration_quality": "usable",
                    "validated_expected_return_bps": 40,
                    "validated_hit_rate": 0.55,
                    "validated_profit_factor": 1.4,
                    "side_probability": 0.99,
                }
            ]
        )
    )

    row = ranked.iloc[0]
    assert row["raw_side_score"] == 0.99
    assert row["calibrated_probability_win"] is None
    assert row["probability_calibration_status"] == "uncalibrated"
    assert row["status"] == "executable"
    assert row["final_execution_side"] == "LONG"


def test_repeated_same_side_validation_metrics_are_not_ticker_scope():
    ranked = build_execution_ranked_candidates(
        pd.DataFrame(
            [
                {
                    "symbol": "AAA",
                    "side": "buy",
                    "source_trade_action": "Long",
                    "trade_action": "Long",
                    "directional_action": "Long",
                    "ticker_direction_bias": "trust_long",
                    "trade_quality_status": "approved",
                    "order_eligible": True,
                    "approved_notional": 100,
                    "suggested_quantity": 1,
                    "expected_return_quality": "calibrated",
                    "calibration_quality": "usable",
                    "validated_expected_return_bps": 41.8,
                    "validated_hit_rate": 0.55,
                    "validated_profit_factor": 1.4,
                },
                {
                    "symbol": "BBB",
                    "side": "buy",
                    "source_trade_action": "Long",
                    "trade_action": "Long",
                    "directional_action": "Long",
                    "ticker_direction_bias": "trust_long",
                    "trade_quality_status": "approved",
                    "order_eligible": True,
                    "approved_notional": 100,
                    "suggested_quantity": 1,
                    "expected_return_quality": "calibrated",
                    "calibration_quality": "usable",
                    "validated_expected_return_bps": 41.8,
                    "validated_hit_rate": 0.55,
                    "validated_profit_factor": 1.4,
                },
            ]
        )
    )

    assert set(ranked["expected_return_scope"]) == {"side"}
    assert "ticker" not in set(ranked["expected_return_scope"])
