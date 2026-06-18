from pathlib import Path

import pandas as pd

from stockml.diagnostics.inverse_strategy_diagnostic import REQUIRED_COLUMNS, build_inverse_strategy, build_inverse_strategy_report, summarize_inverse
from stockml.diagnostics.side_mapping_audit import inverse_action


def test_long_inverse_becomes_short():
    assert inverse_action("Long") == "Short"


def test_short_inverse_becomes_long():
    assert inverse_action("Short") == "Long"


def test_no_decision_does_not_become_executable_unless_explicitly_allowed():
    assert inverse_action("No Decision") == "No Decision"


def test_close_long_inverse_not_treated_as_new_short_unless_allowed():
    assert inverse_action("close Long") == "No Executable Inverse"
    assert inverse_action("close Long", allow_close_inverse=True) == "Open Short"


def test_close_short_inverse_not_treated_as_new_long_unless_allowed():
    assert inverse_action("close Short") == "No Executable Inverse"
    assert inverse_action("close Short", allow_close_inverse=True) == "Open Long"


def test_inverse_report_writes_required_columns(tmp_path: Path, monkeypatch):
    source = tmp_path / "tracking.csv"
    pd.DataFrame([
        {"symbol": "KRMN", "side": "sell", "filled_qty": 36, "filled_avg_price": 52.0, "current_price": 53.95, "submitted_at": "2026-06-18T12:58:02Z", "extended_hours": True, "trade_action": "Short"}
    ]).to_csv(source, index=False)
    monkeypatch.setattr("stockml.diagnostics.inverse_strategy_diagnostic.MODEL_OUTPUTS_DIR", tmp_path)
    output = build_inverse_strategy_report("20260618_010101", source_file=source)
    frame = pd.read_csv(output.path)
    assert set(REQUIRED_COLUMNS).issubset(frame.columns)
    assert frame.iloc[0]["original_net_pnl"] < 0
    assert frame.iloc[0]["inverse_net_pnl"] > 0


def test_summarize_inverse_marks_inverse_winner():
    frame = build_inverse_strategy(pd.DataFrame([{"symbol": "KRMN", "side": "sell", "filled_qty": 36, "filled_avg_price": 52.0, "current_price": 53.95}]))
    summary = summarize_inverse(frame)
    assert summary["inverse_beats_original"] is True
