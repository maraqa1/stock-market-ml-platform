from __future__ import annotations

from pathlib import Path

from stockml.candidates.short_side_policy import ShortSidePolicy, load_short_side_policy, short_side_block_reason


def _alpaca_config(**overrides):
    from stockml.trading.config import AlpacaConfig

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


def test_short_policy_blocks_short_by_default():
    assert short_side_block_reason({"side": "sell"}, ShortSidePolicy()) == "short_side_validation_required"


def test_short_policy_does_not_block_long():
    assert short_side_block_reason({"side": "buy"}, ShortSidePolicy()) == ""


def test_short_policy_allows_short_when_enabled_and_allowed():
    policy = ShortSidePolicy(enabled=True, allow_shorts_in_validation=True)
    assert short_side_block_reason({"trade_action": "Short"}, policy) == ""


def test_short_policy_loads_config(tmp_path: Path):
    path = tmp_path / "trading.yaml"
    path.write_text(
        "\n".join(
            [
                "short_side_policy:",
                "  enabled: true",
                "  allow_shorts_in_validation: true",
                "  require_short_side_attribution_pass: false",
                "  research_only_when_disabled: false",
                "  min_closed_short_trades_for_enablement: 75",
                "  min_short_profit_factor_for_enablement: 1.25",
                "  min_short_win_rate_for_enablement: 0.55",
            ]
        ),
        encoding="utf-8",
    )
    policy = load_short_side_policy(path)
    assert policy.enabled is True
    assert policy.allow_shorts_in_validation is True
    assert policy.require_short_side_attribution_pass is False
    assert policy.research_only_when_disabled is False
    assert policy.min_closed_short_trades_for_enablement == 75
    assert policy.min_short_profit_factor_for_enablement == 1.25
    assert policy.min_short_win_rate_for_enablement == 0.55


def test_short_policy_blocks_basket_order_submission_when_disabled():
    import pandas as pd

    from stockml.trading.order_planner import build_order_plan

    signals = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "trade_action": "Short",
                "side_probability": 0.8,
                "probability_edge": -0.2,
                "risk_adjusted_score": -1.2,
                "close": 20,
                "volume": 1_000_000,
                "avg_dollar_volume_20d": 50_000_000,
                "market_cap": 5_000_000_000,
                "volatility_20d": 0.02,
            }
        ]
    )
    plan = build_order_plan(signals, _alpaca_config(allow_short_selling=True, max_orders=1))
    assert not plan.empty
    assert plan.iloc[0]["side"] == "sell"
    assert plan.iloc[0]["trade_quality_status"] == "rejected"
    assert plan.iloc[0]["order_eligible"] is False or str(plan.iloc[0]["order_eligible"]).lower() == "false"
    assert "short_side_validation_required" in plan.iloc[0]["trade_quality_reason"]


def test_short_policy_keeps_long_executable_when_disabled():
    import pandas as pd

    from stockml.candidates.execution_ranker import build_execution_ranked_candidates

    ranked = build_execution_ranked_candidates(
        pd.DataFrame(
            [
                {
                    "symbol": "AAA",
                    "side": "buy",
                    "trade_action": "Long",
                    "trade_quality_status": "approved",
                    "order_eligible": True,
                    "approved_notional": 100,
                    "suggested_quantity": 1,
                    "expected_return_quality": "calibrated",
                    "candidate_rank": 1,
                }
            ]
        )
    )
    assert ranked.iloc[0]["status"] == "executable"
    assert ranked.iloc[0]["execution_rank"] == 1
