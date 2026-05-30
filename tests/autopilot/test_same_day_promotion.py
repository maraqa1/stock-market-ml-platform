from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, insert, select

from stockml.autopilot.same_day_auto import SameDayAutoConfig, auto_open_candidates, evaluate_auto_open_candidate
from stockml.autopilot.same_day_promotion import evaluate_and_record, evaluate_contract
from stockml.db.schema import create_all, same_day_candidates, same_day_promotion_evaluations


NOW = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    return engine


def _candidate(symbol: str = "AAA", probability: float = 0.7) -> dict:
    return {
        "symbol": symbol,
        "continuation_probability": probability,
        "strategy_stream": "same_day_momentum",
    }


def _insert_confirmed_candidate(engine, symbol: str = "AAA") -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(same_day_candidates).values(
                generated_at=NOW,
                decision_time=NOW,
                symbol=symbol,
                direction="long",
                continuation_probability=0.7,
                reversal_probability=0.3,
                model_id="same-day-test",
                features_id=1,
                same_day_reason="test",
                strategy_stream="same_day_momentum",
                max_hold_days=1,
                must_flatten_eod=True,
                arbitration_outcome="paper_assist_opened",
            )
        )


def test_contract_evaluation_writes_daily_row():
    engine = _engine()
    _insert_confirmed_candidate(engine)

    result = evaluate_and_record(engine=engine, now=NOW)

    with engine.connect() as conn:
        rows = conn.execute(select(same_day_promotion_evaluations)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["evaluated_at"].replace(tzinfo=timezone.utc) == NOW
    assert rows[0]["criteria_met"] == result["criteria_met"]
    assert rows[0]["activated"] is False
    assert {row["name"] for row in rows[0]["criteria_results"]} == {
        "SUFFICIENT_SAMPLES",
        "POSITIVE_NET_PNL",
        "HIT_RATE",
        "RR_RATIO",
        "OVERRIDE_RATE",
        "WORST_DAY",
        "CONCENTRATION",
        "NO_KILL_SWITCH_CASCADES",
    }


def test_contract_can_be_met_with_sufficient_paper_assist_record():
    trades = []
    for idx in range(120):
        trades.append(
            {
                "symbol": f"SYM{idx % 12}",
                "generated_at": NOW - timedelta(days=idx % 30),
                "realized_net_pnl": 1.0,
            }
        )

    result = evaluate_contract(trades, candidates_presented=130)

    assert result["criteria_met"] is True


def test_auto_enabled_false_blocks_auto_open():
    decision = evaluate_auto_open_candidate(
        _candidate(),
        contract_met=True,
        config=SameDayAutoConfig(same_day_auto_enabled=False),
    )

    assert decision == {"action": "paper_assist", "allowed": False, "reason": "SAME_DAY_AUTO_DISABLED"}


def test_auto_open_respects_higher_threshold():
    decision = evaluate_auto_open_candidate(
        _candidate(probability=0.64),
        contract_met=True,
        config=SameDayAutoConfig(same_day_auto_enabled=True, min_continuation_probability=0.65),
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "REJECTED_CONTINUATION_THRESHOLD"


def test_daily_cap_enforced_in_auto_mode():
    decision = evaluate_auto_open_candidate(
        _candidate(probability=0.9),
        contract_met=True,
        config=SameDayAutoConfig(same_day_auto_enabled=True, max_auto_opens_per_day=5),
        todays_auto_opens=5,
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "REJECTED_DAILY_CANDIDATE_CAP"


def test_auto_open_uses_paper_order_callback_with_same_day_flags():
    orders = []

    result = auto_open_candidates(
        [_candidate("AAA", 0.7)],
        contract_met=True,
        config=SameDayAutoConfig(same_day_auto_enabled=True),
        paper_order_func=lambda payload: orders.append(payload) or {"status": "submitted", "order_id": "paper-1"},
        gate_func=lambda **_: SimpleNamespace(allow=True),
    )

    assert result["opened"] == 1
    assert orders[0]["strategy_stream"] == "same_day_momentum"
    assert orders[0]["must_flatten_at_eod"] is True


def test_kill_switch_blocks_auto_open_when_tripped():
    decision = evaluate_auto_open_candidate(
        _candidate(probability=0.9),
        contract_met=True,
        config=SameDayAutoConfig(same_day_auto_enabled=True),
        gate_func=lambda **_: SimpleNamespace(allow=False),
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "BLOCKED_KILL_SWITCH"


def test_paper_only_guarantee():
    for path in [
        Path("src/stockml/autopilot/same_day_auto.py"),
        Path("src/stockml/autopilot/same_day_promotion.py"),
    ]:
        text = path.read_text(encoding="utf-8")
        assert "submit_order" not in text
        assert "/v2/orders" not in text
