from __future__ import annotations

from typing import Any

from stockml.trading_brain_v2.shared.config import TradingBrainConfig
from stockml.trading_brain_v2.shared.safety import assert_v2_live_execution_allowed


def submit_live_order_placeholder(order: dict[str, Any], *, config: TradingBrainConfig | None = None) -> None:
    """Placeholder for a future live handoff.

    The skeleton intentionally has no broker implementation. The guard exists so
    tests can prove live execution is impossible by default.
    """
    assert_v2_live_execution_allowed(requested_live_execution=True, config=config)
    raise NotImplementedError("trading_brain_v2_live_handoff_not_implemented")

