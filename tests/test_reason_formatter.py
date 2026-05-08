from stockml.decisions.no_decision_rules import no_decision_reasons
from stockml.decisions.reason_formatter import format_reasons


def test_reason_formatter_makes_trade_reasons_readable():
    assert format_reasons("market_cap_below_minimum|quantity_below_one") == "Market cap below minimum; Position size too small to buy one share"


def test_no_decision_reason_taxonomy():
    reasons = no_decision_reasons({"trade_action": "No Decision", "no_decision_reason": "weak_probability"})
    assert "not_long_or_short" in reasons
    assert "no_decision_reason_present" in reasons
