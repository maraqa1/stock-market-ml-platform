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
        "min_risk_adjusted_score": 0.001,
        "transaction_cost_bps": 10.0,
    }
    values.update(overrides)
    return AlpacaConfig(**values)


def signal(**overrides):
    row = {
        "ticker": "FLEX",
        "date": "2026-05-08",
        "trade_action": "Long",
        "side_probability": 0.75,
        "probability_edge": 0.25,
        "expected_trade_return": 0.02,
        "risk_adjusted_score": 0.02,
        "close": 50,
        "open": 49,
        "high": 51,
        "low": 48,
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
    assert row["risk_tier"] == "large_liquid"
    assert row["approved_notional"] == 1000
    assert row["suggested_quantity"] == 20


def test_akan_like_unstable_stock_is_rejected():
    gated = apply_trade_quality_gate(
        pd.DataFrame([signal(ticker="AKAN", close=4.5, open=5.5, high=5.6, low=4.4, volatility_20d=0.13, market_cap=100_000_000)]),
        config(),
    )
    row = gated.iloc[0]
    assert row["trade_quality_status"] == "rejected"
    assert "intraday_drop_below_minus_8pct" in row["trade_quality_reason"]


def test_speculative_stock_gets_reduced_notional():
    gated = apply_trade_quality_gate(
        pd.DataFrame([signal(market_cap=600_000_000, avg_dollar_volume_20d=2_000_000, volume=120_000, volatility_20d=0.06)]),
        config(),
    )
    row = gated.iloc[0]
    assert row["trade_quality_status"] == "approved"
    assert row["risk_tier"] == "speculative"
    assert row["approved_notional"] == 250


def test_missing_price_rejects():
    gated = apply_trade_quality_gate(pd.DataFrame([signal(close=pd.NA)]), config(), price_snapshot=pd.DataFrame())
    row = gated.iloc[0]
    assert row["trade_quality_status"] == "rejected"
    assert "missing_or_invalid_current_price" in row["trade_quality_reason"]


def test_no_decision_creates_no_order():
    plan = build_order_plan(pd.DataFrame([signal(trade_action="No Decision")]), config())
    assert plan.empty


def test_diagnostic_only_creates_no_order():
    plan = build_order_plan(pd.DataFrame([signal(diagnostic_only=True)]), config())
    assert plan.empty
