from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import os

import pandas as pd
import requests
import yaml
from sqlalchemy import create_engine, insert, select

from stockml.autopilot.open import (
    AutoOpenConfig,
    _record_open,
    apply_auto_open,
    latest_flat_account_fallback_candidates,
    latest_near_miss_fallback_candidates,
    latest_plan_fallback_candidates,
    latest_per_symbol_forecast_fallback_candidates,
    latest_strong_candidates,
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


class FakeQuoteProvider:
    def __init__(self, quote: dict | None = None, error: Exception | None = None):
        self.quote = quote or {}
        self.error = error
        self.calls: list[str] = []

    def fetch_quote(self, symbol: str):
        self.calls.append(symbol)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            symbol=symbol.upper(),
            bid=self.quote.get("bid"),
            ask=self.quote.get("ask"),
            last_price=self.quote.get("last_price"),
            quote_ts=self.quote.get("quote_ts"),
            fetched_at=self.quote.get("fetched_at"),
            source="fake",
        )


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
        "meta_label_decision": "Take Trade",
        "directional_action": "Long" if bias == "long" else "Short",
        "directional_strength": 0.99,
        "trade_quality_status": "approved",
        "details": {"is_first_15_min": False, "is_last_30_min": False},
    }


def test_record_open_sanitizes_nan_details_for_json_log():
    engine = _engine()
    now = datetime(2026, 7, 1, 20, 30, tzinfo=timezone.utc)

    _record_open(
        symbol="BNY",
        promotion_score=None,
        size_usd=2500,
        verdict="blocked",
        block_reason="quote_stale",
        details={
            "all_block_reasons": float("nan"),
            "nested": {"value": pd.NA},
            "items": [1, float("nan")],
        },
        engine=engine,
        now=now,
    )

    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["block_reason"] == "quote_stale"
    assert row["details"]["all_block_reasons"] is None
    assert row["details"]["nested"]["value"] is None
    assert row["details"]["items"] == [1, None]


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
    assert position_size_usd(5000, config) == 500


def test_auto_open_higher_risk_defaults(tmp_path):
    config = load_auto_open_config(root=tmp_path)

    assert config.max_positions == 20
    assert config.default_position_value_cap_usd == 2500
    assert config.max_long_positions == 15
    assert config.max_short_positions == 15
    assert config.near_miss_entries_with_open_positions is True


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
            "validation_max_new_orders_per_cycle": 2,
            "validation_max_new_orders_per_day": 11,
            "validation_max_open_positions_total": 7,
            "ignored_limit": 50,
        },
        root=tmp_path,
    )

    assert config.max_auto_opens_per_day == 100
    assert config.max_positions == 12
    assert config.flat_account_fallback_max_per_day == 4
    assert config.near_miss_fallback_max_per_day == 6
    assert config.per_symbol_forecast_fallback_max_per_day == 8
    assert config.validation_max_new_orders_per_cycle == 2
    assert config.validation_max_new_orders_per_day == 11
    assert config.validation_max_open_positions_total == 7


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


def test_auto_open_respects_candidate_approved_notional_and_quantity():
    engine = _engine()
    client = FakeClient(equity="100000")
    candidate = _candidate("ATAI", 0.71)
    candidate.update({"current_price": 7.19, "approved_notional": 250.0, "suggested_quantity": 34})
    candidate["details"].update({"approved_notional": 250.0, "suggested_quantity": 34})

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, max_positions=5, default_position_value_cap_usd=2500),
        alpaca_cfg=_trade_config(account_equity=100000, max_notional_per_order=5000),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 1
    assert client.orders[0]["symbol"] == "ATAI"
    assert client.orders[0]["qty"] == "34"
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["size_usd"] == 250.0
    assert row["details"]["planned_approved_notional"] == 250.0
    assert row["details"]["planned_suggested_quantity"] == 34


def test_auto_open_blocks_promoted_candidate_without_model_evidence():
    engine = _engine()
    client = FakeClient()
    candidate = _candidate("CSTL", 0.71)
    for key in ("meta_label_decision", "directional_action", "directional_strength", "trade_quality_status"):
        candidate.pop(key)

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, max_positions=5),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert "model_evidence_missing" in result["autopilot_open_notes"]
    assert client.orders == []
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["symbol"] == "CSTL"
    assert row["verdict"] == "blocked"
    assert row["block_reason"] == "model_evidence_missing"
    assert row["details"]["model_evidence_status"] == "blocked"


def test_paper_allow_all_override_bypasses_stockml_open_gates(tmp_path):
    engine = _engine()
    client = FakeClient()
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "autopilot.yaml").write_text(
        yaml.safe_dump(
            {
                "autopilot": {
                    "open_enabled": True,
                    "validation_mode": False,
                    "holding_review_gate_enabled": False,
                    "max_auto_opens_per_day": 100,
                    "max_positions": 100,
                },
                "anti_churn": {"enabled": False},
                "position_lifecycle": {"require_exit_confirmation": False},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "config" / "trading.yaml").write_text(
        yaml.safe_dump({"trading": {"paper_trading_enabled": True, "live_trading_enabled": False}}, sort_keys=False),
        encoding="utf-8",
    )
    (tmp_path / "config" / "session_modes.yaml").write_text(
        yaml.safe_dump(
            {
                "session_modes": {
                    "overnight_24_5": {
                        "allow_order_submission": True,
                        "require_overnight_tradable": False,
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    candidate = _candidate("CSTL", 0.71)
    for key in ("meta_label_decision", "directional_action", "directional_strength", "trade_quality_status"):
        candidate.pop(key)

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, max_positions=100, validation_mode=False, holding_review_gate_enabled=False),
        alpaca_cfg=_trade_config(submit_orders=True, paper_trading_enabled=True, live_trading_enabled=False),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
        root=tmp_path,
    )

    assert result["autopilot_open_submitted"] == 1
    assert result["autopilot_open_blocked"] == 0
    assert client.orders[0]["symbol"] == "CSTL"


def test_auto_open_allows_same_day_momentum_without_daily_model_evidence():
    engine = _engine()
    client = FakeClient()
    candidate = {
        "symbol": "SNOW",
        "promotion_score": 0.82,
        "nightly_bias": "long",
        "current_price": 243.84,
        "is_held": False,
        "details": {
            "strategy_stream": "same_day_momentum",
            "same_day_momentum": True,
            "same_day_trade_action": "Long",
            "same_day_confidence": 0.82,
            "manual_dollar_traded": 7_400_000_000,
            "manual_move_pct": 39.13,
            "is_first_15_min": False,
            "is_last_30_min": False,
        },
    }

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, same_day_momentum_size_multiplier=0.50),
        alpaca_cfg=_trade_config(max_notional_per_order=1000),
        client=client,
        now=datetime(2026, 5, 28, 15, 30, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 1
    assert "SNOW:same_day_momentum_opened:order-SNOW" in result["autopilot_open_notes"]
    assert client.orders[0]["side"] == "buy"
    assert client.orders[0]["time_in_force"] == "day"
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["verdict"] == "opened"
    assert row["details"]["strategy_stream"] == "same_day_momentum"
    assert row["details"]["must_flatten_eod"] is True


def test_auto_open_blocks_same_day_momentum_below_score_gate():
    engine = _engine()
    candidate = {
        "symbol": "WEAK",
        "promotion_score": 0.30,
        "nightly_bias": "long",
        "current_price": 10,
        "is_held": False,
        "details": {
            "strategy_stream": "same_day_momentum",
            "same_day_momentum": True,
            "same_day_trade_action": "Long",
            "manual_dollar_traded": 5_000_000,
            "is_first_15_min": False,
            "is_last_30_min": False,
        },
    }

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, same_day_momentum_min_score=0.55),
        alpaca_cfg=_trade_config(),
        client=FakeClient(),
        now=datetime(2026, 5, 28, 15, 30, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert "WEAK:blocked:same_day_score_below_minimum" in result["autopilot_open_notes"]


def test_latest_strong_candidates_enriches_model_evidence_from_candidate_pool(monkeypatch, tmp_path):
    engine = _engine()
    now = datetime(2026, 6, 11, 18, 55, tzinfo=timezone.utc)
    with engine.begin() as conn:
        snapshot_id = conn.execute(
            insert(intraday_candidate_snapshots).values(
                snapshot_at=now,
                bar_close_at=now,
                symbol="CXW",
                nightly_score=0.95,
                nightly_bias="long",
                is_held=False,
                last_price=19.25,
                status="ok",
                details={"source": "intraday_promotion"},
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(intraday_promotion_log).values(
                logged_at=now,
                snapshot_id=snapshot_id,
                symbol="CXW",
                verdict="promote_to_selection_strong",
                promotion_score=1.0,
                contributing=["long_trend_5m_positive"],
            )
        )
    evidence_path = tmp_path / "08_alpaca_paper_candidate_pool_20260611_152557.csv"
    pd.DataFrame(
        [
            {
                "symbol": "CXW",
                "trade_action": "Long",
                "directional_action": "Long",
                "directional_strength": 0.955657,
                "meta_label_decision": "Take Trade",
                "trade_quality_status": "reduced",
                "candidate_status": "reduced",
                "order_eligible": True,
                "risk_adjusted_score": 3.159219,
            }
        ]
    ).to_csv(evidence_path, index=False)
    monkeypatch.setattr("stockml.autopilot.open.latest_file", lambda directory, pattern: evidence_path)

    candidates = latest_strong_candidates(engine=engine)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["symbol"] == "CXW"
    assert candidate["meta_label_decision"] == "Take Trade"
    assert candidate["trade_quality_status"] == "reduced"
    assert candidate["candidate_status"] == "reduced"
    assert candidate["order_eligible"] is True
    assert candidate["details"]["source"] == "intraday_promotion"
    assert candidate["details"]["model_evidence_source"] == "latest_candidate_pool"


def test_auto_open_blocks_promoted_candidate_rejected_by_meta_label():
    engine = _engine()
    client = FakeClient()
    candidate = _candidate("CSTL", 0.71)
    candidate["meta_label_decision"] = "Skip Trade"

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, max_positions=5),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert "model_meta_label_rejected" in result["autopilot_open_notes"]
    assert client.orders == []
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["block_reason"] == "model_meta_label_rejected"


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
    assert candidates[0]["trade_action"] == "Long"
    assert candidates[0]["directional_action"] == "Long"
    assert candidates[0]["trade_quality_status"] == "approved"


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


def test_auto_open_blocks_fallback_candidate_missing_from_holding_review(tmp_path):
    engine = _engine()
    client = FakeClient()
    review_dir = tmp_path / "data" / "trading" / "holding_period"
    review_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "symbol": "DDOG",
                "holding_quality": "strong",
                "holding_gate_pass": True,
                "holding_gate_reason": "positive_holding_edge_strong",
                "recommended_holding_days": 10,
                "max_holding_days": 10,
            }
        ]
    ).to_csv(review_dir / "holding_review_20260520_120000.csv", index=False)
    candidate = _candidate("TNDM", 100)
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
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 20, 14, 41, tzinfo=timezone.utc),
        root=tmp_path,
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert "TNDM:blocked:holding_review_missing" in result["autopilot_open_notes"]
    assert client.orders == []
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["block_reason"] == "holding_review_missing"
    assert row["details"]["holding_review_status"] == "blocked"


def test_auto_open_blocks_fallback_candidate_with_avoid_holding_review(tmp_path):
    engine = _engine()
    client = FakeClient()
    review_dir = tmp_path / "data" / "trading" / "holding_period"
    review_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "symbol": "DGXX",
                "holding_quality": "avoid",
                "holding_gate_pass": False,
                "holding_gate_reason": "holding_edge_not_confirmed",
                "recommended_holding_days": 10,
                "max_holding_days": 10,
            }
        ]
    ).to_csv(review_dir / "holding_review_20260520_120000.csv", index=False)
    candidate = _candidate("DGXX", 100)
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
        config=AutoOpenConfig(open_enabled=True),
        alpaca_cfg=_trade_config(),
        client=client,
        now=datetime(2026, 5, 20, 14, 41, tzinfo=timezone.utc),
        root=tmp_path,
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert "DGXX:blocked:holding_edge_not_confirmed" in result["autopilot_open_notes"]
    assert client.orders == []
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["block_reason"] == "holding_edge_not_confirmed"
    assert row["details"]["holding_quality"] == "avoid"


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


def test_auto_open_rounds_up_to_one_whole_share_when_size_is_small():
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

    assert result["autopilot_open_submitted"] == 1
    assert result["autopilot_open_blocked"] == 0
    assert client.orders[0]["qty"] == "1"


def test_auto_open_blocks_when_one_share_exceeds_max_notional():
    engine = _engine()
    client = FakeClient(asset={"tradable": True, "status": "active", "fractionable": False, "shortable": True})
    candidate = _candidate("TOOHI", 100)
    candidate["details"] = {
        "per_symbol_forecast_fallback": True,
        "fallback_reason": "per_symbol_forecast_confirmed_candidate",
        "expected_profitability_score": 100,
        "profitability_ok": True,
        "risk_reward_ok": True,
        "liquidity_ok": True,
        "volatility_ok": True,
        "current_price": 1500,
        "is_first_15_min": False,
        "is_last_30_min": False,
    }

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True),
        alpaca_cfg=_trade_config(max_notional_per_order=1000),
        client=client,
        now=datetime(2026, 5, 15, 14, 41, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert client.orders == []
    assert "TOOHI:blocked:whole_share_size_below_one" in result["autopilot_open_notes"]


def test_rotation_replacement_sizes_to_at_least_one_whole_share():
    engine = _engine()
    client = FakeClient(asset={"tradable": True, "status": "active", "fractionable": False, "shortable": True})
    candidate = _candidate("UNP", 0.65)
    candidate["current_price"] = 250
    candidate["details"] = {
        "rotation_replacement": True,
        "latest_signal_status": "fresh",
        "latest_signal_direction": "long",
        "model_status": "decision_grade",
        "is_first_15_min": False,
        "is_last_30_min": False,
    }

    result = apply_auto_open(
        [candidate],
        [{"symbol": f"HELD{i}"} for i in range(10)],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=False, rotate_enabled=True, max_positions=5),
        alpaca_cfg=_trade_config(max_notional_per_order=1000),
        client=client,
        now=datetime(2026, 5, 15, 14, 41, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 1
    assert client.orders[0]["qty"] == "1"


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
    assert result["autopilot_open_notes"] == "daily_auto_open_cap_reached"


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


def test_paper_autopilot_tick_builds_monitor_and_closes_max_holding_days(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _trade_config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    portal = tmp_path / "data" / "portal_outputs"
    portal.mkdir(parents=True, exist_ok=True)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([]).to_csv(tracking, index=False)
    pd.DataFrame(
        [
            {
                "symbol": "HOLD",
                "qty": 1,
                "current_price": 10,
                "avg_entry_price": 10,
                "side": "long",
                "submitted_at": "2026-05-01T14:30:00Z",
                "unrealized_plpc": 0.0,
            }
        ]
    ).to_csv(positions, index=False)
    pd.DataFrame(
        [
            {
                "symbol": "HOLD",
                "trade_action": "Long",
                "signal_generated_at": "2026-05-23T15:55:00Z",
                "max_holding_days": 1,
            }
        ]
    ).to_csv(portal / "08_alpaca_paper_order_plan_20260523_155500.csv", index=False)
    pd.DataFrame([{"symbol": "HOLD", "status": "filled"}]).to_csv(
        portal / "08_alpaca_paper_order_results_20260523_155500.csv",
        index=False,
    )
    closes = []
    monkeypatch.setattr(
        paper_autopilot,
        "apply_manual_position_action",
        lambda symbol, action, **_: closes.append((symbol, action)) or {"status": "submitted", "message": "closed"},
    )

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
    )

    assert closes == [("HOLD", "close")]
    assert state["autopilot_close_submitted"] == 1
    assert state["autopilot_action_notes"].startswith("HOLD:close:monitor_close:submitted")
    decision_path = sorted((tmp_path / "data" / "trading" / "agent_decisions").glob("position_decisions_*.csv"))[-1]
    decisions = pd.read_csv(decision_path)
    assert decisions.iloc[0]["decision"] == "close"
    assert "max_holding_days_exceeded" in decisions.iloc[0]["decision_reason"]


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


def test_latest_plan_fallback_candidates_loads_approved_order_plan(tmp_path):
    portal = tmp_path / "data" / "portal_outputs"
    portal.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "symbol": "SKIP",
                "trade_action": "Long",
                "side": "buy",
                "trade_quality_status": "rejected",
                "notional": 250,
                "suggested_quantity": 1,
                "current_price": 20,
                "risk_adjusted_score": 99,
            },
            {
                "symbol": "PLAN",
                "trade_action": "Long",
                "side": "buy",
                "trade_quality_status": "approved",
                "notional": 250,
                "suggested_quantity": 2,
                "current_price": 20,
                "risk_adjusted_score": 10,
            },
        ]
    ).to_csv(portal / "08_alpaca_paper_order_plan_20260527_120000.csv", index=False)

    candidates = latest_plan_fallback_candidates(
        root=tmp_path,
        config=AutoOpenConfig(plan_fallback_max_file_age_minutes=0),
    )

    assert [candidate["symbol"] for candidate in candidates] == ["PLAN"]
    assert candidates[0]["details"]["plan_fallback"] is True
    assert candidates[0]["details"]["current_price"] == 20


def test_paper_autopilot_tick_uses_order_plan_when_intraday_sources_are_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _trade_config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([]).to_csv(tracking, index=False)
    pd.DataFrame([{"symbol": "HELD", "qty": 1, "unrealized_plpc": 0.0}]).to_csv(positions, index=False)
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
        per_symbol_forecast_candidate_loader=lambda: [],
        near_miss_candidate_loader=lambda: [],
        plan_candidate_loader=lambda: [_candidate("PLAN")],
        auto_open_applier=lambda candidates, open_positions, mode: calls.append((candidates, open_positions, mode))
        or {
            "autopilot_open_attempted": 1,
            "autopilot_open_submitted": 1,
            "autopilot_open_blocked": 0,
            "autopilot_open_notes": "PLAN:plan_opened:order-PLAN",
        },
    )

    assert calls and calls[0][0][0]["symbol"] == "PLAN"
    assert calls[0][1][0]["symbol"] == "HELD"
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


def test_auto_open_uses_overnight_limit_order_when_24_5_enabled():
    engine = _engine()
    client = FakeClient(asset={"tradable": True, "status": "active", "fractionable": True, "shortable": True, "overnight_tradable": True})

    candidate = _candidate("NVTS", 0.71)
    candidate["quote_timestamp"] = "2026-06-18T02:00:00+00:00"
    candidate["spread_bps"] = 2

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, max_positions=5, min_position_value_usd=1),
        alpaca_cfg=_trade_config(extended_hours=True, overnight_trading_enabled=True, overnight_limit_buffer_bps=50),
        client=client,
        now=datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 1
    assert client.orders[0]["type"] == "limit"
    assert client.orders[0]["extended_hours"] is True
    assert client.orders[0]["limit_price"] > 0
    assert client.orders[0]["qty"] == "1"
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["details"]["session_mode"] == "overnight_24_5"
    assert row["details"]["order_policy"] == "overnight_24_5"
    assert row["details"]["extended_hours"] is True
    assert row["details"]["session_size_multiplier"] == 0.1


def test_auto_open_blocks_24_5_order_without_fresh_quote_timestamp():
    engine = _engine()
    client = FakeClient(asset={"tradable": True, "status": "active", "fractionable": True, "shortable": True, "overnight_tradable": True})

    result = apply_auto_open(
        [_candidate("NVTS", 0.71)],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, max_positions=5, min_position_value_usd=1),
        alpaca_cfg=_trade_config(extended_hours=True, overnight_trading_enabled=True, overnight_limit_buffer_bps=50),
        client=client,
        now=datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert "quote_timestamp_missing" in result["autopilot_open_notes"]
    assert client.orders == []


def test_auto_open_uses_fresh_quote_price_for_extended_limit_and_quantity():
    engine = _engine()
    client = FakeClient(asset={"tradable": True, "status": "active", "fractionable": True, "shortable": True, "overnight_tradable": True})
    quote_provider = FakeQuoteProvider(
        {
            "bid": 49.99,
            "ask": 50.00,
            "quote_ts": datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
            "fetched_at": datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
        }
    )
    candidate = _candidate("NVTS", 0.71)
    candidate["current_price"] = 49.95

    result = apply_auto_open(
        [candidate],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, max_positions=5, min_position_value_usd=1),
        alpaca_cfg=_trade_config(extended_hours=True, overnight_trading_enabled=True, overnight_limit_buffer_bps=50),
        client=client,
        quote_provider=quote_provider,
        now=datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 1
    assert quote_provider.calls == ["NVTS"]
    assert client.orders[0]["limit_price"] == 50.25
    assert client.orders[0]["qty"] == "1"
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["details"]["execution_reference_price"] == 50.0
    assert row["details"]["live_ask"] == 50.0


def test_auto_open_blocks_extended_order_when_quote_fetch_fails():
    engine = _engine()
    client = FakeClient(asset={"tradable": True, "status": "active", "fractionable": True, "shortable": True, "overnight_tradable": True})
    quote_provider = FakeQuoteProvider(error=RuntimeError("quote_api_down"))

    result = apply_auto_open(
        [_candidate("NVTS", 0.71)],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, max_positions=5, min_position_value_usd=1),
        alpaca_cfg=_trade_config(extended_hours=True, overnight_trading_enabled=True, overnight_limit_buffer_bps=50),
        client=client,
        quote_provider=quote_provider,
        now=datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert "quote_timestamp_missing" in result["autopilot_open_notes"]
    assert client.orders == []
    with engine.connect() as conn:
        row = conn.execute(select(autopilot_open_log)).mappings().one()
    assert row["details"]["quote_fetch_error"] == "quote_api_down"


def test_auto_open_blocks_24_5_order_when_asset_not_overnight_tradable():
    engine = _engine()
    client = FakeClient(asset={"tradable": True, "status": "active", "fractionable": True, "shortable": True, "overnight_tradable": False})

    result = apply_auto_open(
        [_candidate("NVTS", 0.71)],
        [],
        mode="paper_autopilot",
        engine=engine,
        config=AutoOpenConfig(open_enabled=True, max_positions=5, min_position_value_usd=1),
        alpaca_cfg=_trade_config(extended_hours=True, overnight_trading_enabled=True),
        client=client,
        now=datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
    )

    assert result["autopilot_open_submitted"] == 0
    assert result["autopilot_open_blocked"] == 1
    assert "asset_not_overnight_tradable" in result["autopilot_open_notes"]
    assert client.orders == []
