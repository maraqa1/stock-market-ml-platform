from stockml.trading_brain_v2.position_management.pm_b01_position_creation import PositionCreationBlock
from stockml.trading_brain_v2.position_management.pm_b02_initial_risk_attachment import InitialRiskAttachmentBlock
from stockml.trading_brain_v2.position_management.pm_b03_live_mark_to_market import LiveMarkToMarketBlock
from stockml.trading_brain_v2.shared.models import EntryAction, ExecutionFill, TradeIntent


def _intent(**overrides):
    values = {
        "symbol": "ATRC",
        "side": "LONG",
        "decision": EntryAction.ENTER,
        "qty": 7,
        "max_notional": 700.0,
        "signal_close": 100.0,
        "live_price_at_decision": 100.0,
        "stop_policy": "wider_standard_stop",
        "take_profit_policy": "standard_profit_take",
        "max_holding_period": "5d",
        "risk_tier": "normal",
        "warnings": ("price_checks_clear",),
        "warning_codes": ("price_checks_clear",),
        "ai2_status": "proceed",
        "signal_id": "sig-1",
        "candidate_id": "cand-1",
        "event_id": "evt-1",
        "source_file": "candidate.csv",
    }
    values.update(overrides)
    return TradeIntent(**values)


def _fill(**overrides):
    values = {
        "symbol": "ATRC",
        "side": "LONG",
        "qty": 7,
        "fill_price": 100.0,
        "filled_at": "2026-08-06T14:45:00+00:00",
        "broker_order_id": "paper-order-1",
        "client_order_id": "client-order-1",
        "signal_id": "sig-1",
        "candidate_id": "cand-1",
        "event_id": "evt-1",
    }
    values.update(overrides)
    return ExecutionFill(**values)


def test_position_state_inherits_signal_candidate_and_event_ids():
    position = PositionCreationBlock().create_position(_intent(), _fill())

    assert position.signal_id == "sig-1"
    assert position.candidate_id == "cand-1"
    assert position.event_id == "evt-1"
    assert position.order_id == "paper-order-1"


def test_position_state_inherits_warnings_and_ai2_status():
    position = PositionCreationBlock().create_position(_intent(warnings=("high_volatility",), warning_codes=("high_volatility",), ai2_status="review"), _fill())

    assert position.ai2_status_at_entry == "review"
    assert position.warnings_at_entry == ("high_volatility",)
    assert position.risk_tier == "normal"


def test_initial_stop_is_calculated_for_clean_proceed():
    position = PositionCreationBlock().create_position(_intent(), _fill())

    assert position.stop_price == 96.0
    assert position.trailing_stop == 96.0
    assert position.max_holding_period == "5d"


def test_reduced_review_has_tighter_initial_stop():
    base = PositionCreationBlock().create_position(
        _intent(decision=EntryAction.ENTER_REDUCED, ai2_status="review", max_holding_period="2d"),
        _fill(),
        attach_risk=False,
    )
    position = InitialRiskAttachmentBlock().attach_initial_risk(
        base,
        _intent(decision=EntryAction.ENTER_REDUCED, ai2_status="review", max_holding_period="2d"),
    )

    assert position.stop_price == 97.0
    assert position.position_risk_budget == 0.006


def test_mark_to_market_positive_profit_and_loss_works():
    position = PositionCreationBlock().create_position(_intent(), _fill())
    marked = LiveMarkToMarketBlock().mark_to_market(position, current_price=110.0)

    assert marked.current_value == 770.0
    assert marked.unrealized_pl == 70.0
    assert marked.unrealized_pl_pct == 0.1


def test_mark_to_market_negative_profit_and_loss_works():
    position = PositionCreationBlock().create_position(_intent(), _fill())
    marked = LiveMarkToMarketBlock().mark_to_market(position, current_price=95.0)

    assert marked.current_value == 665.0
    assert marked.unrealized_pl == -35.0
    assert marked.unrealized_pl_pct == -0.05


def test_max_favorable_and_adverse_excursion_updates():
    position = PositionCreationBlock().create_position(_intent(), _fill())
    higher = LiveMarkToMarketBlock().mark_to_market(position, current_price=110.0)
    lower = LiveMarkToMarketBlock().mark_to_market(higher, current_price=92.0)

    assert higher.max_favorable_excursion == 0.1
    assert higher.max_adverse_excursion == 0.0
    assert lower.max_favorable_excursion == 0.1
    assert lower.max_adverse_excursion == 0.08
    assert lower.max_price_seen == 110.0
    assert lower.min_price_seen == 92.0
