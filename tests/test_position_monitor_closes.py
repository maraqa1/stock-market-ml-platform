from __future__ import annotations

import pandas as pd

from scripts.run_position_monitor import _read_csv
from stockml.trading import paper_autopilot
from stockml.trading.position_monitor_closes import execute_position_monitor_closes


def _write_decisions(root, rows):
    directory = root / "data" / "trading" / "agent_decisions"
    directory.mkdir(parents=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(directory / "position_decisions_1.csv", index=False)
    return frame


def test_position_monitor_read_csv_treats_empty_file_as_empty_frame(tmp_path):
    path = tmp_path / "empty_positions.csv"
    path.write_text("", encoding="utf-8")

    frame = _read_csv(path)

    assert frame.empty


def test_position_monitor_uses_paper_autopilot_trailing_profit(tmp_path):
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    state = paper_autopilot.load_state(tmp_path)
    state["position_peak_plpc"] = {"AAA": 0.047}
    paper_autopilot.save_state(state, tmp_path)
    positions = pd.DataFrame([{"symbol": "AAA", "qty": 1, "unrealized_plpc": 0.026}])
    decisions = _write_decisions(
        tmp_path,
        [{"symbol": "AAA", "decision": "watch", "decision_reason": "signal_stale", "unrealized_plpc": 0.026}],
    )
    calls = []

    result = execute_position_monitor_closes(
        positions,
        decisions,
        root=tmp_path,
        action_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted", "message": "auto_close"},
    )
    state = paper_autopilot.load_state(tmp_path)

    assert result["auto_close_status"] == "paper_autopilot"
    assert result["auto_close_submitted"] == 1
    assert result["autopilot_trailing_close_submitted"] == 1
    assert "AAA:trailing_profit_giveback:submitted:auto_close" in result["auto_close_notes"]
    assert calls == [("AAA", "close")]
    assert state["phase"] == "waiting_for_fills"
    assert state["autopilot_trailing_close_submitted"] == 1


def test_position_monitor_paper_autopilot_skips_symbols_with_open_orders(tmp_path):
    paper_autopilot.start(tmp_path)
    paper_autopilot.set_mode("paper_autopilot", tmp_path)
    state = paper_autopilot.load_state(tmp_path)
    state["position_peak_plpc"] = {"AAA": 0.047}
    paper_autopilot.save_state(state, tmp_path)
    positions = pd.DataFrame([{"symbol": "AAA", "qty": 1, "unrealized_plpc": 0.026}])
    decisions = _write_decisions(
        tmp_path,
        [{"symbol": "AAA", "decision": "watch", "decision_reason": "signal_stale", "unrealized_plpc": 0.026}],
    )
    calls = []

    result = execute_position_monitor_closes(
        positions,
        decisions,
        root=tmp_path,
        active_order_symbols={"AAA"},
        action_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted"},
    )

    assert result["auto_close_status"] == "paper_autopilot"
    assert result["auto_close_submitted"] == 0
    assert result["auto_close_skipped_existing"] == 1
    assert "AAA:trailing_profit_giveback:skipped:active_order_exists" in result["auto_close_notes"]
    assert calls == []


def test_position_monitor_falls_back_to_monitor_auto_close_when_autopilot_inactive(tmp_path):
    positions = pd.DataFrame([{"symbol": "AAA", "qty": 1, "unrealized_plpc": -0.03}])
    decisions = pd.DataFrame([{"symbol": "AAA", "decision": "close", "recommended_action": "close_position"}])
    calls = []

    result = execute_position_monitor_closes(
        positions,
        decisions,
        root=tmp_path,
        action_func=lambda symbol, action: calls.append((symbol, action)) or {"status": "submitted", "message": "manual_close"},
    )

    assert result["auto_close_status"] == "ok"
    assert result["auto_close_submitted"] == 1
    assert calls == [("AAA", "close")]
