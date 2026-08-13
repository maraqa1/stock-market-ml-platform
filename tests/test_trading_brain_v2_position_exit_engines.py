from stockml.trading_brain_v2.position_management.pm_b04_stop_loss_engine import StopLossEngineBlock
from stockml.trading_brain_v2.position_management.pm_b05_profit_taking_engine import ProfitTakingEngineBlock
from stockml.trading_brain_v2.position_management.pm_b06_trailing_stop_engine import TrailingStopEngineBlock
from stockml.trading_brain_v2.position_management.pm_b07_time_based_exit_engine import TimeBasedExitEngineBlock
from stockml.trading_brain_v2.shared.models import EntryAction, ExitAction, PositionState


def _position(**overrides):
    values = {
        "symbol": "ATRC",
        "side": "LONG",
        "qty": 10,
        "entry_price": 100.0,
        "current_price": 100.0,
        "unrealized_pl": 0.0,
        "unrealized_pl_pct": 0.0,
        "signal_id": "sig-1",
        "candidate_id": "cand-1",
        "event_id": "evt-1",
        "ai2_status_at_entry": "proceed",
        "warnings_at_entry": ("price_checks_clear",),
        "risk_tier": "normal",
        "entry_decision": EntryAction.ENTER,
        "entry_reason": "entry_approved",
        "source_file": "candidate.csv",
        "entry_time": "2026-08-06T14:45:00+00:00",
        "signal_close": 100.0,
        "stop_price": 96.5,
        "trailing_stop": 96.5,
        "take_profit_stage": "initial",
        "max_price_seen": 100.0,
        "min_price_seen": 100.0,
        "max_holding_period": "5d",
        "order_id": "paper-order-1",
        "status": "open",
        "current_value": 1000.0,
        "max_favorable_excursion": 0.0,
        "max_adverse_excursion": 0.0,
    }
    values.update(overrides)
    return PositionState(**values)


def test_stop_loss_triggers_exit():
    decision = StopLossEngineBlock().evaluate_position(_position(current_price=96.0, unrealized_pl=-40, unrealized_pl_pct=-0.04))

    assert decision.action is ExitAction.EXIT
    assert decision.reason == "stop_loss_hit"


def test_profit_ladder_one_percent_moves_stop():
    decision = ProfitTakingEngineBlock().evaluate_position(_position(current_price=101.0, unrealized_pl=10, unrealized_pl_pct=0.01))

    assert decision.action is ExitAction.MOVE_STOP
    assert decision.reason == "profit_ladder_1pct_move_stop_to_breakeven"


def test_profit_ladder_two_percent_triggers_first_profit_action():
    decision = ProfitTakingEngineBlock().evaluate_position(_position(current_price=102.0, unrealized_pl=20, unrealized_pl_pct=0.02))

    assert decision.action is ExitAction.TAKE_PROFIT
    assert decision.reason == "profit_ladder_2pct_first_partial"
    assert decision.qty == 2.5


def test_profit_ladder_four_percent_triggers_second_profit_action():
    decision = ProfitTakingEngineBlock().evaluate_position(
        _position(current_price=104.0, unrealized_pl=40, unrealized_pl_pct=0.04, take_profit_stage="first_profit_taken")
    )

    assert decision.action is ExitAction.TAKE_PROFIT
    assert decision.reason == "profit_ladder_4pct_second_partial"


def test_profit_ladder_six_percent_triggers_trailing_logic():
    decision = ProfitTakingEngineBlock().evaluate_position(_position(current_price=106.0, unrealized_pl=60, unrealized_pl_pct=0.06))

    assert decision.action is ExitAction.TRAIL
    assert decision.reason == "profit_ladder_6pct_trail_remaining"


def test_trailing_stop_updates_when_new_high_is_reached():
    result = TrailingStopEngineBlock().evaluate_position(_position(current_price=110.0, max_price_seen=100.0, trailing_stop=96.5))

    assert result.decision.action is ExitAction.TRAIL
    assert result.position.max_price_seen == 110.0
    assert result.position.trailing_stop == 106.7


def test_negative_after_failed_signal_period_exits():
    decision = TimeBasedExitEngineBlock().evaluate_position(
        _position(unrealized_pl=-10, unrealized_pl_pct=-0.01),
        current_time="2026-08-06T16:00:00+00:00",
        failed_signal_minutes=60,
    )

    assert decision.action is ExitAction.EXIT
    assert decision.reason == "negative_after_failed_signal_period"


def test_max_holding_period_exits():
    decision = TimeBasedExitEngineBlock().evaluate_position(
        _position(max_holding_period="1d"),
        current_time="2026-08-08T14:45:00+00:00",
    )

    assert decision.action is ExitAction.EXIT
    assert decision.reason == "max_holding_period_exceeded"
