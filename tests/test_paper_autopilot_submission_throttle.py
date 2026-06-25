from datetime import datetime, timezone

from sqlalchemy import create_engine

from stockml.autopilot.open import AutoOpenConfig, apply_auto_open
from stockml.db.schema import create_all
from stockml.trading.config import AlpacaConfig


class FakeClient:
    def __init__(self):
        self.orders = []

    def get_account(self):
        return {"equity": "100000"}

    def get_asset(self, symbol):
        return {"tradable": True, "status": "active", "fractionable": True, "shortable": True, "attributes": ["overnight"]}

    def submit_order(self, order):
        self.orders.append(order)
        return {"id": f"oid-{order['symbol']}", "status": "accepted"}

    def list_orders(self, status="open", symbols=None):
        return []

    def list_positions(self):
        return []


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    return engine


def _trade_config():
    return AlpacaConfig(
        api_key="paper-key", secret_key="paper-secret", base_url="https://paper-api.alpaca.markets",
        submit_orders=True, paper_trading_enabled=True, live_trading_enabled=False,
        extended_hours=False, overnight_trading_enabled=False, max_orders=10,
        max_notional_per_order=1000, max_total_notional=10000, min_trade_price=1,
        max_sector_fraction=1.0, min_side_probability=0.0, min_abs_probability_edge=0.0,
        min_intraday_volume=0, min_market_cap=0, min_risk_adjusted_score=0.0,
        transaction_cost_bps=0, account_equity=100000, max_position_pct=0.2,
        allow_short_selling=True,
    )


def _candidate(symbol):
    return {
        "symbol": symbol, "promotion_score": 0.9, "nightly_bias": "long", "current_price": 10,
        "is_held": False, "meta_label_decision": "Take Trade", "directional_action": "Long",
        "directional_strength": 1.0, "trade_quality_status": "approved",
        "details": {"is_first_15_min": False, "is_last_30_min": False},
    }


def test_validation_mode_caps_new_orders_to_one_per_cycle():
    engine = _engine()
    client = FakeClient()
    cfg = AutoOpenConfig(open_enabled=True, max_positions=20, max_auto_opens_per_day=20, validation_mode=True, validation_max_new_orders_per_cycle=1, validation_max_new_orders_per_day=2, validation_max_open_positions_total=3)
    result = apply_auto_open([_candidate(f"AAA{i}") for i in range(12)], [], mode="paper_autopilot", engine=engine, config=cfg, alpaca_cfg=_trade_config(), client=client, now=datetime(2026, 6, 24, 15, 0, tzinfo=timezone.utc))
    assert result["autopilot_open_submitted"] == 1
    assert len(client.orders) == 1
