from __future__ import annotations

import pandas as pd

from stockml.trading.position_sizing import SameDaySizingConfig, apply_same_day_sizing, same_day_default_notional


CFG = SameDaySizingConfig()


def _row(symbol: str, price: float = 10.0) -> dict:
    return {
        "symbol": symbol,
        "strategy_stream": "same_day_momentum",
        "current_price": price,
        "approved_notional": 500,
        "notional": 500,
        "suggested_quantity": 50,
        "trade_quality_status": "approved",
        "trade_quality_reason": "approved",
        "order_eligible": True,
    }


def test_same_day_sizing_below_account_floor():
    frame = pd.DataFrame([_row("AAA")])

    out = apply_same_day_sizing(frame, account_equity=249, config=CFG)

    assert out.iloc[0]["order_eligible"] == False
    assert out.iloc[0]["trade_quality_reason"] == "REJECTED_SAME_DAY_EQUITY_FLOOR"
    assert out.iloc[0]["approved_notional"] == 0


def test_same_day_default_notional_is_three_percent_capped_at_100():
    assert same_day_default_notional(1_000, CFG) == 30
    assert same_day_default_notional(10_000, CFG) == 100


def test_same_day_max_concurrent_enforced():
    frame = pd.DataFrame([_row("A"), _row("B"), _row("C"), _row("D")])

    out = apply_same_day_sizing(frame, account_equity=10_000, config=CFG)

    assert out["order_eligible"].tolist() == [True, True, True, False]
    assert out.iloc[3]["trade_quality_reason"] == "REJECTED_SAME_DAY_MAX_CONCURRENT"


def test_same_day_daily_loss_halt():
    frame = pd.DataFrame([_row("AAA")])

    out = apply_same_day_sizing(frame, account_equity=10_000, same_day_realized_pnl_today=-50, config=CFG)

    assert out.iloc[0]["order_eligible"] == False
    assert out.iloc[0]["trade_quality_reason"] == "REJECTED_SAME_DAY_LOSS_LIMIT"


def test_multi_day_unaffected_by_same_day_halt():
    frame = pd.DataFrame(
        [
            _row("DAY"),
            {
                **_row("MULTI"),
                "strategy_stream": "multi_day_forecast",
                "approved_notional": 500,
                "notional": 500,
                "suggested_quantity": 50,
            },
        ]
    )

    out = apply_same_day_sizing(frame, account_equity=10_000, same_day_realized_pnl_today=-50, config=CFG)

    assert out.loc[out["symbol"].eq("DAY"), "order_eligible"].iloc[0] == False
    assert out.loc[out["symbol"].eq("MULTI"), "order_eligible"].iloc[0] == True
    assert out.loc[out["symbol"].eq("MULTI"), "approved_notional"].iloc[0] == 500


def test_same_day_total_exposure_cap_enforced():
    open_positions = pd.DataFrame([{"symbol": "OLD", "strategy_stream": "same_day_momentum", "market_value": 1_450}])
    frame = pd.DataFrame([_row("NEW")])

    out = apply_same_day_sizing(frame, account_equity=10_000, open_positions=open_positions, config=CFG)

    assert out.iloc[0]["order_eligible"] == False
    assert out.iloc[0]["trade_quality_reason"] == "REJECTED_SAME_DAY_EXPOSURE_CAP"
