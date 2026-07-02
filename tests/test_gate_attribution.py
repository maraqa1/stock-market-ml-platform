from stockml.diagnostics.gate_attribution import _recommendation
from stockml.strategy.gate_registry import get_gate


def test_gate_attribution_reports_insufficient_data_without_forward_marks():
    gate = get_gate("expected_trade_return_below_threshold")
    assert _recommendation(gate, False, None, None) == "insufficient_data"


def test_must_have_gate_is_never_remove_or_tune_without_override():
    gate = get_gate("risk_gate_failed")
    assert _recommendation(gate, True, 100.0, 0.0) == "mandatory_do_not_tune"
