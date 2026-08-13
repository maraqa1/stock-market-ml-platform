from stockml.trading_brain_v2.position_management.pm_b08_portfolio_risk_overlay import (
    PORTFOLIO_ALLOW,
    PORTFOLIO_BLOCK_NEW_ENTRIES,
    PORTFOLIO_BLOCK_OVERSIZED_POSITION,
    PORTFOLIO_FORCE_EXIT_INVALID,
    PORTFOLIO_REDUCE_RISK,
    PortfolioOverlayDecision,
    PortfolioRiskOverlayBlock,
)
from stockml.trading_brain_v2.position_management.pm_b09_reentry_addon_logic import ADDON_ALLOW, ADDON_BLOCK, ReEntryAddOnLogicBlock
from stockml.trading_brain_v2.shared.models import EntryAction, PortfolioSnapshot, PositionState


def _portfolio(**overrides):
    values = {
        "snapshot_at": "2026-08-06T15:00:00+00:00",
        "equity": 10000.0,
        "gross_exposure": 1000.0,
        "net_exposure": 1000.0,
        "open_positions": 1,
        "unrealized_pl": 0.0,
        "cash": 9000.0,
    }
    values.update(overrides)
    return PortfolioSnapshot(**values)


def _position(**overrides):
    values = {
        "symbol": "ATRC",
        "side": "LONG",
        "qty": 10,
        "entry_price": 100.0,
        "current_price": 105.0,
        "unrealized_pl": 50.0,
        "unrealized_pl_pct": 0.05,
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
        "stop_price": 100.0,
        "trailing_stop": 100.0,
        "take_profit_stage": "initial",
        "max_price_seen": 105.0,
        "min_price_seen": 100.0,
        "max_holding_period": "5d",
        "order_id": "paper-order-1",
        "status": "open",
        "current_value": 1050.0,
        "max_favorable_excursion": 0.05,
        "max_adverse_excursion": 0.0,
    }
    values.update(overrides)
    return PositionState(**values)


def test_portfolio_daily_loss_blocks_new_entries_and_reduces_risk():
    decision = PortfolioRiskOverlayBlock().evaluate_portfolio(
        _portfolio(unrealized_pl=-250.0),
        positions=[_position(symbol="WEAK", unrealized_pl_pct=-0.05)],
    )

    assert decision.action == PORTFOLIO_REDUCE_RISK
    assert decision.reason == "daily_portfolio_loss_limit_breached"
    assert decision.weakest_symbols == ("WEAK",)


def test_max_open_positions_blocks_new_entries():
    decision = PortfolioRiskOverlayBlock().evaluate_portfolio(_portfolio(open_positions=10), positions=[], max_open_positions=10)

    assert decision.action == PORTFOLIO_BLOCK_NEW_ENTRIES
    assert decision.reason == "max_open_positions_reached"


def test_oversized_name_blocks_add():
    decision = PortfolioRiskOverlayBlock().evaluate_portfolio(
        _portfolio(),
        positions=[],
        proposed_symbol="ATRC",
        proposed_notional=2000.0,
        max_single_name_exposure_pct=0.15,
    )

    assert decision.action == PORTFOLIO_BLOCK_OVERSIZED_POSITION
    assert decision.symbol == "ATRC"


def test_refresh_required_exposure_forces_invalid_handling():
    decision = PortfolioRiskOverlayBlock().evaluate_portfolio(
        _portfolio(),
        positions=[_position(symbol="STALE", ai2_status_at_entry="refresh_required")],
    )

    assert decision.action == PORTFOLIO_FORCE_EXIT_INVALID
    assert decision.weakest_symbols == ("STALE",)


def test_averaging_down_is_blocked():
    decision = ReEntryAddOnLogicBlock().evaluate_add_on(
        _position(unrealized_pl=-5.0, unrealized_pl_pct=-0.005),
        refreshed_signal_status="proceed",
        portfolio_decision=PortfolioOverlayDecision(PORTFOLIO_ALLOW, "ok"),
        requested_add_qty=5,
    )

    assert decision.action == ADDON_BLOCK
    assert decision.reason == "averaging_down_or_non_winner_blocked"


def test_adding_to_winner_requires_protected_stop():
    decision = ReEntryAddOnLogicBlock().evaluate_add_on(
        _position(stop_price=98.0),
        refreshed_signal_status="proceed",
        portfolio_decision=PortfolioOverlayDecision(PORTFOLIO_ALLOW, "ok"),
        requested_add_qty=5,
    )

    assert decision.action == ADDON_BLOCK
    assert decision.reason == "stop_not_at_breakeven_or_better"


def test_adding_to_winner_is_allowed_only_when_stop_is_protected():
    decision = ReEntryAddOnLogicBlock().evaluate_add_on(
        _position(stop_price=101.0),
        refreshed_signal_status="proceed",
        portfolio_decision=PortfolioOverlayDecision(PORTFOLIO_ALLOW, "ok"),
        requested_add_qty=5,
        requested_add_notional=700,
    )

    assert decision.action == ADDON_ALLOW
    assert decision.add_qty == 5


def test_add_on_size_cap_enforced():
    decision = ReEntryAddOnLogicBlock().evaluate_add_on(
        _position(qty=10, stop_price=101.0),
        refreshed_signal_status="proceed",
        portfolio_decision=PortfolioOverlayDecision(PORTFOLIO_ALLOW, "ok"),
        requested_add_qty=20,
        requested_add_notional=5000,
    )

    assert decision.action == ADDON_ALLOW
    assert decision.add_qty == 10
    assert decision.add_notional == 1000.0
