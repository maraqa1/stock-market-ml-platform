from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from stockml.trading import paper_trader
from stockml.trading.config import AlpacaConfig


def cfg(owner: str) -> AlpacaConfig:
    return AlpacaConfig(
        api_key="key", secret_key="secret", base_url="paper", submit_orders=True, extended_hours=False,
        max_orders=20, max_notional_per_order=5000.0, max_total_notional=50000.0, min_trade_price=1.0,
        max_sector_fraction=1.0, min_side_probability=0.0, min_abs_probability_edge=0.0, min_intraday_volume=0,
        min_market_cap=0.0, min_risk_adjusted_score=-1.0, transaction_cost_bps=10.0, paper_trading_enabled=True,
        live_trading_enabled=False, execution_owner=owner,
    )


def plan() -> pd.DataFrame:
    return pd.DataFrame([{
        "symbol": "AAA", "side": "buy", "type": "market", "time_in_force": "day", "client_order_id": "cid-1",
        "trade_quality_status": "approved", "order_eligible": True, "suggested_quantity": 1, "trade_quality_reason": "",
        "notional": 100.0, "approved_notional": 100.0, "trade_action": "Long",
    }])


class Client:
    submitted = 0
    def __init__(self, config):
        pass
    def list_positions(self):
        return []
    def get_order(self, order_id):
        return {"id": order_id, "status": "accepted", "filled_qty": "0", "filled_avg_price": ""}
    def submit_order(self, order):
        Client.submitted += 1
        return {"id": "order-1", "client_order_id": order["client_order_id"], "status": "accepted"}


def setup(monkeypatch, tmp_path: Path, owner: str):
    Client.submitted = 0
    monkeypatch.setattr(paper_trader, "alpaca_config", lambda: cfg(owner))
    monkeypatch.setattr(paper_trader, "latest_model_freshness", lambda signal_file=None: (True, "fresh", "signal.csv"))
    monkeypatch.setattr(paper_trader, "latest_signal_table", lambda signal_file=None: pd.DataFrame())
    monkeypatch.setattr(paper_trader, "build_candidate_pool", lambda signals, config: plan())
    monkeypatch.setattr(paper_trader, "build_order_plan_from_candidate_pool", lambda pool, config: plan())
    monkeypatch.setattr(paper_trader, "_reject_autopilot_conflicts", lambda frame: frame)
    monkeypatch.setattr(paper_trader, "guard_actions", lambda *a, **k: ([], pd.DataFrame()))
    monkeypatch.setattr(paper_trader, "write_shortlist_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(paper_trader, "PORTAL_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(paper_trader, "AlpacaPaperClient", Client)
    monkeypatch.setattr(paper_trader, "load_submission_context", lambda client: {})
    monkeypatch.setattr(paper_trader, "validate_order", lambda order, client, context, seen: (True, ""))
    monkeypatch.setattr(paper_trader, "guard_order_submission", lambda *a, **k: (SimpleNamespace(allowed=True, block_reason=""), {}))


def test_paper_trader_submit_is_blocked_when_paper_autopilot_owns_execution(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, "paper_autopilot")
    result = paper_trader.run_paper_trading()
    assert result["orders_submitted"] == 0
    assert result["execution_owner_block_reason"] == "legacy_submitter_blocked_by_paper_autopilot_owner"
    assert Client.submitted == 0
    rows = pd.read_csv(result["result_path"])
    assert rows.iloc[0]["message"] == "legacy_submitter_blocked_by_paper_autopilot_owner"


def test_paper_trader_submit_allowed_only_for_legacy_owner(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, "legacy_paper_trader")
    result = paper_trader.run_paper_trading()
    assert result["orders_submitted"] == 1
    assert Client.submitted == 1
