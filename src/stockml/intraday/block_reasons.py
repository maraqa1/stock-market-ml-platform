from __future__ import annotations

from enum import Enum


class BlockReason(str, Enum):
    WIDE_SPREAD = "wide_spread"
    LOW_LIQUIDITY = "low_liquidity"
    HALTED = "halted"
    NEAR_OPEN = "near_open"
    NEAR_CLOSE = "near_close"
    EARNINGS_TODAY = "earnings_today"
    EARNINGS_AFTER_CLOSE = "earnings_after_close"
    STALE_QUOTE = "stale_quote"
    PROVIDER_DIVERGENCE = "provider_divergence"
    REGIME_BLOCK = "regime_block"
    SECTOR_CONCENTRATION = "sector_concentration"
    CORPORATE_ACTION = "corporate_action"
    NIGHTLY_SIGNAL_DROPPED = "nightly_signal_dropped"
    SYMBOL_COOLOFF = "symbol_cooloff"
    KILL_SWITCH_DAILY = "kill_switch_daily"
    KILL_SWITCH_WEEKLY = "kill_switch_weekly"
    KILL_SWITCH_TOTAL = "kill_switch_total"
    OVERTRADE_LIMIT = "overtrade_limit"
    LIVE_DISABLED = "live_disabled"
    MISC = "misc"


BLOCK_REASON_VALUES = frozenset(reason.value for reason in BlockReason)


def coerce_block_reason(value: str | BlockReason | None) -> BlockReason:
    if isinstance(value, BlockReason):
        return value
    if value in BLOCK_REASON_VALUES:
        return BlockReason(str(value))
    return BlockReason.MISC

