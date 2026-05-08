import pandas as pd

from stockml.trading.config import AlpacaConfig
from stockml.trading.order_planner import build_order_plan
from stockml.trading.trade_quality_gate import apply_trade_quality_gate


def config(**overrides):
    values = {
        "api_key": "",
        "secret_key": "",
        "base_url": "https://paper-api.alpaca.markets",
        "submit_orders": False,
        "extended_hours": False,
        "max_orders": 10,
        "max_notional_per_order": 1000.0,
        "max_total_notional": 10000.0,
        "min_trade_price": 5.0,
        "max_sector_fraction": 1.0,
        "min_side_probability": 0.55,
        "min_abs_probability_edge": 0.05,
        "min_intraday_volume": 100000,
        "min_market_cap": 300000000.0,
        "min_risk_adjusted_score": 0.005,
        "transaction_cost_bps": 10.0,
    }
    values.update(overrides)
    return AlpacaConfig(**values)


def signal(**overrides):
    row = {
        "ticker": "FLEX",
        "company": "Flex Ltd.",
        "sector": "Technology",
        "date": "2026-05-08",
        "trade_action": "Long",
        "side_probability": 0.75,
        "probability_edge": 0.25,
        "expected_trade_return": 0.02,
        "risk_adjusted_score": 0.02,
        "close": 139,
        "open": 138,
        "high": 141,
        "low": 137,
        "volume": 2_000_000,
        "avg_dollar_volume_20d": 100_000_000,
        "market_cap": 20_000_000_000,
        "volatility_20d": 0.02,
    }
    row.update(overrides)
    return row


def test_flex_like_large_liquid_stock_is_allowed():
    gated = apply_trade_quality_gate(pd.DataFrame([signal()]), config())
    row = gated.iloc[0]
    assert row["trade_quality_status"] == "approved"
    assert row["risk_tier"] == "high_quality"
    assert row["suggested_quantity"] > 0
    assert row["stop_loss_price"] < row["current_price"]
    assert row["take_profit_price"] > row["current_price"]


def test_akan_like_low_market_cap_stock_is_rejected():
    row = apply_trade_quality_gate(pd.DataFrame([signal(ticker="AKAN", market_cap=100_000_000)]), config()).iloc[0]
    assert row["trade_quality_status"] == "rejected"
    assert "market_cap_below_minimum" in row["trade_quality_reason"]


def test_price_below_minimum_rejects():
    row = apply_trade_quality_gate(pd.DataFrame([signal(ticker="BLDP", close=4.5)]), config()).iloc[0]
    assert row["trade_quality_status"] == "rejected"
    assert "price_below_minimum" in row["trade_quality_reason"]


def test_intraday_issue_is_explained():
    row = apply_trade_quality_gate(pd.DataFrame([signal(ticker="ACLS", close=91, open=100, high=101, low=90)]), config()).iloc[0]
    assert row["trade_quality_status"] == "rejected"
    assert "bottom_intraday_range_after_gap_down" in row["trade_quality_reason"]


def test_speculative_stock_gets_reduced_notional():
    row = apply_trade_quality_gate(pd.DataFrame([signal(market_cap=600_000_000, avg_dollar_volume_20d=6_000_000, volume=120_000, volatility_20d=0.06)]), config()).iloc[0]
    assert row["trade_quality_status"] == "reduced"
    assert row["risk_tier"] == "speculative"
    assert 0 < row["approved_notional"] < 1000


def test_missing_price_rejects():
    row = apply_trade_quality_gate(pd.DataFrame([signal(close=pd.NA)]), config(), price_snapshot=pd.DataFrame()).iloc[0]
    assert row["trade_quality_status"] == "rejected"
    assert "current_price_missing" in row["trade_quality_reason"]


def test_no_decision_creates_rejected_order_plan_row():
    plan = build_order_plan(pd.DataFrame([signal(trade_action="No Decision", no_decision_reason="weak_probability")]), config())
    assert plan.iloc[0]["trade_quality_status"] == "rejected"
    assert "not_long_or_short" in plan.iloc[0]["trade_quality_reason"]


def test_diagnostic_only_creates_rejected_order_plan_row():
    plan = build_order_plan(pd.DataFrame([signal(diagnostic_only=True)]), config())
    assert plan.iloc[0]["trade_quality_status"] == "rejected"
    assert "model_not_decision_grade" in plan.iloc[0]["trade_quality_reason"]


def test_shorting_disabled_rejects_short():
    row = apply_trade_quality_gate(pd.DataFrame([signal(trade_action="Short")]), config()).iloc[0]
    assert row["trade_quality_status"] == "rejected"
    assert "shorting_disabled" in row["trade_quality_reason"]


def test_missing_risk_fields_are_enriched_from_feature_snapshot():
    stripped = signal()
    stripped.pop("avg_dollar_volume_20d")
    stripped.pop("volatility_20d")
    risk_features = pd.DataFrame(
        [
            {
                "ticker": "FLEX",
                "date": "2026-05-08",
                "avg_dollar_volume_20d": 100_000_000,
                "volatility_20d": 0.02,
                "market_cap": 20_000_000_000,
            }
        ]
    )
    row = apply_trade_quality_gate(pd.DataFrame([stripped]), config(), risk_features=risk_features).iloc[0]
    assert row["trade_quality_status"] == "approved"
    assert row["avg_dollar_volume_20d"] == 100_000_000
    assert row["volatility_20d"] == 0.02
