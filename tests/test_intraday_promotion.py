from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select

from stockml.db.schema import create_all, intraday_decisions, promotion_dry_runs, promotion_evaluations, shadow_outcomes, shadow_would_trades
from stockml.intraday.promotion import evaluate_criteria, evaluate_promotion, latest_evaluation, record_operator_dry_run_confirmation
from stockml.safety.live_disabled import assert_live_disabled


NOW = datetime(2026, 5, 11, 15, 0, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def engine():
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    return db


def _seed_trade(conn, symbol: str, side: str, day: int, net_excess: float, score: float = 0.72):
    decided_at = datetime(2026, 3, 1 + day, 15, 0, tzinfo=timezone.utc)
    decision_id = conn.execute(
        insert(intraday_decisions).values(
            decided_at=decided_at,
            symbol=symbol,
            bar_close_at=decided_at,
            verdict=f"allow_{side}",
            gate_version="v1.0.0",
            valid_until=decided_at,
            nightly_signal={"bias": side, "score": score},
            features={"mid_price": 100},
            contributing=[],
        )
    ).inserted_primary_key[0]
    trade_id = conn.execute(
        insert(shadow_would_trades).values(
            decision_id=decision_id,
            decided_at=decided_at,
            symbol=symbol,
            side=side,
            entry_price=100,
            estimated_entry_slippage_bps=10,
            nightly_score=score,
            gate_version="v1.0.0",
            evaluation_date=date(2026, 5, 1 + (day % 10)),
            status="evaluated",
        )
    ).inserted_primary_key[0]
    conn.execute(
        insert(shadow_outcomes).values(
            would_trade_id=trade_id,
            evaluated_at=NOW,
            exit_price=101,
            raw_return_pct=0.01,
            cost_bps=20,
            net_return_pct=0.008,
            spy_return_pct=0.0,
            net_excess_pct=net_excess,
            outperformed=net_excess > 0,
        )
    )


def test_promotion_evaluation_writes_one_row_and_reports_unmet_by_default():
    db = engine()

    payload = evaluate_promotion(as_of=NOW, target=db)

    with db.connect() as conn:
        rows = conn.execute(select(promotion_evaluations)).mappings().all()
    assert len(rows) == 1
    assert payload["criteria_met"] is False
    assert {row["name"] for row in payload["criteria_results"]} == {
        "SUFFICIENT_SAMPLES",
        "POSITIVE_NET_EXCESS",
        "CALIBRATION_HOLDS",
        "LOW_CONCENTRATION_BY_SYMBOL",
        "LOW_CONCENTRATION_BY_DAY",
        "ABLATION_LIFT",
        "OPERATOR_DRY_RUN",
    }
    assert latest_evaluation(db)["criteria_met"] is False


def test_latest_evaluation_falls_back_when_storage_missing(monkeypatch):
    class BrokenConnection:
        def execute(self, *args, **kwargs):
            raise RuntimeError("relation promotion_evaluations does not exist")

    monkeypatch.setattr("stockml.intraday.promotion._connect", lambda target=None: (BrokenConnection(), None))

    payload = latest_evaluation()

    assert payload["criteria_met"] is False
    assert payload["criteria_results"][0]["name"] == "PROMOTION_STORAGE_READY"
    assert "live trading remains disabled" in payload["notes"]


def test_operator_dry_run_confirmation_requires_notes_and_counts():
    db = engine()
    with pytest.raises(ValueError):
        record_operator_dry_run_confirmation(operator_id="op", symbol="TSLA", side="long", notes="", target=db)

    record_operator_dry_run_confirmation(operator_id="op", symbol="TSLA", side="long", notes="reviewed", confirmed_at=NOW, target=db)

    with db.connect() as conn:
        row = conn.execute(select(promotion_dry_runs)).mappings().one()
    assert row["operator_id"] == "op"
    assert row["symbol"] == "TSLA"


def test_promotion_criteria_can_pass_with_balanced_shadow_fixture():
    db = engine()
    with db.begin() as conn:
        for i in range(200):
            value = 0.01 if i < 140 else -0.001
            _seed_trade(conn, f"L{i}", "long", i % 20, value)
            _seed_trade(conn, f"S{i}", "short", i % 20, value)
        for i in range(5):
            conn.execute(
                insert(promotion_dry_runs).values(
                    confirmed_at=datetime(2026, 5, 31, 15, 0, tzinfo=timezone.utc) - timedelta(days=i),
                    operator_id="operator@stockml",
                    symbol=f"L{i}",
                    side="long",
                    notes="confirmed manually",
                )
            )

    results = evaluate_criteria(from_date=date(2026, 3, 1), to_date=date(2026, 5, 31), target=db)

    assert all(row.met for row in results)


def test_allow_live_trading_env_refuses_start(monkeypatch):
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "1")
    with pytest.raises(RuntimeError, match="Refusing to start"):
        assert_live_disabled()


def test_submit_order_matches_stay_in_paper_guarded_modules():
    allowed = {
        "src/stockml/trading/alpaca_client.py",
        "src/stockml/trading/execution_engine.py",
        "src/stockml/trading/paper_trader.py",
    }
    offenders = []
    missing_guard = []
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "submit_order(" not in text:
            continue
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        if rel not in allowed:
            offenders.append(rel)
        if rel in {"src/stockml/trading/execution_engine.py", "src/stockml/trading/paper_trader.py"} and "paper_only_guard" not in text:
            missing_guard.append(rel)
    assert offenders == []
    assert missing_guard == []
