import pandas as pd

from stockml.trading.config import AlpacaConfig
from stockml.trading.order_builder import order_row, validate_order_payload


def config(**overrides):
    values = dict(
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
    values.update(overrides)
    return AlpacaConfig(
        **values,
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


def test_order_builder_uses_limit_order_for_overnight_trading():
    row = pd.Series(
        {
            "ticker": "VSTM",
            "trade_action": "Long",
            "approved_notional": 2500,
            "suggested_quantity": 125,
            "trade_quality_status": "approved",
            "order_eligible": True,
            "current_price": 20.0,
        }
    )

    order = order_row(row, config(overnight_trading_enabled=True, overnight_limit_buffer_bps=50))

    assert order["type"] == "limit"
    assert order["extended_hours"] is True
    assert order["time_in_force"] == "day"
    assert order["limit_price"] == 20.1
    assert validate_order_payload(order).valid is True


def test_extended_hours_market_order_is_invalid():
    result = validate_order_payload(
        {
            "symbol": "VSTM",
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "qty": "1",
            "extended_hours": True,
        }
    )

    assert result.valid is False
    assert result.reason == "extended_hours_requires_limit"
