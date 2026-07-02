from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stockml.experiments.raw_candidate_experiment import (
    prepare_experiment_candidates,
    run_raw_candidate_experiment,
)
from stockml.experiments.raw_candidate_experiment_policy import RawCandidateExperimentConfig


NOW = datetime(2026, 7, 2, 14, 30, tzinfo=timezone.utc)


def _candidate_file(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260702_143000.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "trade_action": "Long",
                "status": "rejected",
                "all_block_reasons": "risk_gate_failed",
                "raw_rank": 1,
                "current_price": 10,
                "approved_notional": 250,
            },
            {
                "symbol": "BBB",
                "trade_action": "No Decision",
                "directional_action": "Long",
                "status": "research_only",
                "all_block_reasons": "no_decision",
                "raw_rank": 2,
                "current_price": 20,
                "approved_notional": 250,
            },
            {
                "symbol": "CCC",
                "trade_action": "Short",
                "status": "rejected",
                "all_block_reasons": "short_side_validation_required",
                "raw_rank": 3,
                "current_price": 30,
                "approved_notional": 250,
            },
        ]
    ).to_csv(path, index=False)
    return path


def test_rejected_and_no_decision_candidates_are_experiment_only(tmp_path):
    frame = pd.read_csv(_candidate_file(tmp_path))
    prepared = prepare_experiment_candidates(frame, RawCandidateExperimentConfig(max_notional_per_trade=250))
    assert {"AAA", "BBB", "CCC"}.issubset(set(prepared["symbol"]))
    assert bool(prepared.loc[prepared["symbol"].eq("BBB"), "no_decision_experiment"].iloc[0]) is True
    assert prepared["experiment_mode"].eq("raw_candidate_no_gates").all()
    assert prepared["strategy_mode"].eq("experiment").all()


def test_dry_run_writes_separate_ledgers_and_rawexp_ids(tmp_path, monkeypatch):
    candidate = _candidate_file(tmp_path)
    monkeypatch.setenv("STOCKML_LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("STOCKML_PAPER_TRADING_ENABLED", "true")
    monkeypatch.setenv("STOCKML_ALPACA_SUBMIT_ORDERS", "false")
    result = run_raw_candidate_experiment(
        root=tmp_path,
        candidate_file=candidate,
        dry_run=True,
        now=NOW,
        config=RawCandidateExperimentConfig(enabled=True, max_trades_per_cycle=2, max_trades_per_day=3, allow_shorts=False),
    )
    assert result.status == "dry_run"
    assert result.selected == 2
    assert result.submitted == 0
    assert "data/trading/experiments" in str(result.events_path).replace("\\", "/")
    events = pd.read_csv(result.events_path)
    selected = events[events["status"].eq("dry_run_selected")]
    assert len(selected) == 2
    assert selected["client_order_id"].str.contains("rawexp").all()
    assert events.loc[events["symbol"].eq("CCC"), "experiment_reason"].iloc[0] == "experiment_skip_short_disabled"


def test_max_trades_per_day_is_enforced(tmp_path, monkeypatch):
    candidate = _candidate_file(tmp_path)
    monkeypatch.setenv("STOCKML_LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("STOCKML_PAPER_TRADING_ENABLED", "true")
    result = run_raw_candidate_experiment(
        root=tmp_path,
        candidate_file=candidate,
        dry_run=True,
        now=NOW,
        config=RawCandidateExperimentConfig(enabled=True, max_trades_per_cycle=3, max_trades_per_day=1, allow_shorts=False),
    )
    assert result.selected == 1
    events = pd.read_csv(result.events_path)
    assert "max_trades_per_day_reached" in set(events["experiment_reason"])


def test_normal_paper_autopilot_module_is_not_modified():
    import stockml.trading.paper_autopilot as paper_autopilot

    assert hasattr(paper_autopilot, "action")
