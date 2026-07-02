from __future__ import annotations

import pandas as pd

from stockml.diagnostics.short_inverse_shadow import build_short_inverse_shadow


def test_inverse_shadow_calculates_opposite_long_return():
    candidates = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "side": "sell",
                "status": "research_only",
                "primary_block_reason": "short_side_validation_required",
                "forward_5d_return": 0.025,
            }
        ]
    )
    out = build_short_inverse_shadow(candidates, estimated_cost_bps=5)
    assert len(out) == 1
    assert out.iloc[0]["original_short_return_bps"] == -250
    assert out.iloc[0]["inverse_long_return_bps"] == 250
    assert out.iloc[0]["inverse_after_cost_bps"] == 245
    assert out.iloc[0]["shadow_only"] is True


def test_inverse_shadow_never_outputs_orders():
    out = build_short_inverse_shadow(pd.DataFrame([{"symbol": "AAA", "trade_action": "Short", "forward_return_bps": -10}]))
    assert "order_id" not in out.columns
    assert "submitted" not in out.columns
    assert out.iloc[0]["shadow_only"] is True


def test_inverse_shadow_ignores_longs():
    out = build_short_inverse_shadow(pd.DataFrame([{"symbol": "AAA", "side": "buy", "forward_return_bps": 10}]))
    assert out.empty
