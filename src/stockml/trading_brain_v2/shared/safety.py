from __future__ import annotations

from stockml.trading_brain_v2.shared.config import TradingBrainConfig, load_trading_brain_config


class TradingBrainV2LiveExecutionBlocked(RuntimeError):
    """Raised when V2 attempts live execution without explicit permission."""


def assert_v2_live_execution_allowed(
    *,
    requested_live_execution: bool,
    config: TradingBrainConfig | None = None,
) -> None:
    cfg = config or load_trading_brain_config()
    if requested_live_execution and not cfg.v2_allow_live_execution:
        raise TradingBrainV2LiveExecutionBlocked("trading_brain_v2_live_execution_disabled")

