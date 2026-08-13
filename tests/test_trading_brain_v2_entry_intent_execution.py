import pytest

from stockml.trading_brain_v2.autopilot.ap_b10_entry_decision_engine import EntryDecisionEngineBlock
from stockml.trading_brain_v2.autopilot.ap_b11_trade_intent_builder import TradeIntentBuilderBlock
from stockml.trading_brain_v2.autopilot.ap_b12_execution_handoff import ExecutionHandoffBlock
from stockml.trading_brain_v2.shared.config import TradingBrainConfig
from stockml.trading_brain_v2.shared.models import Candidate, EntryAction, EntryDecision
from stockml.trading_brain_v2.shared.safety import TradingBrainV2LiveExecutionBlocked


def _candidate(**overrides):
    values = {
        "symbol": "ATRC",
        "side": "LONG",
        "rank": 1,
        "candidate_status": "executable",
        "ai2_status": "proceed",
        "decision_label": "Proceed candidate",
        "approved_notional": 1000.0,
        "qty": 0.0,
        "risk_class": "medium",
        "latest_eod_date": "2026-08-06",
        "close_price": 100.0,
        "expected_return_bps": 31.8,
        "one_day_return": 0.01,
        "five_day_return": 0.05,
        "twenty_day_volatility": 0.02,
        "eod_volume": 750000.0,
        "price_check_clear": True,
        "warning_codes": ("price_checks_clear",),
        "signal_id": "sig-1",
        "candidate_id": "cand-1",
        "event_id": "evt-1",
        "source_file": "candidate.csv",
    }
    values.update(overrides)
    return Candidate(**values)


def _decision(action=EntryAction.ENTER, **overrides):
    values = {
        "symbol": "ATRC",
        "action": action,
        "reason": "entry_approved",
        "candidate_id": "cand-1",
        "signal_id": "sig-1",
        "event_id": "evt-1",
        "qty": 7,
        "notional": 750.0,
        "risk_profile": {"risk_tier": "normal"},
        "warnings": ("price_checks_clear",),
        "source_file": "candidate.csv",
    }
    values.update(overrides)
    return EntryDecision(**values)


def test_entry_decision_clean_proceed_enters():
    decision = EntryDecisionEngineBlock().decide(_candidate(), live_price=100.0)

    assert decision.action is EntryAction.ENTER
    assert decision.qty == 7
    assert decision.notional == 750.0
    assert "manual" not in decision.action.value.lower()


def test_entry_decision_review_but_acceptable_enters_reduced():
    decision = EntryDecisionEngineBlock().decide(_candidate(ai2_status="review"), live_price=100.0)

    assert decision.action is EntryAction.ENTER_REDUCED
    assert decision.qty == 2


def test_entry_decision_refresh_required_refreshes():
    decision = EntryDecisionEngineBlock().decide(_candidate(ai2_status="refresh_required"), live_price=100.0)

    assert decision.action is EntryAction.REFRESH_AND_RECHECK


def test_entry_decision_high_volatility_over_threshold_blocks():
    decision = EntryDecisionEngineBlock().decide(_candidate(twenty_day_volatility=0.091), live_price=100.0)

    assert decision.action is EntryAction.BLOCK
    assert decision.reason == "risk_multiplier_zero"


def test_entry_decision_large_intraday_move_refreshes():
    decision = EntryDecisionEngineBlock().decide(_candidate(warning_codes=("large_intraday_move",)), live_price=100.0)

    assert decision.action is EntryAction.REFRESH_AND_RECHECK
    assert decision.reason == "large_intraday_move"


def test_entry_decision_live_price_gap_above_five_percent_blocks():
    decision = EntryDecisionEngineBlock().decide(_candidate(), live_price=106.0)

    assert decision.action is EntryAction.BLOCK
    assert decision.reason == "live_price_gap_block"


def test_entry_decision_price_check_failed_blocks():
    decision = EntryDecisionEngineBlock().decide(_candidate(warning_codes=("price_check_failed",)), live_price=100.0)

    assert decision.action is EntryAction.BLOCK
    assert decision.reason == "price_check_failed"


def test_entry_decision_zero_quantity_blocks():
    decision = EntryDecisionEngineBlock().decide(_candidate(approved_notional=10.0), live_price=100.0)

    assert decision.action is EntryAction.BLOCK
    assert decision.reason == "sized_quantity_zero"


def test_enter_creates_trade_intent():
    result = TradeIntentBuilderBlock().build_trade_intent(_decision(), _candidate(), live_price=100.0)

    assert result.built is True
    assert result.trade_intent is not None
    assert result.trade_intent.symbol == "ATRC"
    assert result.trade_intent.decision is EntryAction.ENTER


def test_enter_reduced_creates_trade_intent():
    result = TradeIntentBuilderBlock().build_trade_intent(
        _decision(action=EntryAction.ENTER_REDUCED, reason="entry_reduced", notional=262.5, qty=2),
        _candidate(ai2_status="review"),
        live_price=100.0,
    )

    assert result.built is True
    assert result.trade_intent is not None
    assert result.trade_intent.decision is EntryAction.ENTER_REDUCED
    assert result.trade_intent.stop_policy == "tight_reduced_stop"


def test_block_does_not_create_trade_intent():
    result = TradeIntentBuilderBlock().build_trade_intent(_decision(action=EntryAction.BLOCK, qty=0, notional=0), _candidate(), live_price=100.0)

    assert result.built is False
    assert result.trade_intent is None


def test_refresh_does_not_create_trade_intent():
    result = TradeIntentBuilderBlock().build_trade_intent(_decision(action=EntryAction.REFRESH_AND_RECHECK, qty=0, notional=0), _candidate(), live_price=100.0)

    assert result.built is False
    assert result.trade_intent is None


def test_live_execution_blocked_when_v2_live_execution_false():
    intent = TradeIntentBuilderBlock().build_trade_intent(_decision(), _candidate(), live_price=100.0).trade_intent

    with pytest.raises(TradingBrainV2LiveExecutionBlocked):
        ExecutionHandoffBlock().execute_intent(intent, mode="live", config=TradingBrainConfig(v2_allow_live_execution=False))


def test_simulated_fill_can_be_generated_in_shadow_mode():
    intent = TradeIntentBuilderBlock().build_trade_intent(_decision(), _candidate(), live_price=100.0).trade_intent
    result = ExecutionHandoffBlock().execute_intent(intent, mode="shadow", config=TradingBrainConfig())

    assert result.submitted is True
    assert result.fill is not None
    assert result.fill.fill_price == 100.0
    assert result.audit_event.event_type == "trading_brain_v2_execution_handoff"
