from __future__ import annotations

from datetime import date

import pandas as pd

from stockml.experiments.raw_candidate_experiment_policy import (
    RawCandidateExperimentConfig,
    candidate_policy_decision,
    manual_enable_path,
    policy_can_start,
)
from stockml.trading.config import AlpacaConfig


def _trade_config(**overrides):
    values = {
        "api_key": "",
        "secret_key": "",
        "base_url": "https://paper-api.alpaca.markets",
        "submit_orders": False,
        "extended_hours": False,
        "max_orders": 20,
        "max_notional_per_order": 1000.0,
        "max_total_notional": 5000.0,
        "min_trade_price": 5.0,
        "max_sector_fraction": 0.4,
        "min_side_probability": 0.55,
        "min_abs_probability_edge": 0.05,
        "min_intraday_volume": 100000,
        "min_market_cap": 300000000.0,
        "min_risk_adjusted_score": 0.005,
        "transaction_cost_bps": 10.0,
        "live_trading_enabled": False,
        "paper_trading_enabled": True,
    }
    values.update(overrides)
    return AlpacaConfig(**values)


def test_experiment_disabled_by_default(tmp_path):
    decision = policy_can_start(RawCandidateExperimentConfig(), root=tmp_path, trade_config=_trade_config(), dry_run=True)
    assert decision.allowed is False
    assert decision.reason == "experiment_disabled"


def test_live_trading_cannot_be_enabled(tmp_path):
    cfg = RawCandidateExperimentConfig(enabled=True, live_trading_allowed=True)
    decision = policy_can_start(cfg, root=tmp_path, trade_config=_trade_config(live_trading_enabled=False), dry_run=True)
    assert decision.allowed is False
    assert decision.reason == "live_trading_disabled_required"


def test_manual_daily_enable_required_for_non_dry_run(tmp_path):
    cfg = RawCandidateExperimentConfig(enabled=True)
    day = date(2026, 7, 2)
    decision = policy_can_start(cfg, root=tmp_path, run_date=day, trade_config=_trade_config(), dry_run=False)
    assert decision.allowed is False
    assert decision.reason == "manual_daily_enable_missing"
    manual_enable_path(tmp_path, day).write_text("enabled\n")
    assert policy_can_start(cfg, root=tmp_path, run_date=day, trade_config=_trade_config(), dry_run=False).allowed


def test_short_candidate_is_skipped_when_disabled(tmp_path):
    row = pd.Series({"symbol": "AAA", "experiment_side": "sell", "notional": 100, "original_status": "rejected", "original_trade_action": "Short"})
    decision = candidate_policy_decision(row, RawCandidateExperimentConfig(allow_shorts=False), root=tmp_path)
    assert decision.allowed is False
    assert decision.reason == "experiment_skip_short_disabled"


def test_max_trade_limits_are_enforced(tmp_path):
    cfg = RawCandidateExperimentConfig(max_trades_per_cycle=1)
    row = pd.Series({"symbol": "AAA", "experiment_side": "buy", "notional": 100, "original_status": "rejected", "original_trade_action": "Long"})
    decision = candidate_policy_decision(row, cfg, root=tmp_path, cycle_selected=1)
    assert decision.reason == "max_trades_per_cycle_reached"


def test_max_notional_is_enforced(tmp_path):
    cfg = RawCandidateExperimentConfig(max_notional_per_trade=250)
    row = pd.Series({"symbol": "AAA", "experiment_side": "buy", "notional": 251, "original_status": "rejected", "original_trade_action": "Long"})
    decision = candidate_policy_decision(row, cfg, root=tmp_path)
    assert decision.allowed is False
    assert decision.reason == "max_notional_per_trade_exceeded"
