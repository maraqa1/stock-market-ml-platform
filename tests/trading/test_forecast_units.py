from __future__ import annotations

import pandas as pd

from stockml.trading.per_symbol_forecast.generate import forecast_rows
from stockml.trading.per_symbol_forecast.statistical import ForecastBounds, statistical_fields


def test_realistic_fwrd_case_uses_bps_not_fraction_units():
    row = {
        "symbol": "FWRD",
        "side": "buy",
        "trade_action": "Long",
        "candidate_rank": 1,
        "risk_adjusted_score": 0.53132675112664,
        "expected_trade_return": 1.06265350225328,
        "current_price": 8.739999771118164,
        "volatility_20d": 0.1203533899588113,
        "liquidity_tier": "medium",
        "volatility_tier": "extreme",
    }

    result = statistical_fields(row)

    assert -500 <= result["expected_5d_return_bps"] <= 500
    assert round(result["expected_5d_return_bps"], 3) == 106.265
    assert result["expected_move_bps"] < 500


def test_forecast_rows_do_not_emit_legacy_expected_return_names():
    frame = forecast_rows(
        pd.DataFrame(
            [
                {
                    "symbol": "AAPL",
                    "side": "buy",
                    "trade_action": "Long",
                    "candidate_rank": 1,
                    "risk_adjusted_score": 0.5,
                    "expected_trade_return": 0.02,
                    "current_price": 100,
                    "volatility_20d": 0.02,
                }
            ]
        )
    )

    assert "expected_1d_return_bps" in frame.columns
    assert "expected_5d_return_bps" in frame.columns
    assert "expected_1d_return" not in frame.columns
    assert "expected_5d_return" not in frame.columns


def test_projection_caps_prevent_silent_units_bugs():
    row = {
        "symbol": "BUG",
        "side": "buy",
        "trade_action": "Long",
        "risk_adjusted_score": 5.0,
        "current_price": 10,
        "volatility_20d": 0.02,
    }

    result = statistical_fields(row, slope_5d=500, bounds=ForecastBounds(reasonable_max_5d_return_bps=500))

    assert result["cap_applied"] is True
    assert result["pre_cap_expected_5d_bps"] == 2500
    assert result["expected_5d_return_bps"] == 500

