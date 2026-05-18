from __future__ import annotations

from datetime import datetime, timezone
import os

import pandas as pd
import requests
from sqlalchemy import create_engine, insert, select

from stockml.autopilot.open import (
    AutoOpenConfig,
    apply_auto_open,
    latest_flat_account_fallback_candidates,
    latest_near_miss_fallback_candidates,
    latest_per_symbol_forecast_fallback_candidates,
    load_auto_open_config,
    position_size_usd,
    ranked_fallback_candidates,
    set_auto_open_enabled,
    set_auto_open_limit_values,
    set_auto_open_max_per_day,
    set_auto_open_strategy_values,
    set_per_symbol_forecast_fallback_max_per_day,
)
from stockml.db.schema import autopilot_open_log, create_all, intraday_candidate_snapshots, intraday_promotion_log, kill_switch_events
from stockml.trading.alpaca_client import AlpacaAPIError
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
    def __init__(self, equity: str = "1000", asset: dict | None = None):
        self.equity = equity
        self.asset = asset or {"tradable": True, "status": "active", "fractionable": True, "shortable": True}
        self.orders: list[dict] = []

    def get_account(self) -> dict:
        return {"equity": self.equity}

    def get_asset(self, symbol: str) -> dict:
        return dict(self.asset)

    def submit_order(self, order: dict) -> dict:
        self.orders.append(order)
        return {"id": f"order-{order['symbol']}", "status": "accepted"}


class RejectingClient(FakeClient):
    def submit_order(self, order: dict) -> dict:
        self.orders.append(order)
        response = requests.Response()
        response.status_code = 403
        response._content = b'{"code":40310000,"message":"asset is not shortable"}'
        raise AlpacaAPIError("POST", "https://paper-api.alpaca.markets/v2/orders", response)


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    return engine


def _candidate(symbol: str = "CSTL", score: float = 0.72, bias: str = "long") -> dict:
    return {
        "symbol": symbol,
        "promotion_score": score,
        "nightly_bias": bias,
        "current_price": 10,
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

    assert clamped.max_auto_opens_per_day == 99


def test_per_symbol_forecast_fallback_daily_cap_persists_to_root_config(tmp_path):
    config = set_per_symbol_forecast_fallback_max_per_day(9, root=tmp_path)

    assert config.per_symbol_forecast_fallback_max_per_day == 9
    assert load_auto_open_config(root=tmp_path).per_symbol_forecast_fallback_max_per_day == 9

    clamped = set_per_symbol_forecast_fallback_max_per_day(999, root=tmp_path)

    assert clamped.per_symbol_forecast_fallback_max_per_day == 100


def test_auto_open_limit_values_persist_and_clamp_to_portal_max(tmp_path):
    config = set_auto_open_limit_values(
        {
            "max_auto_opens_per_day": 101,
            "max_positions": 12,
            "flat_account_fallback_max_per_day": 4,
            "near_miss_fallback_max_per_day": 6,
            "per_symbol_forecast_fallback_max_per_day": 8,
            "ignored_limit": 50,
        },
        root=tmp_path,
    )

    assert config.max_auto_opens_per_day == 100
    assert config.max_positions == 12
    assert config.flat_account_fallback_max_per_day == 4
    assert config.near_miss_fallback_max_per_day == 6
    assert config.per_symbol_forecast_fallback_max_per_day == 8


def test_auto_open_strategy_values_persist_to_root_config(tmp_path):
    config = set_auto_open_strategy_values(
        {
            "max_position_loss_pct": "1.5",
            "signal_flip_confirmation_clocks": "2",
            "stale_unknown_loss_close_pct": "1.25",
            "trailing_profit_arm_pct": "2.5",
            "trailing_profit_giveback_pct": "0.75",
            "basket_drawdown_pause_pct": "1.75",
            "max_long_positions": "6",
            "max_short_positions": "4",
            "near_miss_entries_with_open_positions": "true",
            "close_automation_mode": "automatic",
        },
        root=tmp_path,
    )

    assert config.max_position_loss_pct == 1.5
    assert config.signal_flip_confirmation_clocks == 2
    assert config.stale_unknown_loss_close_pct == 1.25
    assert config.trailing_profit_arm_pct == 2.5
    assert config.trailing_profit_giveback_pct == 0.75
    assert config.basket_drawdown_pause_pct == 1.75
    assert config.max_long_positions == 6
    assert config.max_short_positions == 4
    assert config.near_miss_entries_with_open_positions is True
    assert config.close_automation_mode == "automatic"
    assert load_auto_open_config(root=tmp_path).close_automation_mode == "automatic"


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
    assert client.orders[0]["qty"] == "10"
    assert "notional" not in client.orders[0]
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


def test_per_symbol_forecast_fallback_candidates_select_most_profitable_confirmed(tmp_path):
    directory = tmp_path / "data" / "trading" / "per_symbol_forecast"
    directory.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "symbol": "LOWP",
                "side": "buy",
                "current_trade_action": "Long",
                "forecast_confirmation": "confirmed",
                "confirmation_score": 95,
                "confirmation_reason": "side_aligned;magnitude_ok;profitability_ok;risk_reward_ok",
                "side_alignment": "aligned",
                "expected_profitability_score": 10,
                "expected_move_bps": 60,
                "magnitude_bucket": "medium",
                "direction_context": "long_bias",
                "profitability_ok": True,
                "risk_reward_ok": True,
                "liquidity_ok": True,
                "volatility_ok": True,
            },
            {
                "symbol": "HIGHP",
                "side": "buy",
                "current_trade_action": "Long",
                "forecast_confirmation": "confirmed",
                "confirmation_score": 90,
                "confirmation_reason": "side_aligned;magnitude_ok;profitability_ok;risk_reward_ok",
                "side_alignment": "aligned",
                "expected_profitability_score": 100,
                "expected_move_bps": 150,
                "magnitude_bucket": "large",
                "direction_context": "long_bias",
                "profitability_ok": True,
                "risk_reward_ok": True,
                "liquidity_ok": True,
                "volatility_ok": True,
            },
            {
                "symbol": "CONFLICT",
                "side": "buy",
                "current_trade_action": "Long",
                "forecast_confirmation": "conflicted",
                "confirmation_score": 60,
                "side_alignment": "conflicted",
                "expected_profitability_score": 200,
                "risk_reward_ok": True,
            },
        ]
    ).to_csv(directory / "per_symbol_forecast_20260514_120000.csv", index=False)

    candidates = latest_per_symbol_forecast_fallback_candidates(
        root=tmp_path,
        config=AutoOpenConfig(per_symbol_forecast_fallback_enabled=True),
    )

    assert [candidate["symbol"] for candidate in candidates[:2]] == ["HIGHP", "LOWP"]
    assert candidates[0]["details"]["per_symbol_forecast_fallback"] is True
    assert candidates[0]["details"]["fallback_reason"] == "per_symbol_forecast_confirmed_candidate"
    assert candidates[0]["promotion_score"] == 100


def test_per_symbol_forecast_fallback_candidates_require_quality_flags(tmp_path):
    directory = tmp_path / "data" / "trading" / "per_symbol_forecast"
    directory.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "symbol": "MISSQ",
                "side": "buy",
                "current_trade_action": "Long",
                "forecast_confirmation": "confirmed",
                "confirmation_score": 100,
                "confirmation_reason": "side_aligned;magnitude_ok;profitability_ok;risk_reward_ok",
                "side_alignment": "aligned",
                "expected_profitability_score": 100,
                "expected_move_bps": 100,
                "risk_reward_ok": True,
            },
            {
                "symbol": "BADSCALE",
                "side": "buy",
                "current_trade_action": "Long",
                "forecast_confirmation": "confirmed",
                "confirmation_score": 100,
                "confirmation_reason": "side_aligned;magnitude_ok;profitability_ok;risk_reward_ok",
                "side_alignment": "aligned",
                "expected_profitability_score": 500,
                "expected_move_bps": 500,
                "profitability_ok": True,
                "risk_reward_ok": True,
                "liquidity_ok": True,
                "volatility_ok": True,
            },
        ]
    ).to_csv(directory / "per_symbol_forecast_20260514_120000.csv", index=False)

    candidates = latest_per_symbol_forecast_fallback_candidates(
        root=tmp_path,
        config=AutoOpenConfig(per_symbol_forecast_fallback_enabled=True),
    )

    assert candidates == []


def test_per_symbol_forecast_fallback_candidates_include_qualified_shorts(tmp_path):
    directory = tmp_path / "data" / "trading" / "per_symbol_forecast"
    directory.mkdir(parents=True)
    rows = []
    for idx in range(1, 6):
        rows.append(
            {
                "symbol": f"LONG{idx}",
                "side": "buy",
                "current_trade_action": "Long",
                "forecast_confirmation": "confirmed",
                "confirmation_score": 100,
                "confirmation_reason": "side_aligned;magnitude_ok;profitability_ok;risk_reward_ok",
                "side_alignment": "aligned",
                "expected_profitability_score": 200 - idx,
                "expected_move_bps": 100,
                "profitability_ok": True,
                "risk_reward_ok": True,
                "liquidity_ok": True,
                "volatility_ok": True,
            }
        )
    rows.append(
        {
            "symbol": "SHORTY",
            "side": "sell",
            "current_trade_action": "Short",
            "forecast_confirmation": "confirmed",
            "confirmation_score": 100,
            "confirmation_reason": "side_aligned;magnitude_ok;profitability_ok;risk_reward_ok",
            "side_alignment": "aligned",
            "expected_profitability_score": 50,
            "expected_move_bps": 100,
            "profitability_ok": True,
            "risk_reward_ok": True,
            "liquidity_ok": True,
            "volatility_ok": True,
        }
    )
    pd.DataFrame(rows).to_csv(directory / "per_symbol_forecast_20260514_120000.csv", index=False)

    candidates = latest_per_symbol_forecast_fallback_candidates(
        root=tmp_path,
        config=AutoOpenConfig(per_symbol_forecast_fallback_enabled=True),
        limit=5,
    )

    assert "SHORTY" in [candidate["symbol"] for candidate in candidates]
    short = next(candidate for candidate in candidates if candidate["symbol"] == "SHORTY")
    assert short["nightly_bias"] == "short"
    assert short["current_trade_action"] == "Short"
    assert short["side"] == "sell"
    assert short["details"]["current_trade_action"] == "Short"
    assert short["details"]["side"] == "sell"
    assert short["details"]["nightly_bias"] == "short"


def test_ranked_fallback_candidates_blends_forecast_and_near_miss_strength():
    weak_forecast = _candidate("LOWP", 10)
    weak_forecast["details"] = {
        "per_symbol_forecast_fallback": True,
        "expected_profitability_score": 10,
        "confirmation_score": 90,
        "expected_move_bps": 60,
        "profitability_ok": True,
        "risk_reward_ok": True,
        "liquidity_ok": True,
        "volatility_ok": True,
    }
    strong_forecast = _candidate("HIGHP", 100)
    strong_forecast["details"] = {
        "per_symbol_forecast_fallback": True,
        "expected_profitability_score": 100,
        "confirmation_score": 90,
        "expected_move_bps": 150,
        "profitability_ok": True,
        "risk_reward_ok": True,
        "liquidity_ok": True,
        "volatility_ok": True,
    }
    near_miss = _candidate("GLIBK", 0.0049)
    near_miss["details"] = {
        "near_miss_fallback": True,
        "distance_pct": 0.001,
        "severity": "near_miss",
    }

    ranked = ranked_fallback_candidates([weak_forecast, strong_forecast], [near_miss])

    assert [candidate["symbol"] for candidate in ranked] == ["HIGHP", "GLIBK", "LOWP"]
    assert ranked[0]["details"]["candidate_source"] == "per_symbol_forecast"
    assert ranked[1]["details"]["candidate_source"] == "near_miss"


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


def test_near_miss_fallback_ignores_stale_analysis_file(tmp_path):
    directory = tmp_path / "data" / "trading" / "near_miss"
    directory.mkdir(parents=True)
    path = directory / "near_miss_20260513_180202.csv"
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
        ]
    ).to_csv(path, index=False)
    stale_stamp = datetime(2026, 5, 14, 13, 0, tzinfo=timezone.utc).timestamp()
    os.utime(path, (stale_stamp, stale_stamp))

    candidates = latest_near_miss_fallback_candidates(
        root=tmp_path,
        config=AutoOpenConfig(near_miss_fallback_enabled=True, near_miss_fallback_max_file_age_minutes=30),
        now=datetime(2026, 5, 14, 14, 0, tzinfo=timezone.utc),
    )

    assert candidates == []


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
    assert client.orders[0]["qty"] == "5"
    assert "notional" not in client.orders[0]
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
    assert client.orders[0]["qty"] == "5"
    assert "notional" not in client.orders[0]
    assert "GLIBK:near_miss_opened:order-GLIBK" in result["autopilot_open_notes"]
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["details"]["near_miss_fallback"] is True


def test_auto_open_uses_per_symbol_forecast_size_and_log_prefix():
    engine = _engine()
    client = FakeClient()
    candidate = _candidate("HIGHP")
    candidate["details"] = {
        "per_symbol_forecast_fallback": True,
        "fallback_reason": "per_symbol_forecast_confirmed_candidate",
        "expected_profitability_score": 100,
        "profitability_ok": True,
        "risk_reward_ok": True,
        "liquidity_ok": True,
        "volatility_ok": True,
        "current_price": 10,
        "is_first_15_min": False,
        "is_last_30_min": False,
    }

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, per_symbol_forecast_fallback_size_multiplier=0.50),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 1
    assert client.orders[0]["symbol"] == "HIGHP"
    assert client.orders[0]["qty"] == "5"
    assert "notional" not in client.orders[0]
    assert "HIGHP:per_symbol_forecast_opened:order-HIGHP" in result["autopilot_open_notes"]
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["details"]["per_symbol_forecast_fallback"] is True


def test_auto_open_blocks_out_of_range_forecast_profitability_score():
    engine = _engine()
    client = FakeClient()
    candidate = _candidate("CHTR", 500.066)
    candidate["details"] = {
        "per_symbol_forecast_fallback": True,
        "fallback_reason": "per_symbol_forecast_confirmed_candidate",
        "expected_profitability_score": 500.066,
        "profitability_ok": True,
        "risk_reward_ok": True,
        "liquidity_ok": True,
        "volatility_ok": True,
        "current_price": 10,
        "is_first_15_min": False,
        "is_last_30_min": False,
    }

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 15, 14, 41, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert "CHTR:blocked:profitability_score_out_of_range" in result["autopilot_open_notes"]
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["verdict"] == "blocked"
    assert row["promotion_score"] is None
    assert row["block_reason"] == "profitability_score_out_of_range"
    assert row["details"]["quality_gate_status"] == "blocked"
    assert row["details"]["expected_profitability_score"] == 500.066


def test_auto_open_blocks_forecast_candidate_with_missing_quality_flags():
    engine = _engine()
    client = FakeClient()
    candidate = _candidate("MISSQ", 100)
    candidate["details"] = {
        "per_symbol_forecast_fallback": True,
        "fallback_reason": "per_symbol_forecast_confirmed_candidate",
        "expected_profitability_score": 100,
        "risk_reward_ok": True,
        "is_first_15_min": False,
        "is_last_30_min": False,
    }

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 15, 14, 41, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert "MISSQ:blocked:profitability_ok_not_evaluated" in result["autopilot_open_notes"]
    assert client.orders == []
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["block_reason"] == "profitability_ok_not_evaluated"


def test_auto_open_submits_short_when_shorting_enabled():
    engine = _engine()
    client = FakeClient()
    candidate = _candidate("SHORTY", 100, bias="short")
    candidate["details"] = {
        "per_symbol_forecast_fallback": True,
        "fallback_reason": "per_symbol_forecast_confirmed_candidate",
        "expected_profitability_score": 100,
        "profitability_ok": True,
        "risk_reward_ok": True,
        "liquidity_ok": True,
        "volatility_ok": True,
        "current_price": 10,
        "is_first_15_min": False,
        "is_last_30_min": False,
    }

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True),
        alpaca_cfg=_trade_config(allow_short_selling=True),
        client=client,
        now=datetime(2026, 5, 15, 14, 41, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 1
    assert client.orders[0]["side"] == "sell"
    assert client.orders[0]["qty"] == "7"
    assert "notional" not in client.orders[0]
    assert "SHORTY:per_symbol_forecast_opened:order-SHORTY" in result["autopilot_open_notes"]


def test_auto_open_logs_short_rejection_context():
    engine = _engine()
    client = RejectingClient()
    candidate = _candidate("SHORTY", 100, bias="short")
    candidate["current_trade_action"] = "Short"
    candidate["side"] = "sell"
    candidate["details"] = {
        "per_symbol_forecast_fallback": True,
        "fallback_reason": "per_symbol_forecast_confirmed_candidate",
        "expected_profitability_score": 100,
        "profitability_ok": True,
        "risk_reward_ok": True,
        "liquidity_ok": True,
        "volatility_ok": True,
        "current_price": 10,
        "is_first_15_min": False,
        "is_last_30_min": False,
    }

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True),
        alpaca_cfg=_trade_config(allow_short_selling=True),
        client=client,
        now=datetime(2026, 5, 15, 14, 41, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert client.orders[0]["side"] == "sell"
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["verdict"] == "failed"
    assert row["block_reason"] == "alpaca_api_error"
    assert row["details"]["current_trade_action"] == "Short"
    assert row["details"]["side"] == "sell"
    assert row["details"]["nightly_bias"] == "short"
    assert row["details"]["order"]["side"] == "sell"
    assert row["details"]["order"]["qty"] == "7"
    assert row["details"]["api_code"] == "40310000"
    assert row["details"]["api_message"] == "asset is not shortable"


def test_auto_open_blocks_non_shortable_asset_before_submission():
    engine = _engine()
    client = FakeClient(asset={"tradable": True, "status": "active", "fractionable": True, "shortable": False})
    candidate = _candidate("NOSHORT", 100, bias="short")
    candidate["details"] = {
        "per_symbol_forecast_fallback": True,
        "fallback_reason": "per_symbol_forecast_confirmed_candidate",
        "expected_profitability_score": 100,
        "profitability_ok": True,
        "risk_reward_ok": True,
        "liquidity_ok": True,
        "volatility_ok": True,
        "current_price": 10,
        "is_first_15_min": False,
        "is_last_30_min": False,
    }

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True),
        alpaca_cfg=_trade_config(allow_short_selling=True),
        client=client,
        now=datetime(2026, 5, 15, 14, 41, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert client.orders == []
    assert "NOSHORT:blocked:asset_not_shortable" in result["autopilot_open_notes"]


def test_auto_open_uses_whole_share_qty_for_non_fractionable_long():
    engine = _engine()
    client = FakeClient(asset={"tradable": True, "status": "active", "fractionable": False, "shortable": True})
    candidate = _candidate("WHOLE", 100)
    candidate["details"] = {
        "per_symbol_forecast_fallback": True,
        "fallback_reason": "per_symbol_forecast_confirmed_candidate",
        "expected_profitability_score": 100,
        "profitability_ok": True,
        "risk_reward_ok": True,
        "liquidity_ok": True,
        "volatility_ok": True,
        "current_price": 12,
        "is_first_15_min": False,
        "is_last_30_min": False,
    }

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 15, 14, 41, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 1
    assert client.orders[0]["side"] == "buy"
    assert client.orders[0]["qty"] == "6"
    assert "notional" not in client.orders[0]


def test_auto_open_blocks_whole_share_when_size_below_one_share():
    engine = _engine()
    client = FakeClient(asset={"tradable": True, "status": "active", "fractionable": False, "shortable": True})
    candidate = _candidate("PRICEY", 100)
    candidate["details"] = {
        "per_symbol_forecast_fallback": True,
        "fallback_reason": "per_symbol_forecast_confirmed_candidate",
        "expected_profitability_score": 100,
        "profitability_ok": True,
        "risk_reward_ok": True,
        "liquidity_ok": True,
        "volatility_ok": True,
        "current_price": 250,
        "is_first_15_min": False,
        "is_last_30_min": False,
    }

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 15, 14, 41, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert client.orders == []
    assert "PRICEY:blocked:whole_share_size_below_one" in result["autopilot_open_notes"]


def test_auto_open_blocks_short_when_shorting_disabled():
    engine = _engine()
    client = FakeClient()
    candidate = _candidate("SHORTY", 100, bias="short")
    candidate["details"] = {
        "per_symbol_forecast_fallback": True,
        "fallback_reason": "per_symbol_forecast_confirmed_candidate",
        "expected_profitability_score": 100,
        "profitability_ok": True,
        "risk_reward_ok": True,
        "liquidity_ok": True,
        "volatility_ok": True,
        "is_first_15_min": False,
        "is_last_30_min": False,
    }

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True),
        alpaca_cfg=_trade_config(allow_short_selling=False),
        client=client,
        now=datetime(2026, 5, 15, 14, 41, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert client.orders == []
    assert "SHORTY:blocked:shorting_disabled" in result["autopilot_open_notes"]


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


def test_auto_open_pauses_new_entries_when_basket_is_broadly_red():
    engine = _engine()
    client = FakeClient()
    positions = [
        {"symbol": f"R{i}", "cost_basis": 100, "unrealized_pl": -1, "unrealized_plpc": -0.01}
        for i in range(10)
    ] + [{"symbol": "GREEN", "cost_basis": 100, "unrealized_pl": 1, "unrealized_plpc": 0.01}]

    result = apply_auto_open(
        [_candidate("NEW")],
        positions,
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, max_positions=20),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["basket_state"] == "new_entries_paused"
    assert result["new_entries_paused"] is True
    assert result["autopilot_open_notes"] == "basket_new_entries_paused"
    assert client.orders == []


def test_auto_open_blocks_candidate_with_unknown_latest_signal():
    engine = _engine()
    client = FakeClient()
    candidate = _candidate("UNK")
    candidate["details"]["latest_signal_status"] = "unknown"

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert result["autopilot_open_notes"] == "UNK:blocked:latest_signal_unknown_blocks_entry"
    assert client.orders == []


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
        eod_runner=lambda frame, current_state, open_orders: {
            "eod_state": "inactive",
            "eod_actions": 0,
            "eod_flatten_submitted": 0,
            "eod_remaining": len(frame),
            "eod_banner": "",
            "eod_action_notes": "",
        },
        strong_candidate_loader=lambda: [],
        fallback_candidate_loader=lambda: [_fallback_candidate("ANGI")],
        per_symbol_forecast_candidate_loader=lambda: [],
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


def test_paper_autopilot_tick_prefers_per_symbol_forecast_before_near_miss(monkeypatch, tmp_path):
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
        per_symbol_forecast_candidate_loader=lambda: [_candidate("HIGHP")],
        near_miss_candidate_loader=lambda: [_candidate("GLIBK")],
        auto_open_applier=lambda candidates, open_positions, mode: calls.append((candidates, open_positions, mode))
        or {
            "autopilot_open_attempted": 1,
            "autopilot_open_submitted": 1,
            "autopilot_open_blocked": 0,
            "autopilot_open_notes": "HIGHP:per_symbol_forecast_opened:order-HIGHP",
        },
    )

    assert calls and calls[0][0][0]["symbol"] == "HIGHP"
    assert [candidate["symbol"] for candidate in calls[0][0]] == ["HIGHP", "GLIBK"]
    assert calls[0][0][0]["details"]["candidate_source"] == "per_symbol_forecast"
    assert calls[0][0][1]["details"]["candidate_source"] == "near_miss"
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
        eod_runner=lambda frame, current_state, open_orders: {
            "eod_state": "inactive",
            "eod_actions": 0,
            "eod_flatten_submitted": 0,
            "eod_remaining": len(frame),
            "eod_banner": "",
            "eod_action_notes": "",
        },
        strong_candidate_loader=lambda: [],
        fallback_candidate_loader=lambda: [_fallback_candidate("ANGI")],
        per_symbol_forecast_candidate_loader=lambda: [],
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
