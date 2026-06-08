from stockml.autopilot.basket_risk import BasketRiskConfig, evaluate_basket_risk


def _position(symbol: str, plpc: float, cost_basis: float = 100.0) -> dict:
    return {
        "symbol": symbol,
        "cost_basis": cost_basis,
        "unrealized_pl": cost_basis * plpc,
        "unrealized_plpc": plpc,
    }


def test_10_red_out_of_11_positions_pauses_new_entries():
    rows = [_position(f"R{i}", -0.01) for i in range(10)] + [_position("GREEN", 0.01)]

    state = evaluate_basket_risk(rows)

    assert state.new_entries_paused is True
    assert state.basket_state == "new_entries_paused"
    assert round(state.red_position_pct, 4) == round(10 / 11, 4)
    assert state.reason == "red_position_pct_pause"


def test_basket_return_below_threshold_pauses_new_entries():
    rows = [_position("AAA", -0.016)]

    state = evaluate_basket_risk(rows)

    assert state.new_entries_paused is True
    assert state.basket_state == "new_entries_paused"
    assert state.basket_return < -0.015
    assert state.reason == "small_book_basket_return_pause"


def test_basket_return_uses_gross_basis_for_short_positions():
    rows = [
        {"symbol": "LONG", "cost_basis": 100, "unrealized_pl": -1, "unrealized_plpc": -0.01},
        {"symbol": "SHORT", "cost_basis": -100, "unrealized_pl": -1, "unrealized_plpc": -0.01},
    ]

    state = evaluate_basket_risk(rows)

    assert state.basket_return == -0.01


def test_basket_risk_does_not_pause_when_recovered():
    rows = [_position("AAA", -0.001), _position("BBB", 0.004)]

    state = evaluate_basket_risk(rows, config=BasketRiskConfig())

    assert state.new_entries_paused is False
    assert state.basket_state == "normal"
