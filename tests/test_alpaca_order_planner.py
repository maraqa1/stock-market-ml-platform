import pandas as pd

from stockml.trading.config import AlpacaConfig
from stockml.trading.order_planner import build_candidate_pool, build_order_plan, filter_tradeable_signals


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
        "min_intraday_volume": 100000,
        "min_market_cap": 300000000.0,
        "min_risk_adjusted_score": 0.001,
        "transaction_cost_bps": 10.0,
    }
    values.update(overrides)
    return AlpacaConfig(**values)


def test_filter_tradeable_signals_keeps_only_long_entries_by_default():
    signals = pd.DataFrame(
        [
            {"ticker": "AAA", "trade_action": "Long", "side_probability": 0.7, "probability_edge": 0.2, "close": 10, "risk_adjusted_score": 0.5},
            {"ticker": "BBB", "trade_action": "Short", "side_probability": 0.8, "probability_edge": -0.3, "close": 20, "risk_adjusted_score": -0.7},
            {"ticker": "CCC", "trade_action": "No Decision", "side_probability": 0.9, "probability_edge": 0.4, "close": 30, "risk_adjusted_score": 0.9},
            {"ticker": "DDD", "trade_action": "Long", "side_probability": 0.51, "probability_edge": 0.01, "close": 40, "risk_adjusted_score": 0.1},
        ]
    )
    filtered = filter_tradeable_signals(signals, config(max_orders=10))
    assert set(filtered["ticker"]) == {"AAA", "DDD"}


def test_filter_tradeable_signals_allows_short_when_enabled():
    signals = pd.DataFrame(
        [
            {"ticker": "AAA", "trade_action": "Long", "side_probability": 0.7, "probability_edge": 0.2, "risk_adjusted_score": 0.5},
            {"ticker": "BBB", "trade_action": "Short", "side_probability": 0.8, "probability_edge": -0.3, "risk_adjusted_score": -0.7},
            {"ticker": "CCC", "trade_action": "No Decision", "side_probability": 0.9, "probability_edge": 0.4, "risk_adjusted_score": 0.9},
        ]
    )
    filtered = filter_tradeable_signals(signals, config(max_orders=10, allow_short_selling=True))
    assert set(filtered["ticker"]) == {"AAA", "BBB"}


def test_filter_tradeable_signals_balances_long_and_short_slots_when_enabled():
    signals = pd.DataFrame(
        [
            {"ticker": f"L{i}", "trade_action": "Long", "side_probability": 0.8, "probability_edge": 0.2, "risk_adjusted_score": 100 - i}
            for i in range(10)
        ]
        + [
            {"ticker": f"S{i}", "trade_action": "Short", "side_probability": 0.8, "probability_edge": -0.2, "risk_adjusted_score": -1 - i}
            for i in range(10)
        ]
    )
    filtered = filter_tradeable_signals(signals, config(max_orders=10, allow_short_selling=True))
    assert filtered["trade_action"].str.lower().value_counts().to_dict() == {"long": 5, "short": 5}


def test_filter_tradeable_signals_backfills_when_one_side_has_fewer_candidates():
    signals = pd.DataFrame(
        [
            {"ticker": f"L{i}", "trade_action": "Long", "side_probability": 0.8, "probability_edge": 0.2, "risk_adjusted_score": 100 - i}
            for i in range(10)
        ]
        + [
            {"ticker": "S0", "trade_action": "Short", "side_probability": 0.8, "probability_edge": -0.2, "risk_adjusted_score": -1}
        ]
    )
    filtered = filter_tradeable_signals(signals, config(max_orders=6, allow_short_selling=True))
    counts = filtered["trade_action"].str.lower().value_counts().to_dict()
    assert counts == {"long": 5, "short": 1}


def test_filter_tradeable_signals_excludes_diagnostic_only_rows():
    signals = pd.DataFrame(
        [
            {"ticker": "AAA", "trade_action": "Long", "model_status": "decision_grade", "side_probability": 0.7, "probability_edge": 0.2, "risk_adjusted_score": 0.5},
            {"ticker": "BBB", "trade_action": "Long", "model_status": "diagnostic_only", "side_probability": 0.8, "probability_edge": 0.3, "risk_adjusted_score": 0.7},
        ]
    )
    filtered = filter_tradeable_signals(signals, config(max_orders=10))
    assert list(filtered["ticker"]) == ["AAA"]


def test_filter_tradeable_signals_allows_explicit_diagnostic_paper_candidates():
    signals = pd.DataFrame(
        [
            {"ticker": "AAA", "trade_action": "Long", "diagnostic_only": True, "signal_reason": "model_not_decision_grade", "side_probability": 0.7, "probability_edge": 0.2, "risk_adjusted_score": 0.5},
            {"ticker": "BBB", "trade_action": "Long", "diagnostic_only": True, "signal_reason": "diagnostic_paper_candidate_model_not_decision_grade", "side_probability": 0.8, "probability_edge": 0.3, "risk_adjusted_score": 0.7},
        ]
    )
    filtered = filter_tradeable_signals(signals, config(max_orders=10))
    assert list(filtered["ticker"]) == ["BBB"]


def test_build_order_plan_ignores_no_decision_rows_even_when_high_ranked():
    signals = pd.DataFrame(
        [
            {"ticker": "NOPE", "trade_action": "No Decision", "side_probability": 0.99, "probability_edge": 0.49, "expected_trade_return": 0.1, "close": 50, "open": 50, "high": 51, "low": 49, "volume": 1_000_000, "avg_dollar_volume_20d": 60_000_000, "market_cap": 20_000_000_000, "volatility_20d": 0.02, "risk_adjusted_score": 9.0},
            {"ticker": "YES", "date": "2026-05-08", "trade_action": "Long", "side_probability": 0.7, "probability_edge": 0.2, "expected_trade_return": 0.02, "close": 10, "open": 9.8, "high": 10.2, "low": 9.7, "volume": 1_000_000, "avg_dollar_volume_20d": 60_000_000, "market_cap": 20_000_000_000, "volatility_20d": 0.02, "risk_adjusted_score": 0.5},
        ]
    )
    plan = build_order_plan(signals, config(max_orders=10))
    assert list(plan["symbol"]) == ["YES"]


def trade_signal(ticker, action, score, **overrides):
    values = {
        "ticker": ticker,
        "date": "2026-05-08",
        "trade_action": action,
        "side_probability": 0.8,
        "probability_edge": 0.2 if action == "Long" else -0.2,
        "expected_trade_return": 0.02,
        "close": 20,
        "open": 20,
        "high": 21,
        "low": 19,
        "volume": 1_000_000,
        "avg_dollar_volume_20d": 60_000_000,
        "market_cap": 20_000_000_000,
        "volatility_20d": 0.02,
        "risk_adjusted_score": score,
        "sector": "Technology" if action == "Long" else "Healthcare",
    }
    values.update(overrides)
    return values


def test_build_order_plan_balances_eligible_long_and_short_orders_when_shorting_enabled():
    signals = pd.DataFrame(
        [trade_signal(f"L{i}", "Long", 1.0 - (i * 0.01)) for i in range(4)]
        + [trade_signal(f"S{i}", "Short", 0.8 - (i * 0.01)) for i in range(4)]
    )
    plan = build_order_plan(signals, config(max_orders=4, allow_short_selling=True))

    assert plan["trade_action"].value_counts().to_dict() == {"Long": 2, "Short": 2}
    assert set(plan.loc[plan["trade_action"].eq("Short"), "side"]) == {"sell"}
    assert set(plan["trade_quality_status"]) == {"approved"}


def test_build_order_plan_keeps_rejected_short_rows_visible_for_operator_review():
    signals = pd.DataFrame(
        [trade_signal(f"L{i}", "Long", 1.0 - (i * 0.01)) for i in range(4)]
        + [
            trade_signal(
                f"S{i}",
                "Short",
                0.8 - (i * 0.01),
                market_cap=100_000_000,
            )
            for i in range(4)
        ]
    )
    plan = build_order_plan(signals, config(max_orders=4, allow_short_selling=True))

    assert plan["trade_action"].value_counts().to_dict() == {"Long": 2, "Short": 2}
    short_rows = plan[plan["trade_action"].eq("Short")]
    assert set(short_rows["trade_quality_status"]) == {"rejected"}
    assert short_rows["order_eligible"].eq(False).all()
    assert short_rows["trade_quality_reason"].str.contains("market_cap_below_minimum").all()


def test_candidate_pool_uses_ranked_long_and_short_shortlist_when_shorting_enabled():
    signals = pd.DataFrame(
        [
            trade_signal(
                f"T{i:02d}",
                "No Decision",
                score=0.1,
                rank_overall=i + 1,
                side_probability=0.7,
                probability_edge=0.2,
                expected_trade_return=0.02,
            )
            for i in range(12)
        ]
    )
    pool = build_candidate_pool(signals, config(candidate_pool_size=10, max_orders=4, allow_short_selling=True))

    assert len(pool) == 10
    assert pool["trade_action"].value_counts().to_dict() == {"Long": 5, "Short": 5}
    assert set(pool.loc[pool["trade_action"].eq("Short"), "side"]) == {"sell"}


def test_candidate_pool_size_is_configurable():
    signals = pd.DataFrame(
        [
            trade_signal(
                f"T{i:02d}",
                "No Decision",
                score=0.1,
                rank_overall=i + 1,
                side_probability=0.7,
                probability_edge=0.2,
                expected_trade_return=0.02,
            )
            for i in range(30)
        ]
    )
    pool = build_candidate_pool(signals, config(candidate_pool_size=12, max_orders=4, allow_short_selling=True))

    assert len(pool) == 12
    assert pool["trade_action"].value_counts().to_dict() == {"Long": 6, "Short": 6}


def test_candidate_pool_uses_directional_action_window_when_available():
    signals = pd.DataFrame(
        [
            trade_signal(
                f"L{i:02d}",
                "No Decision",
                score=1.0 - (i * 0.01),
                rank_overall=i + 1,
                directional_action="Long",
                directional_strength=1.0 - (i * 0.001),
                directional_reason="rank_within_directional_long_window",
                probability_edge=0.2,
                sector="Technology",
            )
            for i in range(12)
        ]
        + [
            trade_signal(
                f"S{i:02d}",
                "No Decision",
                score=-1.0 + (i * 0.01),
                rank_overall=1000 - i,
                directional_action="Short",
                directional_strength=1.0 - (i * 0.001),
                directional_reason="rank_within_directional_short_window",
                probability_edge=-0.2,
                sector="Healthcare",
            )
            for i in range(12)
        ]
    )
    pool = build_candidate_pool(
        signals,
        config(candidate_pool_size=10, max_orders=4, allow_short_selling=True, directional_candidate_long_fraction=0.70),
    )

    assert len(pool) == 10
    assert pool["trade_action"].value_counts().to_dict() == {"Long": 7, "Short": 3}
    assert set(pool.loc[pool["trade_action"].eq("Long"), "symbol"]).issuperset({"L00", "L06"})
    assert "directional_strength" in pool.columns


def test_candidate_pool_still_shows_ranked_long_and_short_research_shortlist_when_shorting_disabled():
    signals = pd.DataFrame(
        [
            trade_signal(
                f"T{i:02d}",
                "No Decision",
                score=0.1,
                rank_overall=i + 1,
                side_probability=0.7,
                probability_edge=0.2,
                expected_trade_return=0.02,
            )
            for i in range(12)
        ]
    )
    pool = build_candidate_pool(signals, config(candidate_pool_size=10, max_orders=4, allow_short_selling=False))

    assert len(pool) == 10
    assert pool["trade_action"].value_counts().to_dict() == {"Long": 5, "Short": 5}
    short_rows = pool[pool["trade_action"].eq("Short")]
    assert set(short_rows["trade_quality_status"]) == {"rejected"}
    assert short_rows["trade_quality_reason"].str.contains("shorting_disabled").all()


def test_build_order_plan_uses_notional_paper_orders():
    signals = pd.DataFrame(
        [{
            "ticker": "AAA", "date": "2026-05-08", "trade_action": "Long", "side_probability": 0.7,
            "probability_edge": 0.2, "expected_trade_return": 0.02, "close": 10, "open": 9.8,
            "high": 10.2, "low": 9.7, "volume": 1_000_000, "avg_dollar_volume_20d": 60_000_000,
            "market_cap": 20_000_000_000, "volatility_20d": 0.02, "risk_adjusted_score": 0.5,
        }]
    )
    plan = build_order_plan(signals, config(max_notional_per_order=250.0, extended_hours=True))
    assert plan.iloc[0]["symbol"] == "AAA"
    assert plan.iloc[0]["side"] == "buy"
    assert plan.iloc[0]["notional"] == 375.0
    assert plan.iloc[0]["trade_quality_status"] == "approved"
    assert bool(plan.iloc[0]["extended_hours"]) is False
    assert plan.iloc[0]["client_order_id"] == "stockml-20260508-AAA-buy"


def test_build_order_plan_returns_empty_when_required_columns_missing():
    plan = build_order_plan(pd.DataFrame([{"ticker": "AAA"}]), config())
    assert plan.empty


def test_order_plan_applies_price_and_total_notional_guards():
    signals = pd.DataFrame(
        [
            {"ticker": "AAA", "trade_action": "Long", "side_probability": 0.8, "probability_edge": 0.2, "close": 2, "risk_adjusted_score": 0.9},
            {"ticker": "BBB", "trade_action": "Long", "side_probability": 0.8, "probability_edge": 0.2, "expected_trade_return": 0.02, "close": 20, "open": 20, "high": 21, "low": 19, "volume": 1_000_000, "avg_dollar_volume_20d": 60_000_000, "market_cap": 20_000_000_000, "volatility_20d": 0.02, "risk_adjusted_score": 0.8},
            {"ticker": "CCC", "trade_action": "Long", "side_probability": 0.8, "probability_edge": 0.2, "expected_trade_return": 0.02, "close": 30, "open": 30, "high": 31, "low": 29, "volume": 1_000_000, "avg_dollar_volume_20d": 60_000_000, "market_cap": 20_000_000_000, "volatility_20d": 0.02, "risk_adjusted_score": 0.7},
        ]
    )
    plan = build_order_plan(signals, config(max_orders=3, max_notional_per_order=500.0, max_total_notional=500.0))
    approved = plan[plan["trade_quality_status"].eq("approved")]
    assert "AAA" not in list(approved["symbol"])
    assert set(approved["symbol"]) == {"BBB", "CCC"}


def test_order_plan_prioritizes_directional_strength_before_risk_score():
    signals = pd.DataFrame(
        [
            trade_signal(
                "LOW",
                "No Decision",
                score=10.0,
                rank_overall=1,
                directional_action="Long",
                directional_strength=0.91,
                directional_reason="rank_within_directional_long_window",
            ),
            trade_signal(
                "HIGH",
                "No Decision",
                score=0.1,
                rank_overall=2,
                directional_action="Long",
                directional_strength=0.99,
                directional_reason="rank_within_directional_long_window",
            ),
        ]
    )

    plan = build_order_plan(signals, config(max_orders=1, candidate_pool_size=2))

    assert list(plan["symbol"]) == ["HIGH"]


def test_order_plan_limits_sector_concentration_when_sector_is_available():
    signals = pd.DataFrame(
        [
            {"ticker": "AAA", "trade_action": "Long", "side_probability": 0.8, "probability_edge": 0.2, "expected_trade_return": 0.02, "close": 20, "open": 20, "high": 21, "low": 19, "volume": 1_000_000, "avg_dollar_volume_20d": 60_000_000, "market_cap": 20_000_000_000, "volatility_20d": 0.02, "risk_adjusted_score": 0.9, "sector": "Technology"},
            {"ticker": "BBB", "trade_action": "Long", "side_probability": 0.8, "probability_edge": 0.2, "expected_trade_return": 0.02, "close": 20, "open": 20, "high": 21, "low": 19, "volume": 1_000_000, "avg_dollar_volume_20d": 60_000_000, "market_cap": 20_000_000_000, "volatility_20d": 0.02, "risk_adjusted_score": 0.8, "sector": "Technology"},
            {"ticker": "CCC", "trade_action": "Long", "side_probability": 0.8, "probability_edge": 0.2, "expected_trade_return": 0.02, "close": 20, "open": 20, "high": 21, "low": 19, "volume": 1_000_000, "avg_dollar_volume_20d": 60_000_000, "market_cap": 20_000_000_000, "volatility_20d": 0.02, "risk_adjusted_score": 0.7, "sector": "Healthcare"},
        ]
    )
    plan = build_order_plan(signals, config(max_orders=3, max_sector_fraction=0.34))
    assert list(plan["symbol"]) == ["AAA", "CCC"]
