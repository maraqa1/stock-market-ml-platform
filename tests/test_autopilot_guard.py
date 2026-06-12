import json

from stockml.trading.autopilot_guard import (
    AUTOPILOT_BASKET_BLOCK_REASON,
    AUTOPILOT_SYMBOL_CONFLICT_REASON,
    autopilot_conflicting_symbols,
    autopilot_managed_symbols,
    reconcile_autopilot_state_from_tracking,
)


def test_autopilot_conflicts_only_managed_symbols(tmp_path):
    state_path = tmp_path / "paper_autopilot_state.json"
    state_path.write_text(
        json.dumps(
            {
                "status": "running",
                "mode": "paper_autopilot",
                "open_positions": 1,
                "open_orders": 0,
                "eod_dispositions": [{"symbol": "BNY"}],
                "position_peak_plpc": {"MRVL": 0.01},
            }
        ),
        encoding="utf-8",
    )

    assert autopilot_managed_symbols(state_path) == {"BNY", "MRVL"}
    conflicts, reason = autopilot_conflicting_symbols({"BNY", "FLEX"}, state_path)

    assert conflicts == {"BNY"}
    assert reason == AUTOPILOT_SYMBOL_CONFLICT_REASON


def test_autopilot_conflict_guard_blocks_all_when_position_symbols_unknown(tmp_path):
    state_path = tmp_path / "paper_autopilot_state.json"
    state_path.write_text(
        json.dumps(
            {
                "status": "running",
                "mode": "paper_autopilot",
                "open_positions": 1,
                "open_orders": 0,
            }
        ),
        encoding="utf-8",
    )

    conflicts, reason = autopilot_conflicting_symbols({"FLEX", "MRVL"}, state_path)

    assert conflicts == {"FLEX", "MRVL"}
    assert reason == AUTOPILOT_BASKET_BLOCK_REASON


def test_reconcile_clears_stale_symbols_when_broker_is_flat(tmp_path):
    state_path = tmp_path / "paper_autopilot_state.json"
    positions_path = tmp_path / "positions.csv"
    tracking_path = tmp_path / "tracking.csv"
    positions_path.write_text("", encoding="utf-8")
    tracking_path.write_text("symbol,status,alpaca_status,filled_qty\nCRCL,dry_run,,\nSBET,dry_run,,\n", encoding="utf-8")
    state_path.write_text(
        json.dumps(
            {
                "status": "running",
                "mode": "paper_autopilot",
                "phase": "waiting_for_fills",
                "open_positions": 1,
                "open_orders": 2,
                "eod_dispositions": [{"symbol": "CRCL"}, {"symbol": "SBET"}],
                "position_peak_plpc": {"CRCL": 0.04, "SBET": 0.03},
                "tracking_path": "old_tracking.csv",
                "positions_path": "old_positions.csv",
            }
        ),
        encoding="utf-8",
    )

    state = reconcile_autopilot_state_from_tracking(
        tracking_path=tracking_path,
        positions_path=positions_path,
        orders_tracked=2,
        state_path=state_path,
    )
    conflicts, reason = autopilot_conflicting_symbols({"CRCL", "SBET", "BNY"}, state_path)

    assert state["open_positions"] == 0
    assert state["open_orders"] == 0
    assert state["phase"] == "tracking_orders"
    assert state["eod_dispositions"] == []
    assert state["position_peak_plpc"] == {}
    assert conflicts == set()
    assert reason == ""


def test_reconcile_keeps_open_order_symbols_managed(tmp_path):
    state_path = tmp_path / "paper_autopilot_state.json"
    positions_path = tmp_path / "positions.csv"
    tracking_path = tmp_path / "tracking.csv"
    positions_path.write_text("", encoding="utf-8")
    tracking_path.write_text("symbol,status,alpaca_status,filled_qty\nCRCL,submitted,new,\n", encoding="utf-8")
    state_path.write_text(
        json.dumps(
            {
                "status": "running",
                "mode": "paper_autopilot",
                "open_positions": 0,
                "open_orders": 0,
            }
        ),
        encoding="utf-8",
    )

    reconcile_autopilot_state_from_tracking(
        tracking_path=tracking_path,
        positions_path=positions_path,
        orders_tracked=1,
        state_path=state_path,
    )
    conflicts, reason = autopilot_conflicting_symbols({"CRCL", "BNY"}, state_path)

    assert conflicts == {"CRCL"}
    assert reason == AUTOPILOT_SYMBOL_CONFLICT_REASON


def test_reconcile_does_not_count_canceled_or_filled_rows_as_open(tmp_path):
    state_path = tmp_path / "paper_autopilot_state.json"
    positions_path = tmp_path / "positions.csv"
    tracking_path = tmp_path / "tracking.csv"
    positions_path.write_text("", encoding="utf-8")
    tracking_path.write_text(
        "symbol,status,alpaca_status,filled_qty\n"
        "CRCL,submitted,canceled,0\n"
        "SBET,submitted,filled,242\n",
        encoding="utf-8",
    )
    state_path.write_text(
        json.dumps(
            {
                "status": "running",
                "mode": "paper_autopilot",
                "phase": "waiting_for_fills",
                "open_positions": 0,
                "open_orders": 2,
                "eod_dispositions": [{"symbol": "CRCL"}, {"symbol": "SBET"}],
            }
        ),
        encoding="utf-8",
    )

    state = reconcile_autopilot_state_from_tracking(
        tracking_path=tracking_path,
        positions_path=positions_path,
        orders_tracked=2,
        state_path=state_path,
    )
    conflicts, reason = autopilot_conflicting_symbols({"CRCL", "SBET"}, state_path)

    assert state["open_orders"] == 0
    assert state["tracked_open_orders"] == 0
    assert state["broker_open_orders"] == 0
    assert state["phase"] == "tracking_orders"
    assert conflicts == set()
    assert reason == ""
