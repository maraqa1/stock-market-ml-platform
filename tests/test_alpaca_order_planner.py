import pandas as pd

from stockml.trading.config import AlpacaConfig
from stockml.trading.order_planner import build_order_plan, filter_tradeable_signals


def config(**overrides):
    values = {
        "api_key": "",
        "secret_key": "",
        "base_url": "https://paper-api.alpaca.markets",
        "submit_orders": False,
        "extended_hours": False,
        "max_orders": 2,
        "max_notional_per_order": 500.0,
        "max_total_notional": 1000.0,
        "min_trade_price": 5.0,
        "max_sector_fraction": 1.0,
        "min_side_probability": 0.55,
        "min_abs_probability_edge": 0.05,
    }
    values.update(overrides)
    return AlpacaConfig(**values)


def test_filter_tradeable_signals_keeps_only_gated_long_short():
    signals = pd.DataFrame(
        [
            {"ticker": "AAA", "trade_action": "Long", "side_probability": 0.7, "probability_edge": 0.2, "close": 10, "risk_adjusted_score": 0.5},
            {"ticker": "BBB", "trade_action": "Short", "side_probability": 0.8, "probability_edge": -0.3, "close": 20, "risk_adjusted_score": -0.7},
            {"ticker": "CCC", "trade_action": "No Decision", "side_probability": 0.9, "probability_edge": 0.4, "close": 30, "risk_adjusted_score": 0.9},
            {"ticker": "DDD", "trade_action": "Long", "side_probability": 0.51, "probability_edge": 0.01, "close": 40, "risk_adjusted_score": 0.1},
        ]
    )
    filtered = filter_tradeable_signals(signals, config())
    assert set(filtered["ticker"]) == {"AAA", "BBB"}


def test_build_order_plan_uses_notional_paper_orders():
    signals = pd.DataFrame(
        [{"ticker": "AAA", "date": "2026-05-08", "trade_action": "Long", "side_probability": 0.7, "probability_edge": 0.2, "close": 10, "risk_adjusted_score": 0.5}]
    )
    plan = build_order_plan(signals, config(max_notional_per_order=250.0, extended_hours=True))
    assert plan.iloc[0]["symbol"] == "AAA"
    assert plan.iloc[0]["side"] == "buy"
    assert plan.iloc[0]["notional"] == 250.0
    assert bool(plan.iloc[0]["extended_hours"]) is True
    assert plan.iloc[0]["client_order_id"] == "stockml-20260508-AAA-buy"


def test_build_order_plan_returns_empty_when_required_columns_missing():
    plan = build_order_plan(pd.DataFrame([{"ticker": "AAA"}]), config())
    assert plan.empty


def test_order_plan_applies_price_and_total_notional_guards():
    signals = pd.DataFrame(
        [
            {"ticker": "AAA", "trade_action": "Long", "side_probability": 0.8, "probability_edge": 0.2, "close": 2, "risk_adjusted_score": 0.9},
            {"ticker": "BBB", "trade_action": "Long", "side_probability": 0.8, "probability_edge": 0.2, "close": 20, "risk_adjusted_score": 0.8},
            {"ticker": "CCC", "trade_action": "Long", "side_probability": 0.8, "probability_edge": 0.2, "close": 30, "risk_adjusted_score": 0.7},
        ]
    )
    plan = build_order_plan(signals, config(max_orders=3, max_notional_per_order=500.0, max_total_notional=500.0))
    assert list(plan["symbol"]) == ["BBB"]


def test_order_plan_limits_sector_concentration_when_sector_is_available():
    signals = pd.DataFrame(
        [
            {"ticker": "AAA", "trade_action": "Long", "side_probability": 0.8, "probability_edge": 0.2, "close": 20, "risk_adjusted_score": 0.9, "sector": "Technology"},
            {"ticker": "BBB", "trade_action": "Long", "side_probability": 0.8, "probability_edge": 0.2, "close": 20, "risk_adjusted_score": 0.8, "sector": "Technology"},
            {"ticker": "CCC", "trade_action": "Long", "side_probability": 0.8, "probability_edge": 0.2, "close": 20, "risk_adjusted_score": 0.7, "sector": "Healthcare"},
        ]
    )
    plan = build_order_plan(signals, config(max_orders=3, max_sector_fraction=0.34))
    assert list(plan["symbol"]) == ["AAA", "CCC"]
