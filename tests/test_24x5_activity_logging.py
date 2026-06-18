from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, select

from stockml.autopilot.open import apply_auto_open, AutoOpenConfig
from stockml.db.schema import create_all, position_events
from stockml.services import events
from stockml.trading.config import AlpacaConfig


class FakeClient:
    def get_account(self):
        return {"equity": "100000"}
    def get_asset(self, symbol):
        return {"tradable": True, "status": "active", "shortable": True, "overnight_tradable": True}
    def submit_order(self, order):
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
        overnight_trading_enabled=True,
        overnight_limit_buffer_bps=50.0,
    )


def auto_cfg():
    return AutoOpenConfig(open_enabled=True, max_auto_opens_per_day=5, max_positions=5, default_position_value_cap_usd=1000, default_position_pct_of_equity=0.01, max_single_position_pct_of_equity=0.05, holding_review_gate_enabled=False)


def _engine(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    monkeypatch.setattr(events, "get_engine", lambda required=False: engine)
    return engine


def test_24x5_candidate_submitted_event_includes_session_mode_and_extended_hours(monkeypatch):
    engine = _engine(monkeypatch)
    result = apply_auto_open(
        [{"symbol": "AAA", "promotion_score": 1, "nightly_bias": "long", "current_price": 10, "trade_action": "Long", "meta_label_decision": "Take Trade", "trade_quality_status": "approved"}],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=auto_cfg(),
        alpaca_cfg=trade_cfg(),
        client=FakeClient(),
        now=datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc),
    )
    assert result["autopilot_open_submitted"] == 1
    with engine.connect() as conn:
        rows = conn.execute(select(position_events).where(position_events.c.event_type == "candidate_submitted")).mappings().all()
    assert len(rows) == 1
    assert rows[0]["details"]["session_mode"] == "24x5"
    assert rows[0]["details"]["extended_hours"] is True


def test_24x5_candidate_blocked_event_includes_block_reason(monkeypatch):
    engine = _engine(monkeypatch)
    result = apply_auto_open(
        [{"symbol": "BBB", "promotion_score": 1, "nightly_bias": "long", "current_price": 10}],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=auto_cfg(),
        alpaca_cfg=trade_cfg(),
        client=FakeClient(),
        now=datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc),
    )
    assert result["autopilot_open_blocked"] == 1
    with engine.connect() as conn:
        rows = conn.execute(select(position_events).where(position_events.c.event_type == "candidate_skipped_meta_label")).mappings().all()
    assert len(rows) == 1
    assert rows[0]["details"]["block_reason"] == "model_evidence_missing"
