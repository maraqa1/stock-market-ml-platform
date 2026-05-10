from __future__ import annotations

import pandas as pd

from stockml.trading.config import AlpacaConfig
from stockml.trading import paper_autopilot


def _config(live: bool = False) -> AlpacaConfig:
    return AlpacaConfig(
        api_key="paper-key",
        secret_key="paper-secret",
        base_url="https://paper-api.alpaca.markets",
        submit_orders=True,
        extended_hours=False,
        max_orders=10,
        max_notional_per_order=1000,
        max_total_notional=10000,
        min_trade_price=5,
        max_sector_fraction=0.4,
        min_side_probability=0.55,
        min_abs_probability_edge=0.05,
        min_intraday_volume=100000,
        min_market_cap=300000000,
        min_risk_adjusted_score=0.005,
        transaction_cost_bps=10,
        live_trading_enabled=live,
        paper_trading_enabled=True,
    )


def test_paper_autopilot_start_is_paper_only(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())

    state = paper_autopilot.start(tmp_path)

    assert state["status"] == "running"
    assert state["phase"] == "tracking_orders"
    assert state["paper_only"] is True
    assert state["live_trading_enabled"] is False


def test_paper_autopilot_refuses_live_config(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config(live=True))

    state = paper_autopilot.start(tmp_path)

    assert state["status"] == "stopped"
    assert state["phase"] == "guardrail_stop"
    assert state["termination_reason"] == "live_trading_enabled_guardrail"


def test_paper_autopilot_tick_waits_for_fills(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    state = paper_autopilot.start(tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "accepted"}]).to_csv(tracking, index=False)
    pd.DataFrame([{"symbol": "AAA", "qty": 1}]).to_csv(positions, index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
    )

    assert state["status"] == "running"
    assert state["phase"] == "waiting_for_fills"
    assert state["open_orders"] == 1
    assert state["open_positions"] == 1


def test_paper_autopilot_tick_terminates_when_flat(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([]).to_csv(positions, index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
    )

    assert state["status"] == "complete"
    assert state["phase"] == "cycle_complete"
    assert state["termination_reason"] == "no_open_orders_or_positions"
