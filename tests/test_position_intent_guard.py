from datetime import datetime, timedelta, timezone

from stockml.trading.order_intent import derive_order_intent
from stockml.trading.position_intent_guard import PositionIntentConfig, evaluate_position_intent, guard_order_submission

NOW = datetime(2026, 6, 18, 13, 0, tzinfo=timezone.utc)


def pos(symbol="AAA", qty=10, minutes=10, avg=100):
    return {"symbol": symbol, "qty": qty, "avg_entry_price": avg, "opened_at": (NOW - timedelta(minutes=minutes)).isoformat()}


def test_no_position_buy_open_long_allowed():
    d = evaluate_position_intent(symbol="AAA", attempted_side="buy", attempted_qty=5, position=None, now=NOW)
    assert d.allowed
    assert d.intent.intent == "open_long"


def test_no_position_sell_open_short_allowed_if_shorting_enabled():
    d = evaluate_position_intent(symbol="AAA", attempted_side="sell", attempted_qty=5, position=None, config=PositionIntentConfig(allow_short_selling=True), now=NOW)
    assert d.allowed
    assert d.intent.intent == "open_short"


def test_long_position_sell_within_minimum_hold_is_blocked():
    d = evaluate_position_intent(symbol="AGL", attempted_side="sell", attempted_qty=17, position=pos("AGL", 17, minutes=1, avg=105), now=NOW)
    assert not d.allowed
    assert d.intent.intent == "close_long"
    assert d.block_reason == "minimum_hold_period_not_met"


def test_short_position_buy_within_minimum_hold_is_blocked():
    d = evaluate_position_intent(symbol="KRMN", attempted_side="buy", attempted_qty=36, position=pos("KRMN", -36, minutes=24, avg=52), now=NOW)
    assert not d.allowed
    assert d.intent.intent == "cover_short"
    assert d.block_reason == "minimum_hold_period_not_met"


def test_agl_loss_close_scenario_is_blocked():
    d = evaluate_position_intent(symbol="AGL", attempted_side="sell", attempted_qty=17, position=pos("AGL", 17, minutes=1, avg=105), now=NOW, session_mode="24x5")
    assert not d.allowed
    assert d.block_reason == "minimum_hold_period_not_met"


def test_krmn_cover_loss_scenario_is_blocked():
    d = evaluate_position_intent(symbol="KRMN", attempted_side="buy", attempted_qty=36, position=pos("KRMN", -36, minutes=24, avg=52), now=NOW, session_mode="24x5")
    assert not d.allowed
    assert d.block_reason == "minimum_hold_period_not_met"


def test_long_position_sell_after_minimum_hold_allowed_if_close_reason_valid():
    d = evaluate_position_intent(symbol="AAA", attempted_side="sell", attempted_qty=10, position=pos("AAA", 10, minutes=45), close_reason="take_profit_hit", now=NOW)
    assert d.allowed
    assert d.intent.intent == "close_long"


def test_short_position_buy_after_minimum_hold_allowed_if_close_reason_valid():
    d = evaluate_position_intent(symbol="AAA", attempted_side="buy", attempted_qty=10, position=pos("AAA", -10, minutes=45), close_reason="take_profit_hit", now=NOW)
    assert d.allowed
    assert d.intent.intent == "cover_short"


def test_long_position_sell_greater_than_qty_is_blocked_as_reversal():
    d = evaluate_position_intent(symbol="AAA", attempted_side="sell", attempted_qty=12, position=pos("AAA", 10, minutes=45), now=NOW)
    assert not d.allowed
    assert d.intent.intent == "close_long_then_reverse_short"
    assert d.block_reason == "same_day_reversal_blocked"


def test_short_position_buy_greater_than_abs_qty_is_blocked_as_reversal():
    d = evaluate_position_intent(symbol="AAA", attempted_side="buy", attempted_qty=12, position=pos("AAA", -10, minutes=45), now=NOW)
    assert not d.allowed
    assert d.intent.intent == "cover_short_then_reverse_long"
    assert d.block_reason == "same_day_reversal_blocked"


def test_position_state_unavailable_blocks_opposite_side_order():
    d = evaluate_position_intent(symbol="AAA", attempted_side="sell", attempted_qty=10, position=None, position_state_available=False, now=NOW)
    assert not d.allowed
    assert d.block_reason == "position_state_unavailable_for_opposite_side_order"


def test_order_intent_matrix_examples():
    assert derive_order_intent(current_qty=0, attempted_side="buy", attempted_qty=1).intent == "open_long"
    assert derive_order_intent(current_qty=0, attempted_side="sell", attempted_qty=1).intent == "open_short"
    assert derive_order_intent(current_qty=10, attempted_side="buy", attempted_qty=1).intent == "increase_long"
    assert derive_order_intent(current_qty=-10, attempted_side="sell", attempted_qty=1).intent == "increase_short"
