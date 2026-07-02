from __future__ import annotations

import pandas as pd

from stockml.candidates.short_side_policy import ShortSidePolicy
from stockml.diagnostics.short_side_performance_guard import evaluate_short_side_performance


def test_short_side_guard_disables_when_short_samples_below_threshold():
    frame = pd.DataFrame([{"symbol": "AAA", "side": "short", "realised_pnl": 10, "net_return_bps": 20}])
    out = evaluate_short_side_performance(frame, policy=ShortSidePolicy(min_closed_short_trades_for_enablement=50))
    assert out.iloc[0]["closed_short_trades"] == 1
    assert "INSUFFICIENT_DATA" in out.iloc[0]["short_policy_decision"]


def test_short_side_guard_disables_when_short_pnl_negative():
    frame = pd.DataFrame(
        [
            {"symbol": "AAA", "side": "short", "realised_pnl": -10, "net_return_bps": -20},
            {"symbol": "BBB", "side": "short", "realised_pnl": 2, "net_return_bps": 5},
        ]
    )
    out = evaluate_short_side_performance(frame, policy=ShortSidePolicy(min_closed_short_trades_for_enablement=1))
    assert out.iloc[0]["short_realised_pnl"] == -8
    assert "NEGATIVE_EDGE" in out.iloc[0]["short_policy_decision"]


def test_short_side_guard_disables_when_win_rate_below_threshold():
    frame = pd.DataFrame(
        [
            {"symbol": "AAA", "side": "short", "realised_pnl": -1, "net_return_bps": -10},
            {"symbol": "BBB", "side": "short", "realised_pnl": 3, "net_return_bps": 30},
        ]
    )
    out = evaluate_short_side_performance(frame, policy=ShortSidePolicy(min_closed_short_trades_for_enablement=1, min_short_win_rate_for_enablement=0.75))
    assert out.iloc[0]["short_win_rate"] == 0.5
    assert "LOW_WIN_RATE" in out.iloc[0]["short_policy_decision"]


def test_short_side_guard_disables_when_profit_factor_below_threshold():
    frame = pd.DataFrame(
        [
            {"symbol": "AAA", "side": "short", "realised_pnl": -10, "net_return_bps": -100},
            {"symbol": "BBB", "side": "short", "realised_pnl": 5, "net_return_bps": 50},
        ]
    )
    out = evaluate_short_side_performance(frame, policy=ShortSidePolicy(min_closed_short_trades_for_enablement=1, min_short_profit_factor_for_enablement=1.1))
    assert out.iloc[0]["short_profit_factor"] == 0.5
    assert "LOW_PROFIT_FACTOR" in out.iloc[0]["short_policy_decision"]


def test_reconstructed_attribution_generates_warning_but_reports_pnl():
    frame = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "side": "short",
                "realised_pnl": -12,
                "net_return_bps": -120,
                "opened_by_signal_id": "",
                "trigger_source": "position_snapshot_reconstruction",
                "signal_state_at_close": "estimated_exit_from_last_position_snapshot",
                "max_favourable_bps": 0,
                "max_adverse_bps": 0,
            }
        ]
    )
    out = evaluate_short_side_performance(frame, policy=ShortSidePolicy(min_closed_short_trades_for_enablement=1))
    assert out.iloc[0]["short_realised_pnl"] == -12
    assert out.iloc[0]["attribution_quality"] == "reconstructed"
    assert "short_side_decision_based_on_reconstructed_attribution" in out.iloc[0]["warnings"]
