import pandas as pd

from stockml.trading.config import AlpacaConfig
from stockml.trading.order_builder import order_row


def config():
    return AlpacaConfig(
        api_key="",
        secret_key="",
        base_url="https://paper-api.alpaca.markets",
        submit_orders=False,
        extended_hours=False,
        max_orders=10,
        max_notional_per_order=1000.0,
        max_total_notional=10000.0,
        min_trade_price=5.0,
        max_sector_fraction=1.0,
        min_side_probability=0.55,
        min_abs_probability_edge=0.05,
        min_intraday_volume=100000,
        min_market_cap=300000000.0,
        min_risk_adjusted_score=0.005,
        transaction_cost_bps=10.0,
    )


def test_order_builder_keeps_quality_and_exit_fields():
    row = pd.Series(
        {
            "ticker": "FLEX",
            "company": "Flex Ltd.",
            "sector": "Technology",
            "trade_action": "Long",
            "approved_notional": 1000,
            "suggested_quantity": 7,
            "trade_quality_status": "approved",
            "trade_quality_reason": "approved",
            "stop_loss_price": 134.83,
            "take_profit_price": 147.34,
            "take_profit": 147.34,
            "order_eligible": True,
        }
    )
    order = order_row(row, config())
    assert order["symbol"] == "FLEX"
    assert order["side"] == "buy"
    assert order["order_eligible"] is True
    assert order["stop_loss_price"] == 134.83
    assert order["trade_quality_status"] == "approved"
