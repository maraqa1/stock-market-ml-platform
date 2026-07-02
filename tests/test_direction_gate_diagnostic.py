from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.diagnostics.direction_gate_diagnostic import build_direction_gate_diagnostic, run_direction_gate_diagnostic


def candidate(**overrides):
    data = {
        "symbol": "AAA",
        "side": "buy",
        "rank_overall": 1,
        "source_trade_action": "Long",
        "trade_action": "Long",
        "validated_expected_return_bps": 30.0,
        "validated_profit_factor": 1.30,
        "validated_hit_rate": 0.55,
        "expected_return_quality": "usable",
        "calibration_quality": "usable",
        "trade_quality_status": "approved",
    }
    data.update(overrides)
    return data


def test_direction_gate_diagnostic_marks_pass_and_after_executable():
    frame = pd.DataFrame([candidate()])
    out = build_direction_gate_diagnostic(frame)
    row = out.iloc[0]
    assert row["direction_decision"] == "direction_pass"
    assert bool(row["executable_before_direction_gate"]) is True
    assert bool(row["executable_after_direction_gate"]) is True


def test_direction_gate_diagnostic_blocks_no_decision_after_gate():
    frame = pd.DataFrame([candidate(symbol="NOD", source_trade_action="No Decision", trade_action="Long")])
    out = build_direction_gate_diagnostic(frame)
    row = out.iloc[0]
    assert row["direction_decision"] == "direction_research_only"
    assert bool(row["executable_before_direction_gate"]) is True
    assert bool(row["executable_after_direction_gate"]) is False
    assert row["direction_primary_reason"] == "planner_derived_action_without_source_approval"


def test_direction_gate_diagnostic_short_research_only():
    frame = pd.DataFrame([candidate(symbol="SHORT", side="sell", source_trade_action="Short", trade_action="Short")])
    out = build_direction_gate_diagnostic(frame)
    row = out.iloc[0]
    assert row["direction_decision"] == "direction_research_only"
    assert row["direction_primary_reason"] == "short_side_validation_required"


def test_run_direction_gate_diagnostic_writes_outputs(tmp_path: Path, monkeypatch):
    portal = tmp_path / "data" / "portal_outputs"
    portal.mkdir(parents=True)
    pd.DataFrame(
        [
            candidate(symbol="PASS"),
            candidate(symbol="NOD", source_trade_action="No Decision", trade_action="Long"),
            candidate(symbol="SHORT", side="sell", source_trade_action="Short", trade_action="Short"),
        ]
    ).to_csv(portal / "08_alpaca_paper_candidate_pool_20260702_120000.csv", index=False)
    result = run_direction_gate_diagnostic(root=tmp_path, output_dir=tmp_path / "out", stamp="20260702_120500")
    assert result["status"] == "ok"
    assert result["rows"] == 3
    assert result["direction_pass"] == 1
    assert result["direction_research_only"] == 2
    csv_path = Path(result["csv_path"])
    md_path = Path(result["markdown_path"])
    assert csv_path.exists()
    assert md_path.exists()
    csv = pd.read_csv(csv_path)
    assert set(csv["symbol"]) == {"PASS", "NOD", "SHORT"}
