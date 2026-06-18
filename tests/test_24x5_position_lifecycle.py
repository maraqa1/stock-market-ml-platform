from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select

from stockml.autopilot.open import AutoOpenConfig, apply_auto_open
from stockml.db.schema import create_all, position_events
from stockml.services import events
from stockml.trading.config import AlpacaConfig

NOW = datetime(2026, 6, 18, 13, 0, tzinfo=timezone.utc)


class GuardedClient:
    def __init__(self, positions):
        self.positions = positions
        self.submitted = []

    def get_account(self):
        return {"equity": "100000"}

    def get_asset(self, symbol):
        return {"tradable": True, "status": "active", "shortable": True, "overnight_tradable": True}

    def list_positions(self):
        return self.positions

    def submit_order(self, order):
        self.submitted.append(order)
        return {"id": "ord-1", "status": "new"}


def trade_cfg():
    return AlpacaConfig(
        api_key="key",
        secret_key="secret",
        base_url="paper",
        submit_orders=True,
        extended_hours=True,
        max_orders=20,
        max_notional_per_order=5000.0,
        max_total_notional=50000.0,
        min_trade_price=1.0,
        max_sector_fraction=1.0,
        min_side_probability=0.0,
        min_abs_probability_edge=0.0,
        min_intraday_volume=0,
        min_market_cap=0.0,
        min_risk_adjusted_score=-1.0,
        transaction_cost_bps=10.0,
        paper_trading_enabled=True,
        live_trading_enabled=False,
        allow_short_selling=True,
        overnight_trading_enabled=True,
        overnight_limit_buffer_bps=50.0,
    )


def auto_cfg():
    return AutoOpenConfig(open_enabled=True, max_auto_opens_per_day=5, max_positions=5, default_position_value_cap_usd=1000, default_position_pct_of_equity=0.01, max_single_position_pct_of_equity=0.05, holding_review_gate_enabled=False, basket_drawdown_pause_pct=99)


def _engine(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    monkeypatch.setattr(events, "get_engine", lambda required=False: engine)
    return engine


def candidate(symbol, action):
    return {
        "symbol": symbol,
        "promotion_score": 1,
        "nightly_bias": action.lower(),
        "current_price": 100,
        "trade_action": action,
        "meta_label_decision": "Take Trade",
        "trade_quality_status": "approved",
    }


def test_24x5_extended_path_blocks_long_close_before_submit(monkeypatch):
    engine = _engine(monkeypatch)
    client = GuardedClient([{"symbol": "AGL", "qty": "17", "avg_entry_price": "105", "opened_at": (NOW - timedelta(minutes=1)).isoformat()}])
    result = apply_auto_open([candidate("AGL", "Short")], [], mode="paper_autopilot", engine=engine, config=auto_cfg(), alpaca_cfg=trade_cfg(), client=client, now=NOW)
    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert client.submitted == []
    with engine.connect() as conn:
        rows = conn.execute(select(position_events).where(position_events.c.event_type == "position_intent_blocked")).mappings().all()
    assert len(rows) == 1
    assert rows[0]["details"]["symbol"] == "AGL"
    assert rows[0]["details"]["block_reason"] == "minimum_hold_period_not_met"


def test_24x5_extended_path_blocks_short_cover_before_submit(monkeypatch):
    engine = _engine(monkeypatch)
    client = GuardedClient([{"symbol": "KRMN", "qty": "-36", "avg_entry_price": "52", "opened_at": (NOW - timedelta(minutes=24)).isoformat()}])
    result = apply_auto_open([candidate("KRMN", "Long")], [], mode="paper_autopilot", engine=engine, config=auto_cfg(), alpaca_cfg=trade_cfg(), client=client, now=NOW)
    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert client.submitted == []
    assert "minimum_hold_period_not_met" in result["autopilot_open_notes"]


def test_24x5_allows_new_symbol_when_no_position(monkeypatch):
    engine = _engine(monkeypatch)
    client = GuardedClient([])
    result = apply_auto_open([candidate("NEW", "Long")], [], mode="paper_autopilot", engine=engine, config=auto_cfg(), alpaca_cfg=trade_cfg(), client=client, now=NOW)
    assert result["autopilot_open_submitted"] == 1
    assert client.submitted
