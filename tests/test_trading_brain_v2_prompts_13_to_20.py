import pytest

from stockml.trading_brain_v2.audit.logger import AuditLogger, build_audit_event
from stockml.trading_brain_v2.autopilot.ap_b10_entry_decision_engine import EntryDecisionEngineBlock
from stockml.trading_brain_v2.autopilot.ap_b11_trade_intent_builder import TradeIntentBuilderBlock
from stockml.trading_brain_v2.paper_simulation import TradingBrainV2PaperSimulator
from stockml.trading_brain_v2.position_management.pm_b04_stop_loss_engine import StopLossEngineBlock
from stockml.trading_brain_v2.position_management.pm_b08_portfolio_risk_overlay import PORTFOLIO_FORCE_EXIT_INVALID, PortfolioOverlayDecision
from stockml.trading_brain_v2.position_management.pm_b10_exit_decision_engine import ExitDecisionEngineBlock
from stockml.trading_brain_v2.position_management.pm_b11_performance_attribution import PerformanceAttributionBlock
from stockml.trading_brain_v2.position_management.pm_b12_feedback_store import FeedbackStoreBlock
from stockml.trading_brain_v2.readiness import build_cutover_readiness_report
from stockml.trading_brain_v2.shadow import TradingBrainV2ShadowRunner
from stockml.trading_brain_v2.shared.config import TradingBrainConfig, assert_startup_safety, validate_trading_brain_policy
from stockml.trading_brain_v2.shared.models import Candidate, EntryAction, ExitAction, PortfolioSnapshot, PositionState


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


def _position(**overrides):
    values = {
        "symbol": "ATRC",
        "side": "LONG",
        "qty": 10,
        "entry_price": 100.0,
        "current_price": 96.0,
        "unrealized_pl": -40.0,
        "unrealized_pl_pct": -0.04,
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
        "min_price_seen": 96.0,
        "max_holding_period": "5d",
        "order_id": "paper-order-1",
        "status": "open",
        "current_value": 960.0,
        "max_favorable_excursion": 0.0,
        "max_adverse_excursion": 0.04,
    }
    values.update(overrides)
    return PositionState(**values)


def test_exit_decision_prioritizes_stop_loss_over_hold():
    position = _position()
    decision = ExitDecisionEngineBlock().decide(position, stop_decision=StopLossEngineBlock().evaluate_position(position))

    assert decision.action is ExitAction.EXIT
    assert decision.reason == "stop_loss_hit"
    assert decision.current_price == 96.0


def test_portfolio_force_exit_overrides_position_hold():
    decision = ExitDecisionEngineBlock().decide(
        _position(current_price=101, unrealized_pl=10, unrealized_pl_pct=0.01),
        portfolio_decision=PortfolioOverlayDecision(PORTFOLIO_FORCE_EXIT_INVALID, "refresh_required_exposure_present"),
    )

    assert decision.action is ExitAction.EXIT
    assert decision.reason == "refresh_required_exposure_present"


def test_attribution_groups_by_ai2_status_and_warning_code():
    rows = PerformanceAttributionBlock().attribute([
        _position(ai2_status_at_entry="proceed", warnings_at_entry=("price_checks_clear",), unrealized_pl=10, unrealized_pl_pct=0.01),
        _position(symbol="ATAI", ai2_status_at_entry="review", warnings_at_entry=("high_volatility",), unrealized_pl=-5, unrealized_pl_pct=-0.005),
    ])
    lookup = {(row.group, row.key): row for row in rows}

    assert lookup[("ai2_status", "proceed")].count == 1
    assert lookup[("warning_code", "high_volatility")].total_pnl == -5


def test_feedback_record_persists_required_identifiers(tmp_path):
    store = FeedbackStoreBlock()
    position = _position(current_price=105, unrealized_pl=50, unrealized_pl_pct=0.05)
    decision = ExitDecisionEngineBlock().decide(position)
    path = tmp_path / "feedback.jsonl"
    record = store.build_record(position, decision, exit_price=105)
    store.append_record(path, record)

    records = store.read_records(path)
    assert records[0]["signal_id"] == "sig-1"
    assert records[0]["candidate_id"] == "cand-1"
    assert records[0]["event_id"] == "evt-1"


def test_shadow_mode_does_not_call_live_execution_and_logs_decisions():
    result = TradingBrainV2ShadowRunner().run([_candidate()], live_prices={"ATRC": 100.0}, old_brain_decisions={}, run_id="run-1")

    assert result.entry_decisions[0].action is EntryAction.ENTER
    assert result.trade_intents
    assert result.audit_events[0].details["run_id"] == "run-1"


def test_paper_simulation_creates_position_and_metrics():
    decision = EntryDecisionEngineBlock().decide(_candidate(), live_price=100)
    intent = TradeIntentBuilderBlock().build_trade_intent(decision, _candidate(), live_price=100).trade_intent
    simulator = TradingBrainV2PaperSimulator()
    positions = simulator.open_positions([intent])
    result = simulator.apply_price_updates(positions, {"ATRC": 105.0})

    assert positions[0].signal_id == "sig-1"
    assert result.metrics.total_pnl > 0
    assert result.metrics.winners == 1


def test_policy_validation_and_startup_safety():
    validate_trading_brain_policy(TradingBrainConfig())
    with pytest.raises(ValueError):
        validate_trading_brain_policy(TradingBrainConfig(max_live_gap_refresh_pct=0.06, max_live_gap_block_pct=0.05))
    assert_startup_safety(TradingBrainConfig(active_version="v2", v2_shadow_mode=False, v2_allow_live_execution=False, v2_paper_execution=True))
    with pytest.raises(RuntimeError):
        assert_startup_safety(TradingBrainConfig(active_version="v2", v2_paper_execution=True), audit_available=False)


def test_audit_log_can_be_queried_by_symbol_and_run_id(tmp_path):
    logger = AuditLogger(tmp_path / "audit.jsonl")
    event = build_audit_event(event_type="entry_decision", run_id="run-1", source_file="candidate.csv", symbol="ATRC", message="entry_approved", candidate=_candidate(), entry_decision=EntryDecisionEngineBlock().decide(_candidate(), live_price=100))
    logger.append(event)

    assert len(logger.read(symbol="ATRC")) == 1
    assert len(logger.read(run_id="run-1")) == 1


def test_ai2_style_fixture_integration_results():
    candidates = [
        _candidate(symbol="CLEAN"),
        _candidate(symbol="REVIEW", ai2_status="review", warning_codes=("high_volatility",)),
        _candidate(symbol="MOMO", five_day_return=0.31),
        _candidate(symbol="REFRESH", ai2_status="refresh_required"),
        _candidate(symbol="PRICEFAIL", warning_codes=("price_check_failed",)),
        _candidate(symbol="STALE", latest_eod_date="2026-08-05"),
        _candidate(symbol="GAPBLOCK"),
        _candidate(symbol="GAPREFRESH"),
    ]
    live = {"CLEAN": 100, "REVIEW": 100, "MOMO": 100, "REFRESH": 100, "PRICEFAIL": 100, "STALE": 100, "GAPBLOCK": 106, "GAPREFRESH": 103}
    actions = {
        candidate.symbol: EntryDecisionEngineBlock().decide(candidate, live_price=live[candidate.symbol], expected_latest_eod_date="2026-08-06").action
        for candidate in candidates
    }

    assert actions["CLEAN"] is EntryAction.ENTER
    assert actions["REVIEW"] in {EntryAction.ENTER_REDUCED, EntryAction.BLOCK}
    assert actions["MOMO"] is EntryAction.BLOCK
    assert actions["REFRESH"] is EntryAction.REFRESH_AND_RECHECK
    assert actions["PRICEFAIL"] is EntryAction.BLOCK
    assert actions["STALE"] is EntryAction.REFRESH_AND_RECHECK
    assert actions["GAPBLOCK"] is EntryAction.BLOCK
    assert actions["GAPREFRESH"] is EntryAction.REFRESH_AND_RECHECK


def test_readiness_report_states_live_cutover_unsafe():
    report = build_cutover_readiness_report(TradingBrainConfig(active_version="v2", v2_shadow_mode=False, v2_paper_execution=True))

    assert "Live cutover is unsafe" in report
    assert "ap_b01_to_b12_implemented: PASS" in report
