import json

from stockml.trading.autopilot_guard import (
    AUTOPILOT_BASKET_BLOCK_REASON,
    AUTOPILOT_SYMBOL_CONFLICT_REASON,
    autopilot_conflicting_symbols,
    autopilot_managed_symbols,
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
