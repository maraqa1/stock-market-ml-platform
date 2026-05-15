from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.monitor_trading_snapshots import ALERT, PASS, WARN, SnapshotComparator, TradingSnapshotMonitor, check_meta_label_suppression, check_pipeline_freeze, check_sizing_blockage, check_stale_data


def row(**overrides):
    payload = {
        "pool": "model_shortlist",
        "symbol": "AAA",
        "direction": "long",
        "outcome": "accepted",
        "raw_score": 0.5,
        "display_score": 0.5,
        "notional": 100,
        "quantity": 1,
        "data_age_seconds": 10,
        "raw_json": "{}",
    }
    payload.update(overrides)
    return payload


def test_snapshot_comparator_counts_field_changes():
    previous = pd.DataFrame([row(outcome="accepted", raw_score=0.5, notional=100, quantity=1)])
    current = pd.DataFrame([row(outcome="rejected", raw_score=0.6, display_score=0.6, notional=200, quantity=2)])

    counts = SnapshotComparator(previous, current).field_change_counts()

    assert counts == {"outcomes": 1, "scores": 2, "notional": 1, "quantity": 1}


def test_snapshot_comparator_tolerates_duplicate_keys():
    previous = pd.DataFrame([row(symbol="AAA"), row(symbol="AAA")])
    current = pd.DataFrame([row(symbol="AAA", outcome="rejected"), row(symbol="AAA", outcome="rejected")])

    counts = SnapshotComparator(previous, current).field_change_counts()

    assert counts["outcomes"] == 1


def test_pipeline_freeze_alerts_after_two_cycles():
    state = {}
    previous = pd.DataFrame([row()])
    current = pd.DataFrame([row()])

    first = check_pipeline_freeze(current, previous, state, freeze_cycles=2)
    second = check_pipeline_freeze(current, previous, state, freeze_cycles=2)

    assert first.status == WARN
    assert second.status == ALERT
    assert second.details["change_counts"]["outcomes"] == 0


def test_sizing_blockage_alerts_when_same_symbol_stuck():
    state = {}
    frame = pd.DataFrame([row(symbol="AAA", outcome="accepted", notional=None, quantity=None)])

    first = check_sizing_blockage(frame, state, sizing_block_cycles=2)
    second = check_sizing_blockage(frame, state, sizing_block_cycles=2)

    assert first.status == WARN
    assert second.status == ALERT
    assert "AAA" in second.details["symbols"]


def test_stale_data_alerts_on_four_hour_rows():
    state = {}
    frame = pd.DataFrame([row(symbol="OLD", data_age_seconds=15000)])

    result = check_stale_data(frame, state, stale_threshold_seconds=3600)

    assert result.status == ALERT
    assert "OLD" in result.details["symbols"]


def test_stale_data_alerts_on_twenty_percent_increase():
    state = {"last_stale_count": 10}
    frame = pd.DataFrame([row(symbol=f"S{i}", data_age_seconds=4000) for i in range(13)])

    result = check_stale_data(frame, state, stale_threshold_seconds=3600)

    assert result.status == ALERT
    assert result.details["previous_stale_count"] == 10
    assert result.details["stale_count"] == 13


def test_meta_label_suppression_alerts_after_three_low_cycles():
    state = {}
    frame = pd.DataFrame([row(raw_json=json.dumps({"meta_label_probability": 0.4}))])

    check_meta_label_suppression(frame, state, meta_label_cycles=3)
    check_meta_label_suppression(frame, state, meta_label_cycles=3)
    result = check_meta_label_suppression(frame, state, meta_label_cycles=3)

    assert result.status == ALERT
    assert result.details["low_streak"] == 3


def test_monitor_persists_state_and_log(tmp_path: Path):
    directory = tmp_path / "snapshots"
    directory.mkdir()
    pd.DataFrame([row()]).to_csv(directory / "trading_snapshot_20260515_120000.csv", index=False)

    monitor = TradingSnapshotMonitor(directory, state_path=tmp_path / "state.json", log_path=tmp_path / "monitor.log")
    results = monitor.process_new_files()

    assert results
    assert (tmp_path / "state.json").exists()
    assert (tmp_path / "monitor.log").exists()
    assert "pipeline_freeze" in (tmp_path / "monitor.log").read_text(encoding="utf-8")
