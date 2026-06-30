import pandas as pd

from stockml.diagnostics.side_mapping_audit import build_side_mapping_audit


def test_side_mapping_audit_flags_long_mapped_to_sell():
    out = build_side_mapping_audit(pd.DataFrame([{"symbol": "AAA", "trade_action": "Long", "side": "sell"}]))
    assert "long_mapped_to_sell" in set(out["audit_flag"])


def test_side_mapping_audit_flags_short_mapped_to_buy():
    out = build_side_mapping_audit(pd.DataFrame([{"symbol": "AAA", "trade_action": "Short", "side": "buy"}]))
    assert "short_mapped_to_buy" in set(out["audit_flag"])


def test_side_mapping_audit_flags_no_decision_order():
    out = build_side_mapping_audit(pd.DataFrame([{"symbol": "AAA", "trade_action": "No Decision", "side": "buy"}]))
    assert "no_decision_mapped_to_order" in set(out["audit_flag"])


def test_side_mapping_audit_ok_for_long_buy():
    out = build_side_mapping_audit(pd.DataFrame([{"symbol": "AAA", "trade_action": "Long", "side": "buy"}]))
    assert set(out["audit_flag"]) == {"ok"}


from pathlib import Path

from stockml.diagnostics.side_mapping_audit import build_side_mapping_audit_report


def test_side_mapping_audit_report_writes_missing_data(tmp_path: Path):
    output = build_side_mapping_audit_report("20260630_120000", order_file=tmp_path / "missing.csv")
    assert output.status == "missing_data"
    assert output.path.exists()
    assert output.missing_inputs == ("order_plan_or_results",)
