from __future__ import annotations

import pandas as pd

from stockml.diagnostics.short_squeeze_risk import build_short_squeeze_risk, short_squeeze_risk_for_row


def test_high_volatility_gap_up_creates_squeeze_risk_flag():
    risk = short_squeeze_risk_for_row({"volatility_20d": 0.1, "gap_pct": 0.08, "return_5d": 0.15})
    assert risk["short_squeeze_risk_tier"] == "high"
    assert "extreme_volatility" in risk["short_squeeze_risk_reasons"]
    assert "gap_up" in risk["short_squeeze_risk_reasons"]


def test_build_short_squeeze_risk_only_reports_shorts():
    out = build_short_squeeze_risk(
        pd.DataFrame(
            [
                {"symbol": "AAA", "trade_action": "Short", "volatility_20d": 0.1},
                {"symbol": "BBB", "trade_action": "Long", "volatility_20d": 0.1},
            ]
        )
    )
    assert list(out["symbol"]) == ["AAA"]
