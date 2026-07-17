import os

import pandas as pd

from stockml.trading.config import AlpacaConfig
from stockml.trading.order_planner import build_order_plan
from stockml.trading import trade_quality_gate
from stockml.trading.trade_quality_gate import apply_trade_quality_gate, latest_metadata_snapshot


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
        "min_market_cap": 500000000.0,
        "min_avg_dollar_volume_20d": 20_000_000.0,
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
        "source_trade_action": "Long",
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
        "expected_return_quality": "usable",
        "calibration_quality": "usable",
        "validated_expected_return_bps": 42,
        "validated_hit_rate": 0.56,
        "validated_profit_factor": 1.2,
        "ticker_direction_bias": "trust_long",
        "ticker_direction_sample_count": 100,
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
    row = apply_trade_quality_gate(pd.DataFrame([signal(market_cap=600_000_000, avg_dollar_volume_20d=25_000_000, volume=120_000, volatility_20d=0.06)]), config()).iloc[0]
    assert row["trade_quality_status"] == "reduced"
    assert row["risk_tier"] == "speculative"
    assert 0 < row["approved_notional"] < 1000


def test_extreme_volatility_long_with_validated_edge_is_rejected_when_opportunity_disabled(monkeypatch):
    monkeypatch.setattr(trade_quality_gate, "latest_expected_return_calibration", lambda: pd.DataFrame())
    row = apply_trade_quality_gate(
        pd.DataFrame(
            [
                signal(
                    ticker="VOLT",
                    close=20,
                    open=19.8,
                    high=21,
                    low=19,
                    source_trade_action="Long",
                    expected_return_scope="side",
                    expected_return_quality="usable",
                    calibration_quality="usable",
                    validated_expected_return_bps=80,
                    validated_hit_rate=0.56,
                    validated_profit_factor=1.4,
                    ticker_direction_bias="trust_long",
                    ticker_direction_sample_count=12,
                    volatility_20d=0.13,
                )
            ]
        ),
        config(),
    ).iloc[0]

    assert row["trade_quality_status"] == "rejected"
    assert row["risk_tier"] == "reject"
    assert row["approved_notional"] == 0
    assert row["suggested_quantity"] == 0
    assert row["volatility_opportunity_status"] == "disabled"
    assert bool(row["volatility_opportunity_allows_reduced_trade"]) is False
    assert "volatility_extreme" in row["trade_quality_reason"]


def test_extreme_volatility_long_with_weak_edge_stays_rejected(monkeypatch):
    monkeypatch.setattr(trade_quality_gate, "latest_expected_return_calibration", lambda: pd.DataFrame())
    row = apply_trade_quality_gate(
        pd.DataFrame(
            [
                signal(
                    ticker="WEAK",
                    source_trade_action="Long",
                    expected_return_scope="side",
                    expected_return_quality="usable",
                    calibration_quality="usable",
                    validated_expected_return_bps=10,
                    validated_hit_rate=0.56,
                    validated_profit_factor=1.4,
                    ticker_direction_bias="trust_long",
                    ticker_direction_sample_count=12,
                    volatility_20d=0.13,
                )
            ]
        ),
        config(),
    ).iloc[0]

    assert row["trade_quality_status"] == "rejected"
    assert "volatility_extreme" in row["trade_quality_reason"]
    assert row["volatility_opportunity_status"] == "disabled"
    assert row["volatility_opportunity_reason"] == "volatility_opportunity_disabled"


def test_extreme_volatility_planner_only_row_stays_rejected(monkeypatch):
    monkeypatch.setattr(trade_quality_gate, "latest_expected_return_calibration", lambda: pd.DataFrame())
    row = apply_trade_quality_gate(
        pd.DataFrame(
            [
                signal(
                    ticker="PLAN",
                    source_trade_action="No Decision",
                    expected_return_scope="side",
                    expected_return_quality="usable",
                    calibration_quality="usable",
                    validated_expected_return_bps=100,
                    validated_hit_rate=0.56,
                    validated_profit_factor=1.4,
                    ticker_direction_bias="trust_long",
                    ticker_direction_sample_count=12,
                    volatility_20d=0.13,
                )
            ]
        ),
        config(),
    ).iloc[0]

    assert row["trade_quality_status"] == "rejected"
    assert "volatility_extreme" in row["trade_quality_reason"]
    assert row["volatility_opportunity_reason"] == "volatility_opportunity_disabled"


def test_extreme_volatility_direction_conflict_stays_rejected(monkeypatch):
    monkeypatch.setattr(trade_quality_gate, "latest_expected_return_calibration", lambda: pd.DataFrame())
    row = apply_trade_quality_gate(
        pd.DataFrame(
            [
                signal(
                    ticker="CONFLICT",
                    source_trade_action="Long",
                    expected_return_scope="side",
                    expected_return_quality="usable",
                    calibration_quality="usable",
                    validated_expected_return_bps=100,
                    validated_hit_rate=0.56,
                    validated_profit_factor=1.4,
                    ticker_direction_bias="trust_short",
                    ticker_direction_sample_count=12,
                    volatility_20d=0.13,
                )
            ]
        ),
        config(),
    ).iloc[0]

    assert row["trade_quality_status"] == "rejected"
    assert "volatility_extreme" in row["trade_quality_reason"]
    assert row["volatility_opportunity_reason"] == "volatility_opportunity_disabled"


def test_higher_risk_profile_sizes_larger_paper_orders():
    cfg = config(account_equity=100_000, max_orders=20, max_total_notional=50_000, max_position_pct=0.05)
    rows = apply_trade_quality_gate(
        pd.DataFrame(
            [
                signal(ticker="HQ", side_probability=0.80),
                signal(ticker="MED", side_probability=0.80, market_cap=2_000_000_000, avg_dollar_volume_20d=25_000_000),
                signal(ticker="SPEC", side_probability=0.80, market_cap=600_000_000, avg_dollar_volume_20d=25_000_000, volume=120_000, volatility_20d=0.06),
            ]
        ),
        cfg,
    ).set_index("ticker")

    assert rows.loc["HQ", "approved_notional"] == 2500
    assert rows.loc["MED", "approved_notional"] == 1250
    assert rows.loc["SPEC", "approved_notional"] == 625


def test_missing_side_probability_does_not_crush_ranked_candidate_size():
    cfg = config(account_equity=100_000, max_orders=20, max_total_notional=50_000, max_position_pct=0.05)
    row = apply_trade_quality_gate(pd.DataFrame([signal(side_probability=0.0)]), cfg).iloc[0]

    assert row["risk_tier"] == "high_quality"
    assert row["approved_notional"] == 2500


def test_strong_directional_candidate_rounds_up_to_one_share_when_safe():
    row = apply_trade_quality_gate(
        pd.DataFrame(
            [
                signal(
                    ticker="MU",
                    close=700,
                    open=695,
                    high=705,
                    low=690,
                    trade_action="Long",
                    directional_action="Long",
                    directional_strength=0.98,
                    market_cap=500_000_000_000,
                    avg_dollar_volume_20d=200_000_000,
                    volume=2_000_000,
                )
            ]
        ),
        config(account_equity=20_000, max_position_pct=0.01, max_total_notional=1_000, max_orders=10),
    ).iloc[0]

    assert row["trade_quality_status"] == "approved"
    assert row["suggested_quantity"] == 1
    assert row["approved_notional"] == 700
    assert row["position_sizing_reason"] == "directional_one_share_round_up"


def test_directional_round_up_does_not_apply_to_speculative_candidates():
    row = apply_trade_quality_gate(
        pd.DataFrame(
            [
                signal(
                    ticker="SPEC",
                    close=700,
                    open=695,
                    high=705,
                    low=690,
                    trade_action="Long",
                    directional_action="Long",
                    directional_strength=0.99,
                        market_cap=600_000_000,
                        avg_dollar_volume_20d=25_000_000,
                    volume=120_000,
                )
            ]
        ),
        config(account_equity=20_000, max_position_pct=0.01, max_total_notional=1_000, max_orders=10),
    ).iloc[0]

    assert row["trade_quality_status"] == "rejected"
    assert "quantity_below_one" in row["trade_quality_reason"]


def test_missing_price_rejects():
    row = apply_trade_quality_gate(pd.DataFrame([signal(close=pd.NA)]), config(), price_snapshot=pd.DataFrame()).iloc[0]
    assert row["trade_quality_status"] == "rejected"
    assert "current_price_missing" in row["trade_quality_reason"]


def test_no_decision_creates_no_entry_order_plan_rows():
    plan = build_order_plan(pd.DataFrame([signal(trade_action="No Decision", no_decision_reason="weak_probability")]), config())
    assert plan.empty


def test_diagnostic_only_creates_no_entry_order_plan_rows():
    plan = build_order_plan(pd.DataFrame([signal(diagnostic_only=True)]), config())
    assert plan.empty


def test_shorting_disabled_rejects_short():
    row = apply_trade_quality_gate(pd.DataFrame([signal(trade_action="Short")]), config()).iloc[0]
    assert row["trade_quality_status"] == "rejected"
    assert "shorting_disabled" in row["trade_quality_reason"]


def test_short_negative_directional_scores_can_pass_when_shorting_enabled():
    row = apply_trade_quality_gate(
        pd.DataFrame(
            [
                signal(
                    trade_action="Short",
                    expected_trade_return=-0.02,
                    risk_adjusted_score=-0.02,
                    close=100,
                    open=101,
                    high=102,
                    low=99,
                )
            ]
        ),
        config(allow_short_selling=True),
    ).iloc[0]

    assert row["trade_quality_status"] == "approved"
    assert bool(row["order_eligible"]) is True
    assert row["stop_loss_price"] > row["current_price"]
    assert row["take_profit_price"] < row["current_price"]


def test_short_gap_up_top_range_is_rejected():
    row = apply_trade_quality_gate(
        pd.DataFrame(
            [
                signal(
                    trade_action="Short",
                    expected_trade_return=-0.02,
                    risk_adjusted_score=-0.02,
                    close=105,
                    open=100,
                    high=106,
                    low=99,
                )
            ]
        ),
        config(allow_short_selling=True),
    ).iloc[0]

    assert row["trade_quality_status"] == "rejected"
    assert "top_intraday_range_after_gap_up" in row["trade_quality_reason"]


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


def test_trade_quality_gate_reports_spread_adjusted_edge_when_available():
    row = apply_trade_quality_gate(pd.DataFrame([signal(spread_bps=40, expected_trade_return=0.05)]), config()).iloc[0]

    assert row["trade_quality_status"] == "approved"
    assert row["spread_gate_decision"] == "wide_spread_edge_supported"
    assert row["expected_move_bps"] == 500
    assert row["expected_net_edge_bps"] == 450
    assert row["edge_to_spread_ratio"] == 12.5


def test_latest_metadata_snapshot_backfills_from_recent_full_file(tmp_path, monkeypatch):
    older = tmp_path / "04_us_metadata_enriched_20260519_201330.csv"
    newer = tmp_path / "04_us_metadata_enriched_20260520_043845.csv"
    older.write_text("ticker,market_cap\nDDOG,20000000000\nVPG,500000000\n", encoding="utf-8")
    newer.write_text("ticker,market_cap\nOTHER,1000000000\n", encoding="utf-8")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    monkeypatch.setattr(trade_quality_gate, "INTERIM_DIR", tmp_path)

    frame = latest_metadata_snapshot(tickers=["DDOG"])

    assert frame.set_index("ticker").loc["DDOG", "market_cap"] == 20_000_000_000
