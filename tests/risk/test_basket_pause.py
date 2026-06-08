from __future__ import annotations

from stockml.autopilot.basket_risk import BasketRiskConfig, evaluate_basket_risk


CFG = BasketRiskConfig(
    pause_new_entries_if_red_position_pct_above=0.70,
    pause_new_entries_if_basket_return_below=-0.0075,
    resume_new_entries_if_basket_return_above=-0.0025,
    min_positions_for_percentage_rule=5,
    small_book_basket_return_floor_pct=-0.015,
    small_book_loss_floor_pct=-0.02,
)


def _position(symbol: str, plpc: float, cost_basis: float = 100.0) -> dict:
    return {
        "symbol": symbol,
        "cost_basis": cost_basis,
        "unrealized_pl": cost_basis * plpc,
        "unrealized_plpc": plpc,
    }


def test_one_position_red_does_not_pause():
    state = evaluate_basket_risk([_position("BNY", -0.0055)], config=CFG)

    assert state.new_entries_paused is False
    assert state.basket_state == "normal"
    assert state.red_position_pct == 1.0
    assert round(state.basket_return, 4) == -0.0055


def test_one_position_red_paused_only_at_absolute_floor():
    state = evaluate_basket_risk([_position("BNY", -0.016)], config=CFG)

    assert state.new_entries_paused is True
    assert state.reason == "small_book_basket_return_pause"
    assert "1-position book" in state.reason_text
    assert "-1.6%" in state.reason_text


def test_five_positions_uses_percentage_rule():
    rows = [_position(f"R{i}", -0.01) for i in range(4)] + [_position("GREEN", 0.0)]

    state = evaluate_basket_risk(rows, config=CFG)

    assert state.new_entries_paused is True
    assert state.reason == "red_position_pct_pause"
    assert round(state.red_position_pct, 2) == 0.8
    assert round(state.basket_return, 3) == -0.008


def test_hard_daily_loss_always_pauses():
    state = evaluate_basket_risk(
        [_position("BNY", 0.01)],
        config=CFG,
        daily_realized_pnl=-201.0,
        account_equity=10_000.0,
    )

    assert state.new_entries_paused is True
    assert state.reason == "hard_daily_loss_pause"
    assert "daily realized loss" in state.reason_text


def test_resume_threshold_unchanged():
    paused = evaluate_basket_risk([_position("AAA", -0.003)], config=CFG, previous_state="new_entries_paused")
    resumed = evaluate_basket_risk([_position("AAA", -0.002)], config=CFG, previous_state="new_entries_paused")

    assert paused.new_entries_paused is True
    assert paused.reason == "basket_drawdown_pause_not_resumed"
    assert resumed.new_entries_paused is False


def test_banner_text_reflects_active_rule():
    small_book = evaluate_basket_risk([_position("BNY", -0.016)], config=CFG)
    large_book = evaluate_basket_risk([_position(f"R{i}", -0.01) for i in range(4)] + [_position("GREEN", 0)], config=CFG)
    hard_loss = evaluate_basket_risk([_position("AAA", 0.01)], config=CFG, daily_realized_pnl=-250, account_equity=10_000)

    assert "1-position book" in small_book.reason_text
    assert "positions in the red" in large_book.reason_text
    assert "daily realized loss" in hard_loss.reason_text
