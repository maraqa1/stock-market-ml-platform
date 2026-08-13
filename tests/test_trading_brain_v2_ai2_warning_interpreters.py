from stockml.trading_brain_v2.autopilot.ap_b04_ai2_status_interpreter import AI2StatusInterpreterBlock
from stockml.trading_brain_v2.autopilot.ap_b05_warning_interpreter import WarningInterpreterBlock
from stockml.trading_brain_v2.shared.models import EntryAction


def test_ai2_status_interpreter_maps_proceed_to_normal_gate_eligible():
    decision = AI2StatusInterpreterBlock().interpret_status("Proceed candidate", symbol="ATRC")

    assert decision.action is EntryAction.ENTER
    assert decision.eligible_for_normal_gates is True
    assert decision.reason == "ai2_proceed_continue_to_gates"


def test_ai2_status_interpreter_maps_review_without_manual_review():
    decision = AI2StatusInterpreterBlock().interpret_status("Review before execution", symbol="ATAI")

    assert decision.action is EntryAction.ENTER_REDUCED
    assert "manual" not in decision.action.value.lower()
    assert decision.eligible_for_normal_gates is False


def test_ai2_status_interpreter_maps_refresh_and_unknown():
    block = AI2StatusInterpreterBlock()

    assert block.interpret_status("Do not execute until refreshed").action is EntryAction.REFRESH_AND_RECHECK
    assert block.interpret_status("unmapped").action is EntryAction.BLOCK


def test_warning_parser_normalizes_supported_codes():
    block = WarningInterpreterBlock()

    codes = block.parse_warning_codes(
        "warning: high volatility; warning: large intraday move; ok: price_checks_clear",
        "5-day move suggests extended momentum setup",
    )

    assert codes == ("high_volatility", "large_intraday_move", "price_checks_clear", "extended_5d_momentum")


def test_warning_interpreter_refreshes_large_moves():
    block = WarningInterpreterBlock()

    assert block.interpret_codes(("large_intraday_move",)).action is EntryAction.REFRESH_AND_RECHECK
    assert block.interpret_codes(("large_1d_move",)).action is EntryAction.REFRESH_AND_RECHECK


def test_warning_interpreter_reduces_high_volatility_and_extended_momentum():
    block = WarningInterpreterBlock()

    assert block.interpret_codes(("high_volatility",)).action is EntryAction.ENTER_REDUCED
    assert block.interpret_codes(("extended_5d_momentum",)).action is EntryAction.ENTER_REDUCED


def test_warning_interpreter_blocks_price_failure_and_unknown():
    block = WarningInterpreterBlock()

    assert block.interpret_codes(("price_check_failed",)).action is EntryAction.BLOCK
    assert block.interpret_codes(("unknown_warning",)).action is EntryAction.BLOCK


def test_price_checks_clear_continues_but_is_not_final_entry_guarantee():
    decision = WarningInterpreterBlock().interpret_codes(("price_checks_clear",))

    assert decision.action is EntryAction.ENTER
    assert decision.reason == "price_checks_clear_continue"

