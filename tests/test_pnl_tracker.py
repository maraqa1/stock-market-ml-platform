import pandas as pd

from stockml.trading.paper_portfolio import portfolio_summary
from stockml.trading.pnl_tracker import position_pnl_summary, realized_trade_pnl


def test_position_pnl_summary_uses_position_fields():
    positions = pd.DataFrame([{"symbol": "FLEX", "qty": 2, "market_value": 220, "cost_basis": 200}])
    summary = position_pnl_summary(positions)
    assert summary.iloc[0]["unrealized_pl"] == 20
    assert round(summary.iloc[0]["unrealized_plpc"], 4) == 0.1


def test_realized_trade_pnl_handles_long_and_short():
    trades = pd.DataFrame(
        [
            {"symbol": "AAA", "side": "buy", "qty": 10, "entry_price": 10, "exit_price": 11},
            {"symbol": "BBB", "side": "sell", "qty": 10, "entry_price": 10, "exit_price": 9},
        ]
    )
    pnl = realized_trade_pnl(trades)
    assert list(pnl["realized_pnl"]) == [10, 10]


def test_portfolio_summary_totals_positions():
    positions = pd.DataFrame(
        [
            {"symbol": "AAA", "market_value": 100, "cost_basis": 90, "unrealized_pl": 10},
            {"symbol": "BBB", "market_value": 200, "cost_basis": 210, "unrealized_pl": -10},
        ]
    )
    summary = portfolio_summary(positions)
    assert summary["position_count"] == 2
    assert summary["market_value"] == 300
    assert summary["unrealized_pl"] == 0
