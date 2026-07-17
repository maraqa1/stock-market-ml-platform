from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stockml.trading.counterfactual_log import (
    attach_counterfactual_forward_returns,
    build_counterfactual_candidates,
    write_counterfactual_candidates,
)


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def test_counterfactual_log_writes_all_candidates_on_no_trade_day(tmp_path: Path):
    candidates = pd.DataFrame(
        [
            {"symbol": "AAA", "side": "buy", "trade_action": "Long", "rank_overall": 1, "close": 10.0},
            {"symbol": "BBB", "side": "buy", "trade_action": "No Decision", "rank_overall": 2, "close": 20.0},
        ]
    )
    plan = pd.DataFrame(columns=["symbol", "order_eligible", "trade_quality_status"])

    result = write_counterfactual_candidates(candidates, plan=plan, decision_time=NOW, output_dir=tmp_path, stamp="20260717_120000")
    out = pd.read_csv(result.path)

    assert len(out) == 2
    assert out["symbol"].tolist() == ["AAA", "BBB"]
    assert out["decision_price"].tolist() == [10.0, 20.0]
    assert result.metadata_path.exists()


def test_counterfactual_log_keeps_plan_gate_fields():
    candidates = pd.DataFrame([{"symbol": "AAA", "side": "buy", "trade_action": "Long", "close": 10.0}])
    plan = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "order_eligible": False,
                "trade_quality_status": "rejected",
                "primary_block_reason": "price_below_minimum",
                "net_expected_return_bps": -5,
            }
        ]
    )

    out = build_counterfactual_candidates(candidates, plan=plan, decision_time=NOW)

    assert out.iloc[0]["trade_quality_status"] == "rejected"
    assert out.iloc[0]["primary_block_reason"] == "price_below_minimum"
    assert out.iloc[0]["net_expected_return_bps"] == -5


def test_counterfactual_forward_returns_join_fixture_gold(tmp_path: Path):
    counterfactual = pd.DataFrame(
        [
            {"decision_date": "2026-07-17", "symbol": "AAA", "side": "buy", "trade_action": "Long", "decision_price": 10.0},
            {"decision_date": "2026-07-17", "symbol": "BBB", "side": "sell", "trade_action": "Short", "decision_price": 20.0},
        ]
    )
    gold = pd.DataFrame(
        [
            {"date": "2026-07-17", "ticker": "AAA", "forward_5d_return": 0.01, "forward_5d_alpha_vs_spy": 0.005, "forward_5d_alpha_vs_sector": 0.002},
            {"date": "2026-07-17", "ticker": "BBB", "forward_5d_return": 0.02, "forward_5d_alpha_vs_spy": 0.015, "forward_5d_alpha_vs_sector": 0.01},
        ]
    )

    out = attach_counterfactual_forward_returns(counterfactual, gold_path=None)
    assert set(out["outcome_status"]) == {"insufficient_data"}

    # Directly exercise the merge path using a temporary gold fixture.
    # The writer uses the same attach function through gold_outcome_slice.
    from stockml.trading.counterfactual_log import write_counterfactual_forward_returns

    counter_path = tmp_path / "counter.csv"
    gold_path = tmp_path / "gold.csv"
    counterfactual.to_csv(counter_path, index=False)
    gold.to_csv(gold_path, index=False)
    result = write_counterfactual_forward_returns(counter_path, gold_path=gold_path, output_dir=tmp_path, stamp="fixture")
    joined = pd.read_csv(result.path)

    assert joined.loc[joined["symbol"].eq("AAA"), "directional_forward_5d_bps"].iloc[0] == 100.0
    assert joined.loc[joined["symbol"].eq("BBB"), "directional_forward_5d_bps"].iloc[0] == -200.0
    assert set(joined["outcome_status"]) == {"ok"}
