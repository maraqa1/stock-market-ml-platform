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
