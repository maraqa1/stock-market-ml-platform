from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, insert, select

from stockml.autopilot.open import (
    AutoOpenConfig,
    apply_auto_open,
    latest_flat_account_fallback_candidates,
    latest_near_miss_fallback_candidates,
    load_auto_open_config,
    position_size_usd,
    set_auto_open_enabled,
    set_auto_open_max_per_day,
)
from stockml.db.schema import autopilot_open_log, create_all, intraday_candidate_snapshots, intraday_promotion_log, kill_switch_events
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


def _fallback_candidate(symbol: str = "ANGI", score: float = 0.4175, bias: str = "long") -> dict:
    candidate = _candidate(symbol, score, bias)
    candidate["details"] = {
        "flat_account_fallback": True,
        "fallback_reason": "flat_account_no_strong_promotions",
        "is_first_15_min": False,
        "is_last_30_min": False,
    }
    return candidate


def test_position_size_respects_account_floor_and_caps():
    config = AutoOpenConfig(open_enabled=True)

    assert position_size_usd(200, config) == 0
    assert position_size_usd(1000, config) == 100
    assert position_size_usd(5000, config) == 200


def test_auto_open_feature_toggle_persists_to_root_config(tmp_path):
    enabled = set_auto_open_enabled(True, root=tmp_path)

    assert enabled.open_enabled is True
    assert load_auto_open_config(root=tmp_path).open_enabled is True

    disabled = set_auto_open_enabled(False, root=tmp_path)

    assert disabled.open_enabled is False
    assert load_auto_open_config(root=tmp_path).open_enabled is False


def test_auto_open_config_handles_malformed_yaml_without_crashing(tmp_path):
    path = tmp_path / "config" / "autopilot.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("version: 1\n<<<<<<< Updated upstream\n", encoding="utf-8")

    config = load_auto_open_config(root=tmp_path)

    assert config.open_enabled is False
    assert config.near_miss_fallback_enabled is True


def test_auto_open_daily_cap_persists_to_root_config(tmp_path):
    config = set_auto_open_max_per_day(8, root=tmp_path)

    assert config.max_auto_opens_per_day == 8
    assert load_auto_open_config(root=tmp_path).max_auto_opens_per_day == 8

    clamped = set_auto_open_max_per_day(99, root=tmp_path)

    assert clamped.max_auto_opens_per_day == 20


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


def test_flat_account_fallback_candidates_select_best_confirmed_watch():
    engine = _engine()
    now = datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        angie = conn.execute(
            insert(intraday_candidate_snapshots).values(
                snapshot_at=now,
                bar_close_at=now,
                symbol="ANGI",
                nightly_score=0.3375,
                nightly_bias="long",
                is_held=False,
                bid=5.26,
                ask=5.27,
                last_price=5.145,
                spread_bps=19.03,
                dollar_volume_today=1_162_570.77,
                trend_5m_pct=1.2658,
                trend_15m_pct=1.4634,
                distance_from_vwap_bps=76.81,
                intraday_range_position=1.0,
                status="ok",
                details={},
            )
        ).inserted_primary_key[0]
        weak = conn.execute(
            insert(intraday_candidate_snapshots).values(
                snapshot_at=now,
                bar_close_at=now,
                symbol="CSCO",
                nightly_score=-0.0013,
                nightly_bias="short",
                is_held=False,
                spread_bps=3.04,
                dollar_volume_today=231_575_952.46,
                status="ok",
                details={},
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(intraday_promotion_log),
            [
                {
                    "logged_at": now,
                    "snapshot_id": angie,
                    "symbol": "ANGI",
                    "verdict": "watch",
                    "promotion_score": 0.4175,
                    "contributing": [
                        "long_trend_5m_positive",
                        "long_trend_15m_positive",
                        "long_above_vwap_floor",
                        "long_range_position_confirmed",
                        "long_market_aligned",
                    ],
                },
                {
                    "logged_at": now,
                    "snapshot_id": weak,
                    "symbol": "CSCO",
                    "verdict": "watch",
                    "promotion_score": 0.0013,
                    "contributing": [
                        "short_trend_15m_negative",
                        "short_below_vwap_ceiling",
                        "short_range_position_confirmed",
                        "short_market_aligned",
                    ],
                },
            ],
        )

    candidates = latest_flat_account_fallback_candidates(
        engine=engine,
        config=AutoOpenConfig(flat_account_fallback_min_score=0.40),
    )

    assert [candidate["symbol"] for candidate in candidates] == ["ANGI"]
    assert candidates[0]["details"]["flat_account_fallback"] is True


def test_near_miss_fallback_candidates_select_configured_near_misses(tmp_path):
    directory = tmp_path / "data" / "trading" / "near_miss"
    directory.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "symbol": "GLIBK",
                "side": "buy",
                "status": "rejected",
                "failed_gate": "risk_adjusted_score_below_threshold",
                "failed_gate_label": "Risk-adjusted score below threshold",
                "actual_value": 0.00499,
                "required_value": 0.005,
                "distance_to_pass": 0.00001,
                "distance_pct": 0.002,
                "severity": "near_miss",
                "reason": "Risk-adjusted score below threshold",
                "risk_adjusted_score": 0.00499,
            },
            {
                "symbol": "FIP",
                "side": "buy",
                "status": "rejected",
                "failed_gate": "price_below_minimum",
                "failed_gate_label": "Price below minimum",
                "distance_pct": 0.02,
                "severity": "near_miss",
            },
            {
                "symbol": "ANGI",
                "side": "buy",
                "status": "rejected",
                "failed_gate": "market_cap_below_minimum",
                "failed_gate_label": "Market cap below minimum",
                "distance_pct": 0.30,
                "severity": "hard_fail",
            },
        ]
    ).to_csv(directory / "near_miss_20260513_105212.csv", index=False)

    candidates = latest_near_miss_fallback_candidates(
        root=tmp_path,
        config=AutoOpenConfig(near_miss_fallback_enabled=True),
    )

    assert [candidate["symbol"] for candidate in candidates] == ["GLIBK"]
    assert candidates[0]["details"]["near_miss_fallback"] is True
    assert candidates[0]["details"]["failed_gate"] == "risk_adjusted_score_below_threshold"


def test_near_miss_fallback_candidates_include_moderate_gaps_without_hard_safety_fails(tmp_path):
    directory = tmp_path / "data" / "trading" / "near_miss"
    directory.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "symbol": "DOO",
                "side": "buy",
                "status": "rejected",
                "failed_gate": "risk_adjusted_score_below_threshold",
                "failed_gate_label": "Risk-adjusted score below threshold",
                "actual_value": 0.0042,
                "required_value": 0.005,
                "distance_to_pass": 0.0008,
                "distance_pct": 0.16,
                "severity": "moderate_gap",
                "reason": "Risk-adjusted score below threshold",
                "risk_adjusted_score": 0.0042,
            },
            {
                "symbol": "EMBC",
                "side": "buy",
                "status": "rejected",
                "failed_gate": "volatility_extreme",
                "failed_gate_label": "Volatility extreme",
                "actual_value": 0.13,
                "required_value": 0.12,
                "distance_pct": 0.08,
                "severity": "near_miss",
                "reason": "Price below minimum; Volatility extreme",
                "risk_adjusted_score": 0.22,
            },
            {
                "symbol": "EMBC",
                "side": "buy",
                "status": "rejected",
                "failed_gate": "price_below_minimum",
                "failed_gate_label": "Price below minimum",
                "actual_value": 3.41,
                "required_value": 5.0,
                "distance_pct": 0.32,
                "severity": "hard_fail",
                "reason": "Price below minimum; Volatility extreme",
                "risk_adjusted_score": 0.22,
            },
        ]
    ).to_csv(directory / "near_miss_20260513_180202.csv", index=False)

    candidates = latest_near_miss_fallback_candidates(root=tmp_path, config=AutoOpenConfig(near_miss_fallback_enabled=True))

    assert [candidate["symbol"] for candidate in candidates] == ["DOO"]
    assert candidates[0]["details"]["severity"] == "moderate_gap"


def test_auto_open_uses_reduced_size_for_flat_account_fallback():
    engine = _engine()
    client = FakeClient()

    result = apply_auto_open(
        [_fallback_candidate("ANGI")],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, flat_account_fallback_size_multiplier=0.50),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 1
    assert client.orders[0]["symbol"] == "ANGI"
    assert client.orders[0]["notional"] == "50.0"
    assert "ANGI:fallback_opened:order-ANGI" in result["autopilot_open_notes"]
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["size_usd"] == 50
    assert row["details"]["flat_account_fallback"] is True


def test_auto_open_uses_smaller_size_for_near_miss_fallback():
    engine = _engine()
    client = FakeClient()
    candidate = _candidate("GLIBK")
    candidate["details"] = {
        "near_miss_fallback": True,
        "fallback_reason": "near_miss_diagnostic_candidate",
        "is_first_15_min": False,
        "is_last_30_min": False,
    }

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, near_miss_fallback_size_multiplier=0.50),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 1
    assert client.orders[0]["symbol"] == "GLIBK"
    assert client.orders[0]["notional"] == "50.0"
    assert "GLIBK:near_miss_opened:order-GLIBK" in result["autopilot_open_notes"]
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["details"]["near_miss_fallback"] is True


def test_auto_open_near_miss_can_fill_remaining_slot_when_position_is_open():
    engine = _engine()
    client = FakeClient()
    candidate = _candidate("GLIBK")
    candidate["details"] = {
        "near_miss_fallback": True,
        "fallback_reason": "near_miss_diagnostic_candidate",
        "is_first_15_min": False,
        "is_last_30_min": False,
    }

    result = apply_auto_open(
        [candidate],
        [{"symbol": "ANGI"}],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, max_positions=5, near_miss_fallback_requires_flat_account=False, near_miss_fallback_size_multiplier=0.50),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 1
    assert client.orders[0]["symbol"] == "GLIBK"
    assert "GLIBK:near_miss_opened:order-GLIBK" in result["autopilot_open_notes"]


def test_auto_open_near_miss_can_still_require_flat_account_when_configured():
    engine = _engine()
    client = FakeClient()
    candidate = _candidate("GLIBK")
    candidate["details"] = {"near_miss_fallback": True}

    result = apply_auto_open(
        [candidate],
        [{"symbol": "ANGI"}],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, near_miss_fallback_requires_flat_account=True),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert result["autopilot_open_notes"] == "GLIBK:blocked:near_miss_requires_flat_account"
    assert client.orders == []


def test_auto_open_fallback_requires_flat_account():
    engine = _engine()
    client = FakeClient()

    result = apply_auto_open(
        [_fallback_candidate("ANGI")],
        [{"symbol": "CCOI"}],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert result["autopilot_open_notes"] == "ANGI:blocked:fallback_requires_flat_account"
    assert client.orders == []


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
        eod_runner=lambda frame, current_state, open_orders: {
            "eod_state": "inactive",
            "eod_actions": 0,
            "eod_flatten_submitted": 0,
            "eod_remaining": len(frame),
            "eod_banner": "",
            "eod_action_notes": "",
        },
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


def test_paper_autopilot_tick_uses_flat_fallback_when_account_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _trade_config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([]).to_csv(tracking, index=False)
    pd.DataFrame([]).to_csv(positions, index=False)
    calls = []

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 0, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        strong_candidate_loader=lambda: [],
        fallback_candidate_loader=lambda: [_fallback_candidate("ANGI")],
        auto_open_applier=lambda candidates, open_positions, mode: calls.append((candidates, open_positions, mode))
        or {
            "autopilot_open_attempted": 1,
            "autopilot_open_submitted": 1,
            "autopilot_open_blocked": 0,
            "autopilot_open_notes": "ANGI:fallback_opened:order-ANGI",
        },
    )

    assert calls and calls[0][0][0]["symbol"] == "ANGI"
    assert calls[0][1] == []
    assert state["phase"] == "waiting_for_fills"
    assert state["autopilot_open_submitted"] == 1


def test_paper_autopilot_tick_prefers_near_miss_before_flat_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _trade_config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([]).to_csv(tracking, index=False)
    pd.DataFrame([]).to_csv(positions, index=False)
    calls = []

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 0, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        strong_candidate_loader=lambda: [],
        fallback_candidate_loader=lambda: [_fallback_candidate("ANGI")],
        near_miss_candidate_loader=lambda: [_candidate("GLIBK")],
        auto_open_applier=lambda candidates, open_positions, mode: calls.append((candidates, open_positions, mode))
        or {
            "autopilot_open_attempted": 1,
            "autopilot_open_submitted": 1,
            "autopilot_open_blocked": 0,
            "autopilot_open_notes": "GLIBK:near_miss_opened:order-GLIBK",
        },
    )

    assert calls and calls[0][0][0]["symbol"] == "GLIBK"
    assert all(candidate["symbol"] != "ANGI" for candidate in calls[0][0])
    assert calls[0][1] == []
    assert state["phase"] == "waiting_for_fills"
    assert state["autopilot_open_submitted"] == 1


def test_paper_autopilot_tick_uses_near_miss_when_position_is_open(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _trade_config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([]).to_csv(tracking, index=False)
    pd.DataFrame([{"symbol": "ANGI", "qty": 10, "unrealized_plpc": 0.0}]).to_csv(positions, index=False)
    calls = []

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 0, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        strong_candidate_loader=lambda: [],
        fallback_candidate_loader=lambda: [_fallback_candidate("ANGI")],
        near_miss_candidate_loader=lambda: [_candidate("GLIBK")],
        auto_open_applier=lambda candidates, open_positions, mode: calls.append((candidates, open_positions, mode))
        or {
            "autopilot_open_attempted": 1,
            "autopilot_open_submitted": 1,
            "autopilot_open_blocked": 0,
            "autopilot_open_notes": "GLIBK:near_miss_opened:order-GLIBK",
        },
    )

    assert calls and calls[0][0][0]["symbol"] == "GLIBK"
    assert calls[0][1][0]["symbol"] == "ANGI"
    assert state["phase"] == "waiting_for_fills"
    assert state["autopilot_open_submitted"] == 1


def test_paper_autopilot_tick_skips_auto_open_when_market_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _trade_config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([]).to_csv(tracking, index=False)
    pd.DataFrame([]).to_csv(positions, index=False)
    calls = []

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 0, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        strong_candidate_loader=lambda: [_candidate("GLIBK")],
        fallback_candidate_loader=lambda: [_fallback_candidate("ANGI")],
        auto_open_applier=lambda candidates, open_positions, mode: calls.append((candidates, open_positions, mode)),
        allow_auto_open=False,
    )

    assert calls == []
    assert state["autopilot_open_submitted"] == 0
    assert state["autopilot_open_notes"] == "auto_open_skipped_market_closed"


def test_paper_autopilot_tick_refreshes_positions_after_auto_open_fill(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _trade_config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking_before = tmp_path / "tracking-before.csv"
    positions_before = tmp_path / "positions-before.csv"
    tracking_after = tmp_path / "tracking-after.csv"
    positions_after = tmp_path / "positions-after.csv"
    pd.DataFrame([]).to_csv(tracking_before, index=False)
    pd.DataFrame([]).to_csv(positions_before, index=False)
    pd.DataFrame([{"symbol": "ANGI", "alpaca_status": "filled"}]).to_csv(tracking_after, index=False)
    pd.DataFrame([{"symbol": "ANGI", "qty": 10, "unrealized_plpc": 0.0}]).to_csv(positions_after, index=False)
    refreshes = [
        {"orders_tracked": 0, "tracking_path": tracking_before, "positions_path": positions_before},
        {"orders_tracked": 1, "tracking_path": tracking_after, "positions_path": positions_after},
    ]

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: refreshes.pop(0),
        broker_open_orders_func=lambda cfg: 0,
        strong_candidate_loader=lambda: [],
        fallback_candidate_loader=lambda: [_fallback_candidate("ANGI")],
        auto_open_applier=lambda candidates, open_positions, mode: {
            "autopilot_open_attempted": 1,
            "autopilot_open_submitted": 1,
            "autopilot_open_blocked": 0,
            "autopilot_open_notes": "ANGI:fallback_opened:order-ANGI",
        },
    )

    assert state["phase"] == "monitoring_positions"
    assert state["open_orders"] == 0
    assert state["open_positions"] == 1
    assert state["orders_tracked"] == 1
    assert state["positions_path"] == str(positions_after)


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
