from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, insert, select

from stockml.autopilot.open import AutoOpenConfig, apply_auto_open, position_size_usd
from stockml.db.schema import autopilot_open_log, create_all, kill_switch_events
from stockml.trading import paper_autopilot
from stockml.trading.config import AlpacaConfig


def _trade_config(**overrides) -> AlpacaConfig:
    values = dict(
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
        account_equity=1000,
        max_position_pct=0.2,
        allow_short_selling=False,
        live_trading_enabled=False,
        paper_trading_enabled=True,
    )
    values.update(overrides)
    return AlpacaConfig(**values)


class FakeClient:
    def __init__(self, equity: str = "1000"):
        self.equity = equity
        self.orders: list[dict] = []

    def get_account(self) -> dict:
        return {"equity": self.equity}

    def submit_order(self, order: dict) -> dict:
        self.orders.append(order)
        return {"id": f"order-{order['symbol']}", "status": "accepted"}


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    return engine


def _candidate(symbol: str = "CSTL", score: float = 0.72, bias: str = "long") -> dict:
    return {
        "symbol": symbol,
        "promotion_score": score,
        "nightly_bias": bias,
        "is_held": False,
        "details": {"is_first_15_min": False, "is_last_30_min": False},
    }


def test_position_size_respects_account_floor_and_caps():
    config = AutoOpenConfig(open_enabled=True)

    assert position_size_usd(200, config) == 0
    assert position_size_usd(1000, config) == 100
    assert position_size_usd(5000, config) == 200


def test_auto_open_is_disabled_by_default_and_writes_no_order():
    engine = _engine()
    client = FakeClient()

    result = apply_auto_open(
        [_candidate()],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=False),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_notes"] == "auto_open_disabled"
    assert client.orders == []


def test_auto_open_submits_paper_order_and_logs_opened():
    engine = _engine()
    client = FakeClient()

    result = apply_auto_open(
        [_candidate("CSTL", 0.71)],
        [{"symbol": "CCOI"}],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, max_positions=5),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 1
    assert client.orders[0]["symbol"] == "CSTL"
    assert client.orders[0]["side"] == "buy"
    assert client.orders[0]["notional"] == "100.0"
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["symbol"] == "CSTL"
    assert row["verdict"] == "opened"
    assert row["order_id"] == "order-CSTL"


def test_auto_open_skips_held_symbols_and_opens_next_candidate():
    engine = _engine()
    client = FakeClient()

    result = apply_auto_open(
        [_candidate("CCOI", 0.9), _candidate("CSTL", 0.8)],
        [{"symbol": "CCOI"}],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, max_positions=5),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 1
    assert client.orders[0]["symbol"] == "CSTL"


def test_auto_open_respects_daily_cap():
    engine = _engine()
    now = datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        for symbol in ["AAA", "BBB", "CCC"]:
            conn.execute(
                insert(autopilot_open_log).values(
                    logged_at=now,
                    symbol=symbol,
                    promotion_score=0.8,
                    size_usd=100,
                    verdict="opened",
                    details={},
                )
            )

    result = apply_auto_open(
        [_candidate("CSTL")],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, max_auto_opens_per_day=3),
        alpaca_cfg=_trade_config(),
        client=FakeClient(),
        now=now,
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_notes"] == "auto_open_cap_or_basket_full"


def test_auto_open_blocks_when_kill_switch_active():
    engine = _engine()
    now = datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            insert(kill_switch_events).values(
                switch_name="daily.realized_plus_unrealized_loss_usd",
                event_type="tripped",
                occurred_at=now,
                payload={"current": -40, "threshold": -30},
            )
        )

    result = apply_auto_open(
        [_candidate()],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True),
        alpaca_cfg=_trade_config(),
        client=FakeClient(),
        now=now,
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_notes"] == "kill_switch_active"


def test_paper_autopilot_tick_invokes_auto_open_when_idle(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _trade_config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([]).to_csv(tracking, index=False)
    pd.DataFrame([{"symbol": "CCOI", "qty": 1}]).to_csv(positions, index=False)
    calls = []

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 0, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        strong_candidate_loader=lambda: [_candidate("CSTL")],
        auto_open_applier=lambda candidates, open_positions, mode: calls.append((candidates, open_positions, mode))
        or {
            "autopilot_open_attempted": 1,
            "autopilot_open_submitted": 1,
            "autopilot_open_blocked": 0,
            "autopilot_open_notes": "CSTL:opened:order-CSTL",
        },
    )

    assert calls and calls[0][0][0]["symbol"] == "CSTL"
    assert calls[0][2] == "paper_autopilot"
    assert state["phase"] == "waiting_for_fills"
    assert state["autopilot_open_submitted"] == 1


def test_paper_autopilot_tick_does_not_auto_open_during_eod(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _trade_config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([]).to_csv(tracking, index=False)
    pd.DataFrame([{"symbol": "CCOI", "qty": 1}]).to_csv(positions, index=False)
    calls = []

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 0, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        eod_runner=lambda frame, current_state, open_orders: {
            "eod_state": "review",
            "eod_actions": 0,
            "eod_flatten_submitted": 0,
            "eod_remaining": 1,
            "eod_banner": "EOD review running.",
            "eod_action_notes": "",
        },
        strong_candidate_loader=lambda: calls.append("loaded") or [_candidate("CSTL")],
        auto_open_applier=lambda candidates, open_positions, mode: {"autopilot_open_submitted": 1},
    )

    assert calls == []
    assert state["phase"] == "monitoring_positions"
    assert state["autopilot_open_submitted"] == 0
    assert state["eod_state"] == "review"
