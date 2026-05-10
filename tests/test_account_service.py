from __future__ import annotations

from stockml.trading.config import AlpacaConfig
from portal.services.account import account_snapshot


def _config(api_key: str = "paper-key", secret_key: str = "paper-secret") -> AlpacaConfig:
    return AlpacaConfig(
        api_key=api_key,
        secret_key=secret_key,
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
        account_equity=33333.34,
        live_trading_enabled=False,
        paper_trading_enabled=True,
    )


class AccountClient:
    def __init__(self, payload):
        self.payload = payload

    def get_account(self):
        return self.payload


def test_account_snapshot_prefers_alpaca_equity():
    payload = account_snapshot(
        _config(),
        AccountClient({"equity": "48123.45", "cash": "1200.50", "buying_power": "2401", "portfolio_value": "48123.45", "account_number": "PA-1", "status": "ACTIVE"}),
    )

    assert payload["source"] == "alpaca"
    assert payload["equity"] == 48123.45
    assert payload["cash"] == 1200.50
    assert payload["account_id"] == "PA-1"


def test_account_snapshot_falls_back_to_config_without_credentials():
    payload = account_snapshot(_config(api_key="", secret_key=""))

    assert payload["source"] == "config"
    assert payload["equity"] == 33333.34
    assert payload["error"] == "alpaca_credentials_missing"
