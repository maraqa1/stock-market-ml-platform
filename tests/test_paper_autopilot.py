from __future__ import annotations

import pandas as pd

from stockml.trading.config import AlpacaConfig
from stockml.trading import paper_autopilot


def _config(live: bool = False) -> AlpacaConfig:
    return AlpacaConfig(
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
        live_trading_enabled=live,
        paper_trading_enabled=True,
    )


def _position(symbol: str, qty: int = 1, unrealized_plpc: float = 0.0, **updates) -> dict:
    row = {
        "symbol": symbol,
        "qty": qty,
        "avg_entry_price": 100.0,
        "current_price": 100.0 * (1 + float(unrealized_plpc)),
        "unrealized_plpc": unrealized_plpc,
        "side": "long" if qty >= 0 else "short",
    }
    row.update(updates)
    return row


def test_paper_autopilot_start_is_paper_only(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())

    state = paper_autopilot.start(tmp_path)

    assert state["mode"] == "observe"
    assert state["status"] == "running"
    assert state["phase"] == "tracking_orders"
    assert state["paper_only"] is True
    assert state["live_trading_enabled"] is False


def test_paper_autopilot_refuses_live_config(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config(live=True))

    state = paper_autopilot.start(tmp_path)

    assert state["status"] == "stopped"
    assert state["phase"] == "guardrail_stop"
    assert state["termination_reason"] == "live_trading_enabled_guardrail"


def test_paper_autopilot_tick_waits_for_fills(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    state = paper_autopilot.start(tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "accepted"}]).to_csv(tracking, index=False)
    pd.DataFrame([{"symbol": "AAA", "qty": 1}]).to_csv(positions, index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        intraday_decision_loader=lambda: {"intraday_allows": 2, "intraday_blocks": 1, "latest_intraday_at": "2026-05-11T15:00:00+00:00"},
        monitor_decision_loader=lambda root: {"monitor_actions": 3, "monitor_close": 1, "monitor_rotate": 1, "monitor_watch": 1, "latest_monitor_at": "2026-05-11T15:00:00+00:00"},
    )

    assert state["status"] == "running"
    assert state["phase"] == "monitoring_positions"
    assert state["open_orders"] == 1
    assert state["tracked_open_orders"] == 1
    assert state["broker_open_orders"] == 0
    assert state["open_positions"] == 1
    assert state["intraday_allows"] == 2
    assert state["intraday_blocks"] == 1
    assert state["monitor_actions"] == 3
    assert state["monitor_close"] == 1
    logs = paper_autopilot.recent_tick_logs(tmp_path)
    assert logs[0]["phase"] == "waiting_for_fills"
    assert logs[0]["mode"] == "observe"
    assert logs[0]["open_orders"] == 1
    assert logs[0]["intraday_allows"] == 2
    assert logs[0]["monitor_actions"] == 3


def test_paper_autopilot_tick_terminates_when_flat(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([]).to_csv(positions, index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
    )

    assert state["status"] == "complete"
    assert state["phase"] == "cycle_complete"
    assert state["termination_reason"] == "no_open_orders_or_positions"


def test_paper_autopilot_mode_stays_running_when_flat(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([]).to_csv(positions, index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        allow_auto_open=False,
    )

    assert state["status"] == "running"
    assert state["phase"] == "tracking_orders"
    assert state["termination_reason"] == ""


def test_paper_autopilot_cancels_stale_entries_before_waiting_for_fills(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    first_tracking = tmp_path / "tracking_first.csv"
    second_tracking = tmp_path / "tracking_second.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "new"}]).to_csv(first_tracking, index=False)
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "canceled"}]).to_csv(second_tracking, index=False)
    pd.DataFrame([]).to_csv(positions, index=False)
    refreshes = [
        {"orders_tracked": 1, "tracking_path": first_tracking, "positions_path": positions},
        {"orders_tracked": 1, "tracking_path": second_tracking, "positions_path": positions},
    ]

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: refreshes.pop(0),
        broker_open_orders_func=lambda cfg: 0,
        stale_entry_maintainer=lambda: {
            "stale_entry_status": "ok",
            "stale_entry_open_orders": 1,
            "stale_entry_candidates": 1,
            "stale_entry_canceled": 1,
            "stale_entry_skipped": 0,
            "stale_entry_error": 0,
            "stale_entry_notes": "AAA:canceled:31.0m",
            "stale_entry_path": "data/trading/stale_entry_orders/stale_entry_orders_test.csv",
        },
        allow_auto_open=False,
    )

    assert state["status"] == "running"
    assert state["phase"] == "tracking_orders"
    assert state["open_orders"] == 0
    assert state["tracked_open_orders"] == 0
    assert state["stale_entry_canceled"] == 1
    assert state["stale_entry_candidates"] == 1
    assert state["stale_entry_notes"] == "AAA:canceled:31.0m"
    assert state["tracking_path"].endswith("tracking_second.csv")


def test_paper_autopilot_tick_counts_direct_broker_orders(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([{"symbol": "AAA", "qty": 1}]).to_csv(positions, index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 5,
    )

    assert state["status"] == "running"
    assert state["phase"] == "monitoring_positions"
    assert state["open_orders"] == 5
    assert state["tracked_open_orders"] == 0
    assert state["broker_open_orders"] == 5


def test_paper_autopilot_mode_auto_closes_close_decisions(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([_position("AAA"), _position("BBB")]).to_csv(positions, index=False)
    decisions = tmp_path / "data" / "trading" / "agent_decisions"
    decisions.mkdir(parents=True)
    pd.DataFrame(
        [
            {"symbol": "AAA", "decision": "close"},
            {"symbol": "BBB", "decision": "watch"},
        ]
    ).to_csv(decisions / "position_decisions_1.csv", index=False)
    calls = []

    def apply_close(root, frame, state):
        calls.append(frame["symbol"].tolist())
        return paper_autopilot.apply_paper_autopilot_decisions(
            root,
            frame,
            state=state,
            action_func=lambda symbol, action: {"status": "submitted", "message": f"auto_{action}", "order_id": "order-1"},
        )

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        autopilot_decision_applier=apply_close,
    )

    assert calls == [["AAA", "BBB"]]
    assert state["mode"] == "paper_autopilot"
    assert state["phase"] == "waiting_for_fills"
    assert state["autopilot_actions"] == 1
    assert state["autopilot_close_submitted"] == 1
    assert state["autopilot_defensive_close_submitted"] == 0
    assert "AAA:monitor_close:submitted:auto_close" in state["autopilot_action_notes"]


def test_paper_autopilot_mode_defensively_closes_stale_losers(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([_position("AAA", unrealized_plpc=-0.031), _position("BBB", unrealized_plpc=-0.010)]).to_csv(positions, index=False)
    decisions = tmp_path / "data" / "trading" / "agent_decisions"
    decisions.mkdir(parents=True)
    pd.DataFrame(
        [
            {"symbol": "AAA", "decision": "watch", "decision_reason": "signal_stale", "unrealized_plpc": -0.031},
            {"symbol": "BBB", "decision": "watch", "decision_reason": "signal_stale", "unrealized_plpc": -0.010},
        ]
    ).to_csv(decisions / "position_decisions_1.csv", index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        autopilot_decision_applier=lambda root, frame, state: paper_autopilot.apply_paper_autopilot_decisions(
            root,
            frame,
            state=state,
            action_func=lambda symbol, action: {"status": "submitted", "message": f"auto_{action}", "order_id": "order-1"},
        ),
    )

    assert state["phase"] == "waiting_for_fills"
    assert state["autopilot_actions"] == 0
    assert state["autopilot_close_submitted"] == 0
    assert state["autopilot_defensive_close_submitted"] == 0
    assert "defensive_stale_loss:submitted" not in state.get("autopilot_action_notes", "")


def test_paper_autopilot_mode_defensively_closes_unknown_signal_losers(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([_position("AAA", unrealized_plpc=-0.021), _position("BBB", unrealized_plpc=-0.010)]).to_csv(positions, index=False)
    decisions = tmp_path / "data" / "trading" / "agent_decisions"
    decisions.mkdir(parents=True)
    pd.DataFrame(
        [
            {"symbol": "AAA", "decision": "watch", "decision_reason": "latest_signal_unknown", "unrealized_plpc": -0.021},
            {"symbol": "BBB", "decision": "watch", "decision_reason": "latest_signal_unknown", "unrealized_plpc": -0.010},
        ]
    ).to_csv(decisions / "position_decisions_1.csv", index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        autopilot_decision_applier=lambda root, frame, state: paper_autopilot.apply_paper_autopilot_decisions(
            root,
            frame,
            state=state,
            action_func=lambda symbol, action: {"status": "submitted", "message": f"auto_{action}", "order_id": "order-1"},
        ),
    )

    assert state["phase"] == "waiting_for_fills"
    assert state["autopilot_actions"] == 0
    assert state["autopilot_close_submitted"] == 0
    assert state["autopilot_defensive_close_submitted"] == 0
    assert "defensive_stale_loss:submitted" not in state.get("autopilot_action_notes", "")


def test_paper_autopilot_mode_auto_closes_position_health_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "FWRD", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([_position("FWRD", unrealized_plpc=-0.0233, holding_quality="avoid")]).to_csv(positions, index=False)
    decisions = tmp_path / "data" / "trading" / "agent_decisions"
    decisions.mkdir(parents=True)
    pd.DataFrame(
        [{"symbol": "FWRD", "decision": "watch", "decision_reason": "signal_stale", "unrealized_plpc": -0.0233}]
    ).to_csv(decisions / "position_decisions_1.csv", index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        autopilot_decision_applier=lambda root, frame, state: paper_autopilot.apply_paper_autopilot_decisions(
            root,
            frame,
            state=state,
            action_func=lambda symbol, action: {"status": "submitted", "message": f"auto_{action}", "order_id": "order-1"},
        ),
    )

    assert state["phase"] == "waiting_for_fills"
    assert state["autopilot_close_submitted"] == 1
    assert state["autopilot_health_close_submitted"] == 1
    assert "FWRD:losing_position_edge_failed:submitted:auto_close" in state["autopilot_action_notes"]


def test_paper_autopilot_review_only_does_not_auto_close_health_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "autopilot.yaml").write_text(
        "version: 1\nautopilot:\n  close_automation_mode: review_only\n",
        encoding="utf-8",
    )
    positions = pd.DataFrame([_position("FWRD", unrealized_plpc=-0.0233, holding_quality="avoid")])
    decisions = tmp_path / "data" / "trading" / "agent_decisions"
    decisions.mkdir(parents=True)
    pd.DataFrame(
        [{"symbol": "FWRD", "decision": "watch", "decision_reason": "signal_stale", "unrealized_plpc": -0.0233}]
    ).to_csv(decisions / "position_decisions_1.csv", index=False)

    result = paper_autopilot.apply_paper_autopilot_decisions(
        tmp_path,
        positions,
        state=paper_autopilot.load_state(tmp_path),
        action_func=lambda symbol, action: {"status": "submitted", "message": f"auto_{action}", "order_id": "order-1"},
    )

    assert result["autopilot_actions"] == 0
    assert result["autopilot_close_submitted"] == 0


def test_paper_autopilot_mode_closes_hard_stop_losers(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([_position("AAA", unrealized_plpc=-0.045)]).to_csv(positions, index=False)
    decisions = tmp_path / "data" / "trading" / "agent_decisions"
    decisions.mkdir(parents=True)
    pd.DataFrame([{"symbol": "AAA", "decision": "watch", "decision_reason": "position_within_rules", "unrealized_plpc": -0.045}]).to_csv(decisions / "position_decisions_1.csv", index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        autopilot_decision_applier=lambda root, frame, state: paper_autopilot.apply_paper_autopilot_decisions(
            root,
            frame,
            state=state,
            action_func=lambda symbol, action: {"status": "submitted", "message": f"auto_{action}", "order_id": "order-1"},
        ),
    )

    assert state["autopilot_hard_stop_submitted"] == 1
    assert "AAA:hard_stop_hit:submitted:auto_close" in state["autopilot_action_notes"]


def test_paper_autopilot_mode_protects_stale_winners_that_give_back(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    state = paper_autopilot.load_state(tmp_path)
    state["position_peak_plpc"] = {"AAA": 0.052}
    paper_autopilot.save_state(state, tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([_position("AAA", unrealized_plpc=0.034, peak_pnl_pct=0.052)]).to_csv(positions, index=False)
    decisions = tmp_path / "data" / "trading" / "agent_decisions"
    decisions.mkdir(parents=True)
    pd.DataFrame([{"symbol": "AAA", "decision": "watch", "decision_reason": "signal_stale", "unrealized_plpc": 0.034}]).to_csv(decisions / "position_decisions_1.csv", index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        autopilot_decision_applier=lambda root, frame, state: paper_autopilot.apply_paper_autopilot_decisions(
            root,
            frame,
            state=state,
            action_func=lambda symbol, action: {"status": "submitted", "message": f"auto_{action}", "order_id": "order-1"},
        ),
    )

    assert state["autopilot_trailing_close_submitted"] == 0
    assert "trailing_profit_giveback:submitted" not in state.get("autopilot_action_notes", "")


def test_paper_autopilot_trailing_profit_uses_configured_thresholds(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "autopilot.yaml").write_text(
        "version: 1\nautopilot:\n  trailing_profit_arm_pct: 2.0\n  trailing_profit_giveback_pct: 0.5\n",
        encoding="utf-8",
    )
    state = paper_autopilot.load_state(tmp_path)
    state["position_peak_plpc"] = {"AAA": 0.022}
    paper_autopilot.save_state(state, tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([_position("AAA", unrealized_plpc=0.016, peak_pnl_pct=0.022)]).to_csv(positions, index=False)
    decisions = tmp_path / "data" / "trading" / "agent_decisions"
    decisions.mkdir(parents=True)
    pd.DataFrame([{"symbol": "AAA", "decision": "watch", "decision_reason": "signal_stale", "unrealized_plpc": 0.016}]).to_csv(decisions / "position_decisions_1.csv", index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        autopilot_decision_applier=lambda root, frame, state: paper_autopilot.apply_paper_autopilot_decisions(
            root,
            frame,
            state=state,
            action_func=lambda symbol, action: {"status": "submitted", "message": f"auto_{action}", "order_id": "order-1"},
        ),
    )

    assert state["autopilot_trailing_close_submitted"] == 0
    assert "trailing_profit_giveback:submitted" not in state.get("autopilot_action_notes", "")


def test_paper_autopilot_fresh_winner_requires_larger_giveback(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "autopilot.yaml").write_text(
        "version: 1\nautopilot:\n  trailing_profit_arm_pct: 2.0\n  trailing_profit_giveback_pct: 1.0\n",
        encoding="utf-8",
    )
    state = paper_autopilot.load_state(tmp_path)
    state["position_peak_plpc"] = {"AAA": 0.045}
    paper_autopilot.save_state(state, tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([_position("AAA", unrealized_plpc=0.031, peak_pnl_pct=0.045)]).to_csv(positions, index=False)
    decisions = tmp_path / "data" / "trading" / "agent_decisions"
    decisions.mkdir(parents=True)
    pd.DataFrame([{"symbol": "AAA", "decision": "hold", "decision_reason": "position_within_rules", "unrealized_plpc": 0.031}]).to_csv(decisions / "position_decisions_1.csv", index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        autopilot_decision_applier=lambda root, frame, state: paper_autopilot.apply_paper_autopilot_decisions(
            root,
            frame,
            state=state,
            action_func=lambda symbol, action: {"status": "submitted", "message": f"auto_{action}", "order_id": "order-1"},
        ),
    )

    assert state["autopilot_trailing_close_submitted"] == 0
    assert "trailing_profit_giveback:submitted" not in state.get("autopilot_action_notes", "")


def test_paper_autopilot_mode_protects_unknown_signal_winners_that_give_back(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    state = paper_autopilot.load_state(tmp_path)
    state["position_peak_plpc"] = {"AAA": 0.035}
    paper_autopilot.save_state(state, tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([_position("AAA", unrealized_plpc=0.002, peak_pnl_pct=0.035)]).to_csv(positions, index=False)
    decisions = tmp_path / "data" / "trading" / "agent_decisions"
    decisions.mkdir(parents=True)
    pd.DataFrame([{"symbol": "AAA", "decision": "watch", "decision_reason": "latest_signal_unknown", "unrealized_plpc": 0.002}]).to_csv(decisions / "position_decisions_1.csv", index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        autopilot_decision_applier=lambda root, frame, state: paper_autopilot.apply_paper_autopilot_decisions(
            root,
            frame,
            state=state,
            action_func=lambda symbol, action: {"status": "submitted", "message": f"auto_{action}", "order_id": "order-1"},
        ),
    )

    assert state["autopilot_trailing_close_submitted"] == 0
    assert "trailing_profit_giveback:submitted" not in state.get("autopilot_action_notes", "")


def test_paper_autopilot_mode_closes_replace_recommendations_when_rotation_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "autopilot.yaml").write_text(
        "version: 1\nautopilot:\n  open_enabled: false\n  rotate_enabled: true\n",
        encoding="utf-8",
    )
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([_position("AAA"), _position("BBB")]).to_csv(positions, index=False)
    decisions = tmp_path / "data" / "trading" / "agent_decisions"
    decisions.mkdir(parents=True)
    pd.DataFrame(
        [
            {"symbol": "AAA", "decision": "replace", "decision_reason": "signal_stale|replacement_rank_improvement", "replacement_symbol": "CCC"},
            {"symbol": "BBB", "decision": "watch", "decision_reason": "signal_stale", "unrealized_plpc": -0.010},
        ]
    ).to_csv(decisions / "position_decisions_1.csv", index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        autopilot_decision_applier=lambda root, frame, state: paper_autopilot.apply_paper_autopilot_decisions(
            root,
            frame,
            state=state,
            action_func=lambda symbol, action: {"status": "submitted", "message": f"auto_{action}", "order_id": "order-1"},
        ),
    )

    assert state["phase"] == "monitoring_positions"
    assert state["autopilot_close_submitted"] == 0
    assert state["autopilot_replace_close_submitted"] == 0
    assert "monitor_replace:submitted" not in state.get("autopilot_action_notes", "")


def test_paper_autopilot_does_not_close_replace_when_rotation_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "autopilot.yaml").write_text(
        "version: 1\nautopilot:\n  open_enabled: false\n  rotate_enabled: false\n",
        encoding="utf-8",
    )
    positions = pd.DataFrame([_position("AAA")])
    decisions = tmp_path / "data" / "trading" / "agent_decisions"
    decisions.mkdir(parents=True)
    pd.DataFrame([{"symbol": "AAA", "decision": "replace", "decision_reason": "signal_stale|replacement_rank_improvement"}]).to_csv(
        decisions / "position_decisions_1.csv",
        index=False,
    )

    result = paper_autopilot.apply_paper_autopilot_decisions(
        tmp_path,
        positions,
        state=paper_autopilot.load_state(tmp_path),
        action_func=lambda symbol, action: {"status": "submitted", "message": f"auto_{action}", "order_id": "order-1"},
    )

    assert result["autopilot_actions"] == 0
    assert result["autopilot_close_submitted"] == 0


def test_paper_assist_does_not_auto_close_close_decisions(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_assist", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([{"symbol": "AAA", "qty": 1}]).to_csv(positions, index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        autopilot_decision_applier=lambda root, frame, state: {"autopilot_actions": 9, "autopilot_close_submitted": 9, "autopilot_action_notes": "should_not_run"},
    )

    assert state["phase"] == "monitoring_positions"
    assert state["autopilot_actions"] == 0
    assert state["autopilot_close_submitted"] == 0


def test_paper_autopilot_runs_eod_policy_in_autopilot_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame([{"symbol": "AAA", "alpaca_status": "filled"}]).to_csv(tracking, index=False)
    pd.DataFrame([{"symbol": "AAA", "qty": 1}]).to_csv(positions, index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 1, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        eod_runner=lambda frame, current_state, open_orders: {
            "eod_state": "flatten",
            "eod_banner": "EOD flatten in progress: closing 1 positions.",
            "eod_actions": 1,
            "eod_flatten_submitted": 1,
            "eod_remaining": 1,
            "eod_action_notes": "AAA:eod_flatten:submitted",
        },
    )

    assert state["phase"] == "waiting_for_fills"
    assert state["open_orders"] == 1
    assert state["eod_state"] == "flatten"
    assert state["eod_flatten_submitted"] == 1
    assert state["eod_banner"] == "EOD flatten in progress: closing 1 positions."


def test_paper_autopilot_logs_not_running_ticks(tmp_path):
    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 0, "tracking_path": "", "positions_path": ""},
        broker_open_orders_func=lambda cfg: 0,
    )

    assert state["last_error"] == "autopilot_not_running"
    logs = paper_autopilot.recent_tick_logs(tmp_path)
    assert len(logs) == 1
    assert logs[0]["last_error"] == "autopilot_not_running"


def test_paper_autopilot_mode_can_be_switched(tmp_path):
    state = paper_autopilot.set_mode("paper_autopilot", tmp_path)

    assert state["mode"] == "paper_autopilot"
    view = paper_autopilot.context(tmp_path)
    assert view["mode"] == "paper_autopilot"
    assert view["mode_label"] == "Paper Autopilot"
    assert {option["value"] for option in view["mode_options"]} == {
        "observe",
        "paper_assist",
        "paper_autopilot",
        "ai_gated_paper",
    }


def test_paper_autopilot_rejects_unknown_mode(tmp_path):
    paper_autopilot.set_mode("observe", tmp_path)

    state = paper_autopilot.set_mode("live", tmp_path)

    assert state["mode"] == "observe"
    assert state["last_error"] == "unsupported_autopilot_mode:live"


def test_monitor_decision_summary_reads_latest_position_decisions(tmp_path):
    decisions = tmp_path / "data" / "trading" / "agent_decisions"
    decisions.mkdir(parents=True)
    pd.DataFrame(
        [
            {"symbol": "AAA", "decision": "watch"},
            {"symbol": "BBB", "decision": "close"},
            {"symbol": "CCC", "decision": "rotate"},
            {"symbol": "DDD", "decision": "hold"},
        ]
    ).to_csv(decisions / "position_decisions_1.csv", index=False)

    summary = paper_autopilot.load_monitor_decision_summary(tmp_path)

    assert summary["monitor_actions"] == 3
    assert summary["monitor_watch"] == 1
    assert summary["monitor_close"] == 1
    assert summary["monitor_rotate"] == 1


def test_paper_autopilot_appends_fallback_candidates_after_strong_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame(columns=["symbol", "alpaca_status"]).to_csv(tracking, index=False)
    pd.DataFrame(columns=["symbol", "qty"]).to_csv(positions, index=False)
    captured = {}

    def apply_open(candidates, positions_records, mode):
        captured["symbols"] = [candidate["symbol"] for candidate in candidates]
        captured["mode"] = mode
        return {
            "autopilot_open_attempted": len(candidates),
            "autopilot_open_submitted": 0,
            "autopilot_open_blocked": len(candidates),
            "autopilot_open_notes": "all_blocked_for_test",
        }

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 0, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        strong_candidate_loader=lambda: [{"symbol": "LPRO", "promotion_score": 1.0}],
        per_symbol_forecast_candidate_loader=lambda: [{"symbol": "RXT", "promotion_score": 0.9}],
        near_miss_candidate_loader=lambda: [{"symbol": "RXT", "promotion_score": 0.8}],
        plan_candidate_loader=lambda: [{"symbol": "BNY", "promotion_score": 0.7}],
        fallback_candidate_loader=lambda: [{"symbol": "FLAT", "promotion_score": 0.6}],
        auto_open_applier=apply_open,
    )

    assert captured["mode"] == "paper_autopilot"
    assert captured["symbols"] == ["LPRO", "RXT", "BNY"]
    assert state["autopilot_open_attempted"] == 3
    assert state["autopilot_open_blocked"] == 3


def test_paper_autopilot_clears_stale_basket_pause_when_auto_open_hits_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(paper_autopilot, "alpaca_config", lambda: _config())
    state = paper_autopilot.start(tmp_path)
    state.update(
        {
            "mode": "paper_autopilot",
            "basket_state": "new_entries_paused",
            "new_entries_paused": True,
            "basket_risk_reason": "small_book_basket_return_pause",
            "basket_risk_reason_text": "stale pause",
        }
    )
    paper_autopilot.save_state(state, tmp_path)
    tracking = tmp_path / "tracking.csv"
    positions = tmp_path / "positions.csv"
    pd.DataFrame(columns=["symbol", "alpaca_status"]).to_csv(tracking, index=False)
    pd.DataFrame(columns=["symbol", "qty"]).to_csv(positions, index=False)

    state = paper_autopilot.tick(
        tmp_path,
        refresh_func=lambda: {"orders_tracked": 0, "tracking_path": tracking, "positions_path": positions},
        broker_open_orders_func=lambda cfg: 0,
        strong_candidate_loader=lambda: [{"symbol": "BNY", "promotion_score": 1.0}],
        per_symbol_forecast_candidate_loader=lambda: [],
        near_miss_candidate_loader=lambda: [],
        plan_candidate_loader=lambda: [],
        fallback_candidate_loader=lambda: [],
        auto_open_applier=lambda candidates, positions, mode: {
            "autopilot_open_attempted": 0,
            "autopilot_open_submitted": 0,
            "autopilot_open_blocked": 0,
            "autopilot_open_notes": "validation_daily_auto_open_cap_reached",
        },
    )

    assert state["basket_state"] == "normal"
    assert state["new_entries_paused"] is False
    assert state["basket_risk_reason"] == ""
    assert state["basket_risk_reason_text"] == ""
    assert state["autopilot_open_notes"] == "validation_daily_auto_open_cap_reached"
