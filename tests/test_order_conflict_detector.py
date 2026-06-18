from __future__ import annotations

from datetime import datetime, timezone

from stockml.autopilot.order_conflict_detector import block_conflicting_actions, detect_order_conflicts

NOW = datetime(2026, 6, 18, 14, 30, tzinfo=timezone.utc)


def test_conflict_detector_blocks_same_symbol_open_close_cycle():
    actions = [
        {"symbol": "AAA", "action": "open", "side": "buy"},
        {"symbol": "AAA", "action": "close", "side": "sell"},
        {"symbol": "BBB", "action": "open", "side": "buy"},
    ]
    allowed, report = block_conflicting_actions(actions, now=NOW, cycle_id="cycle")
    assert [row["symbol"] for row in allowed] == ["BBB"]
    assert set(report["symbol"]) == {"AAA"}
    assert set(report["reason"]) == {"same_cycle_open_close"}


def test_detect_order_conflicts_returns_report_only():
    report = detect_order_conflicts([
        {"symbol": "AAA", "action": "open", "side": "buy"},
        {"symbol": "AAA", "action": "close", "side": "sell"},
    ], now=NOW)
    assert len(report) == 2
    assert report.iloc[0]["decision"] == "manual_review"
