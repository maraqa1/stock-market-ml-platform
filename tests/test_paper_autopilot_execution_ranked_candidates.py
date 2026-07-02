from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.candidates.execution_ranker import execution_ranked_auto_open_candidates
from stockml.trading import paper_autopilot
from stockml.trading.config import AlpacaConfig


def _config(*, submit_orders: bool = True) -> AlpacaConfig:
    return AlpacaConfig(
        api_key="paper-key",
        secret_key="paper-secret",
        base_url="https://paper-api.alpaca.markets",
        submit_orders=submit_orders,
        extended_hours=False,
        max_orders=20,
        max_notional_per_order=5000,
        max_total_notional=50000,
        min_trade_price=5,
        max_sector_fraction=1.0,
        min_side_probability=0.0,
        min_abs_probability_edge=0.0,
        min_intraday_volume=0,
        min_market_cap=0,
        min_risk_adjusted_score=-1,
        transaction_cost_bps=10,
        live_trading_enabled=False,
        paper_trading_enabled=True,
        execution_owner="paper_autopilot",
    )


def _ranked_file(root: Path) -> Path:
    out = root / "data" / "portal_outputs"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "execution_ranked_candidates_20260701_120000.csv"
    pd.DataFrame(
        [
            {
                "raw_rank": 1,
                "execution_rank": "",
                "symbol": "ICCM",
                "side": "buy",
                "status": "blocked",
                "executable": False,
                "research_only": False,
                "all_block_reasons": "price_below_minimum",
                "primary_block_reason": "price_below_minimum",
            },
            {
                "raw_rank": 28,
                "execution_rank": 1,
                "symbol": "BNY",
                "side": "buy",
                "status": "executable",
                "executable": True,
                "research_only": False,
                "all_block_reasons": "",
                "primary_block_reason": "",
                "validated_expected_return_bps": 42,
                "validated_hit_rate": 0.57,
            },
            {
                "raw_rank": 57,
                "execution_rank": "",
                "symbol": "CRCL",
                "side": "sell",
                "status": "research_only",
                "executable": False,
                "research_only": True,
                "all_block_reasons": "short_side_validation_required",
                "primary_block_reason": "short_side_validation_required",
            },
        ]
    ).to_csv(path, index=False)
    return path


def _csv(path: Path, rows: list[dict]) -> Path:
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_loader_uses_latest_execution_ranked_candidates_and_ignores_blocked_and_research_only(tmp_path):
    _ranked_file(tmp_path)
    candidates = execution_ranked_auto_open_candidates(root=tmp_path)
    assert [row["symbol"] for row in candidates] == ["BNY"]
    assert candidates[0]["execution_rank"] == 1
    assert candidates[0]["raw_rank"] == 28
    assert candidates[0]["details"]["candidate_source"] == "execution_ranked_candidates"


def test_loader_ignores_no_decision_candidate(tmp_path):
    out = tmp_path / "data" / "portal_outputs"
    out.mkdir(parents=True)
    pd.DataFrame(
        [{
            "raw_rank": 1,
            "execution_rank": 1,
            "symbol": "AAA",
            "side": "",
            "trade_action": "No Decision",
            "source_trade_action": "No Decision",
            "status": "executable",
            "executable": True,
            "research_only": False,
            "all_block_reasons": "",
        }]
    ).to_csv(out / "execution_ranked_candidates_20260701_120000.csv", index=False)
    assert execution_ranked_auto_open_candidates(root=tmp_path) == []


def test_paper_autopilot_tick_prioritizes_execution_ranked_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = _csv(tmp_path / "tracking.csv", [{"symbol": "OLD", "alpaca_status": "filled"}])
    positions = _csv(tmp_path / "positions.csv", [])
    calls: list[list[str]] = []

    def open_applier(candidates, open_positions, mode):
        calls.append([row["symbol"] for row in candidates])
        return {"autopilot_open_attempted": 1, "autopilot_open_submitted": 1, "autopilot_open_blocked": 0, "autopilot_open_notes": "BNY:opened:test-order"}

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 0, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        intraday_decision_loader=lambda: {"intraday_allows": 0, "intraday_blocks": 0, "latest_intraday_at": ""},
        monitor_decision_loader=lambda root: {"monitor_actions": 0, "monitor_close": 0, "monitor_rotate": 0, "monitor_watch": 0, "latest_monitor_at": ""},
        strong_candidate_loader=lambda: [{"symbol": "RAW1"}],
        execution_ranked_candidate_loader=lambda: [{"symbol": "BNY", "execution_rank": 1}],
        auto_open_applier=open_applier,
    )

    assert calls[0][0] == "BNY"
    assert state["autopilot_open_submitted"] == 1
    assert state["phase"] == "waiting_for_fills"


def test_paper_autopilot_uses_execution_ranked_candidates_as_authoritative_source(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = _csv(tmp_path / "tracking.csv", [{"symbol": "OLD", "alpaca_status": "filled"}])
    positions = _csv(tmp_path / "positions.csv", [])
    calls: list[list[str]] = []

    def open_applier(candidates, open_positions, mode):
        calls.append([row["symbol"] for row in candidates])
        return {"autopilot_open_attempted": len(candidates), "autopilot_open_submitted": 0, "autopilot_open_blocked": 0, "autopilot_open_notes": "checked"}

    paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 0, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        strong_candidate_loader=lambda: [{"symbol": "RAW1"}],
        per_symbol_forecast_candidate_loader=lambda: [{"symbol": "FORECAST"}],
        near_miss_candidate_loader=lambda: [{"symbol": "NEAR"}],
        plan_candidate_loader=lambda: [{"symbol": "PLAN"}],
        execution_ranked_candidate_loader=lambda: [{"symbol": "BNY", "execution_rank": 1}],
        auto_open_applier=open_applier,
    )

    assert calls == [["BNY"]]


def test_runtime_gate_failure_scans_next_candidate_through_auto_open_applier(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = _csv(tmp_path / "tracking.csv", [{"symbol": "OLD", "alpaca_status": "filled"}])
    positions = _csv(tmp_path / "positions.csv", [])
    seen: list[str] = []

    def open_applier(candidates, open_positions, mode):
        for row in candidates:
            seen.append(row["symbol"])
            if row["symbol"] == "NEXT":
                return {"autopilot_open_attempted": 2, "autopilot_open_submitted": 1, "autopilot_open_blocked": 1, "autopilot_open_notes": "BNY:blocked:runtime_gate; NEXT:opened:test-order"}
        return {"autopilot_open_attempted": 1, "autopilot_open_submitted": 0, "autopilot_open_blocked": 1, "autopilot_open_notes": "candidate_blocked_runtime"}

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 0, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        execution_ranked_candidate_loader=lambda: [{"symbol": "BNY", "execution_rank": 1}, {"symbol": "NEXT", "execution_rank": 2}],
        auto_open_applier=open_applier,
    )

    assert seen == ["BNY", "NEXT"]
    assert state["autopilot_open_blocked"] == 1
    assert state["autopilot_open_submitted"] == 1


def test_submit_orders_false_logs_blocked_config_via_apply_open(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config(submit_orders=False))
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = _csv(tmp_path / "tracking.csv", [{"symbol": "OLD", "alpaca_status": "filled"}])
    positions = _csv(tmp_path / "positions.csv", [])

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 0, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        execution_ranked_candidate_loader=lambda: [{"symbol": "BNY", "execution_rank": 1}],
        auto_open_applier=lambda c, p, m: {
            "autopilot_open_attempted": 1,
            "autopilot_open_submitted": 0,
            "autopilot_open_blocked": 1,
            "autopilot_open_notes": "paper_autopilot_submit_blocked_config",
        },
    )

    assert state["autopilot_open_submitted"] == 0
    assert state["autopilot_open_notes"] == "paper_autopilot_submit_blocked_config"
