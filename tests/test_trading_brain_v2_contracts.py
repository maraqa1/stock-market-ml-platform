import pytest

from stockml.trading_brain_v2.shared.models import (
    AuditEvent,
    Candidate,
    EntryAction,
    EntryDecision,
    ExecutionFill,
    ExitAction,
    ExitDecision,
    PortfolioSnapshot,
    PositionState,
    RiskPolicy,
    TradeIntent,
)


def candidate_payload():
    return {
        "symbol": "ATRC",
        "side": "LONG",
        "rank": 1,
        "candidate_status": "executable",
        "ai2_status": "Proceed candidate",
        "decision_label": "Proceed candidate",
        "approved_notional": 250.0,
        "qty": 6,
        "risk_class": "medium",
        "latest_eod_date": "2026-08-06",
        "close_price": 39.49,
        "expected_return_bps": 31.8,
        "one_day_return": 0.0038,
        "five_day_return": -0.0164,
        "twenty_day_volatility": 0.032,
        "eod_volume": 753722,
        "price_check_clear": True,
        "warning_codes": ["price_checks_clear"],
        "signal_id": "sig-1",
        "candidate_id": "cand-1",
        "event_id": "evt-1",
        "source_file": "execution_ranked_candidates.csv",
    }


def test_candidate_serializes_and_deserializes():
    candidate = Candidate.from_dict(candidate_payload())
    restored = Candidate.from_dict(candidate.to_dict())

    assert restored == candidate
    assert restored.warning_codes == ("price_checks_clear",)


def test_candidate_missing_required_field_is_rejected():
    payload = candidate_payload()
    payload["symbol"] = ""

    with pytest.raises(ValueError, match="missing_required_fields:symbol"):
        Candidate.from_dict(payload)


def test_entry_decision_allowed_actions_round_trip():
    decision = EntryDecision(
        symbol="ATRC",
        action="ENTER_REDUCED",
        reason="high_volatility",
        candidate_id="cand-1",
        signal_id="sig-1",
        event_id="evt-1",
        supporting_reasons="warning_interpreter|risk_sizing",
    )

    assert decision.action is EntryAction.ENTER_REDUCED
    assert EntryDecision.from_dict(decision.to_dict()) == decision


def test_entry_decision_rejects_invalid_action():
    with pytest.raises(ValueError):
        EntryDecision(
            symbol="ATRC",
            action="REVIEW",
            reason="not_allowed",
            candidate_id="cand-1",
            signal_id="sig-1",
            event_id="evt-1",
        )


def test_trade_intent_contains_required_entry_context():
    intent = TradeIntent(
        symbol="ATRC",
        side="LONG",
        decision="ENTER",
        qty=6,
        max_notional=250.0,
        signal_close=39.49,
        live_price_at_decision=39.52,
        stop_policy="standard_stop",
        take_profit_policy="standard_take_profit",
        max_holding_period="5d",
        risk_tier="medium",
        warnings=[],
        signal_id="sig-1",
        candidate_id="cand-1",
        event_id="evt-1",
        source_file="ai2.shortlist.csv",
    )

    restored = TradeIntent.from_dict(intent.to_dict())

    assert restored.decision is EntryAction.ENTER
    assert restored.signal_close == 39.49


def test_position_state_inherits_entry_lineage_and_context():
    position = PositionState(
        symbol="ATRC",
        side="LONG",
        qty=6,
        entry_price=39.49,
        current_price=40.25,
        unrealized_pl=4.56,
        unrealized_pl_pct=0.019,
        signal_id="sig-1",
        candidate_id="cand-1",
        event_id="evt-1",
        ai2_status_at_entry="Proceed candidate",
        warnings_at_entry=["price_checks_clear"],
        risk_tier="medium",
        entry_decision="ENTER",
        entry_reason="clean_price_checks",
        source_file="ai2.shortlist.csv",
    )

    assert position.signal_id == "sig-1"
    assert position.ai2_status_at_entry == "Proceed candidate"
    assert position.warnings_at_entry == ("price_checks_clear",)
    assert PositionState.from_dict(position.to_dict()) == position


def test_exit_decision_allowed_actions_round_trip():
    decision = ExitDecision(
        symbol="ATRC",
        action="TAKE_PROFIT",
        reason="target_hit",
        qty=3,
        signal_id="sig-1",
        candidate_id="cand-1",
        event_id="evt-1",
    )

    assert decision.action is ExitAction.TAKE_PROFIT
    assert ExitDecision.from_dict(decision.to_dict()) == decision


def test_supporting_models_round_trip():
    fill = ExecutionFill(
        symbol="ATRC",
        side="buy",
        qty=6,
        fill_price=39.5,
        filled_at="2026-08-06T14:45:00+00:00",
        broker_order_id="broker-1",
        client_order_id="client-1",
        signal_id="sig-1",
        candidate_id="cand-1",
        event_id="evt-1",
    )
    policy = RiskPolicy(
        policy_id="segment-1",
        max_notional_per_position=500,
        max_gross_exposure=4000,
        max_positions=10,
        allow_short_selling=False,
    )
    portfolio = PortfolioSnapshot(
        snapshot_at="2026-08-06T14:45:00+00:00",
        equity=98531,
        gross_exposure=4000,
        net_exposure=4000,
        open_positions=10,
        unrealized_pl=62.74,
    )
    audit = AuditEvent(
        event_at="2026-08-06T14:45:00+00:00",
        event_type="entry_decision",
        source="trading_brain_v2",
        symbol="ATRC",
        message="shadow intent created",
        details={"decision": "ENTER"},
    )

    assert ExecutionFill.from_dict(fill.to_dict()) == fill
    assert RiskPolicy.from_dict(policy.to_dict()) == policy
    assert PortfolioSnapshot.from_dict(portfolio.to_dict()) == portfolio
    assert AuditEvent.from_dict(audit.to_dict()) == audit

