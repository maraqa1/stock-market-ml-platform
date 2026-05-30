from __future__ import annotations

from enum import Enum


class OutcomeReason(str, Enum):
    ACCEPTED = "accepted"

    REJECTED_PRICE_MIN = "rejected_price_min"
    REJECTED_PRICE_BAND = "rejected_price_band"
    REJECTED_MARKETCAP_MIN = "rejected_marketcap_min"
    REJECTED_VOLATILITY_EXTREME = "rejected_volatility_extreme"
    REJECTED_INTRADAY_PATTERN_GAP_DOWN = "rejected_intraday_pattern_gap_down"
    REJECTED_INTRADAY_PATTERN_NEGATIVE = "rejected_intraday_pattern_negative"
    REJECTED_LIQUIDITY_THIN = "rejected_liquidity_thin"
    REJECTED_WIDE_SPREAD = "rejected_wide_spread"
    REJECTED_TIME_OF_DAY = "rejected_time_of_day"
    REJECTED_SIGNAL_STALE = "rejected_signal_stale"
    REJECTED_SIGNAL_FRESH = "rejected_signal_fresh"
    REJECTED_HALTED = "rejected_halted"
    REJECTED_EARNINGS_TODAY = "rejected_earnings_today"
    REJECTED_EARNINGS_RECENT = "rejected_earnings_recent"
    REJECTED_CONTINUATION_THRESHOLD = "rejected_continuation_threshold"
    REJECTED_REVERSAL_RISK_TOO_HIGH = "rejected_reversal_risk_too_high"
    REJECTED_MARKET_MISALIGNED = "rejected_market_misaligned"
    REJECTED_SECTOR_MISALIGNED = "rejected_sector_misaligned"
    REJECTED_SYMBOL_ACTIVITY_LIMIT = "rejected_symbol_activity_limit"
    REJECTED_DAILY_CANDIDATE_CAP = "rejected_daily_candidate_cap"
    REJECTED_NO_BORROW = "rejected_no_borrow"

    REJECTED_META_LABEL_THRESHOLD = "rejected_meta_label_threshold"
    REJECTED_EXPECTED_RETURN_THRESHOLD = "rejected_expected_return_threshold"
    REJECTED_RISK_ADJUSTED_THRESHOLD = "rejected_risk_adjusted_threshold"
    REJECTED_EXPECTED_RETURN_BELOW_COST = "rejected_expected_return_below_cost"

    REJECTED_SIZE_TRIMMED_TO_ZERO = "rejected_size_trimmed_to_zero"
    REJECTED_UNKNOWN = "rejected_unknown"

    NEAR_MISS_SCORE = "near_miss_score"
    NEAR_MISS_LIQUIDITY = "near_miss_liquidity"

    BLOCKED_STALE_QUOTE = "blocked_stale_quote"
    BLOCKED_WIDE_SPREAD = "blocked_wide_spread"
    BLOCKED_EARNINGS_TODAY = "blocked_earnings_today"
    BLOCKED_KILL_SWITCH = "blocked_kill_switch"
    BLOCKED_OVERTRADE_LIMIT = "blocked_overtrade_limit"
    BLOCKED_UNKNOWN = "blocked_unknown"

    OPEN_CANDIDATE = "open_candidate"


HUMAN_LABELS = {
    OutcomeReason.ACCEPTED: "Accepted",
    OutcomeReason.REJECTED_PRICE_MIN: "Price below minimum",
    OutcomeReason.REJECTED_PRICE_BAND: "Outside same-day price band",
    OutcomeReason.REJECTED_MARKETCAP_MIN: "Market cap below minimum",
    OutcomeReason.REJECTED_VOLATILITY_EXTREME: "Volatility extreme",
    OutcomeReason.REJECTED_INTRADAY_PATTERN_GAP_DOWN: "Closed near bottom after gap down",
    OutcomeReason.REJECTED_INTRADAY_PATTERN_NEGATIVE: "Intraday move extremely negative",
    OutcomeReason.REJECTED_LIQUIDITY_THIN: "Liquidity below minimum",
    OutcomeReason.REJECTED_WIDE_SPREAD: "Spread too wide",
    OutcomeReason.REJECTED_TIME_OF_DAY: "Outside same-day trading window",
    OutcomeReason.REJECTED_SIGNAL_STALE: "Signal too stale",
    OutcomeReason.REJECTED_SIGNAL_FRESH: "Signal too fresh",
    OutcomeReason.REJECTED_HALTED: "Symbol halted",
    OutcomeReason.REJECTED_EARNINGS_TODAY: "Earnings today",
    OutcomeReason.REJECTED_EARNINGS_RECENT: "Recent earnings risk",
    OutcomeReason.REJECTED_CONTINUATION_THRESHOLD: "Continuation probability below threshold",
    OutcomeReason.REJECTED_REVERSAL_RISK_TOO_HIGH: "Reversal risk too high",
    OutcomeReason.REJECTED_MARKET_MISALIGNED: "Market misaligned",
    OutcomeReason.REJECTED_SECTOR_MISALIGNED: "Sector misaligned",
    OutcomeReason.REJECTED_SYMBOL_ACTIVITY_LIMIT: "Symbol activity limit reached",
    OutcomeReason.REJECTED_DAILY_CANDIDATE_CAP: "Daily candidate cap reached",
    OutcomeReason.REJECTED_NO_BORROW: "Short borrow unavailable",
    OutcomeReason.REJECTED_META_LABEL_THRESHOLD: "Meta-label probability below threshold",
    OutcomeReason.REJECTED_EXPECTED_RETURN_THRESHOLD: "Expected return below threshold",
    OutcomeReason.REJECTED_RISK_ADJUSTED_THRESHOLD: "Risk-adjusted score below threshold",
    OutcomeReason.REJECTED_EXPECTED_RETURN_BELOW_COST: "Expected return below cost",
    OutcomeReason.REJECTED_SIZE_TRIMMED_TO_ZERO: "Trimmed size to zero",
    OutcomeReason.REJECTED_UNKNOWN: "Unknown rejection reason",
    OutcomeReason.NEAR_MISS_SCORE: "Score below cut by small margin",
    OutcomeReason.NEAR_MISS_LIQUIDITY: "Liquidity below cut by small margin",
    OutcomeReason.BLOCKED_STALE_QUOTE: "Stale quote",
    OutcomeReason.BLOCKED_WIDE_SPREAD: "Spread too wide",
    OutcomeReason.BLOCKED_EARNINGS_TODAY: "Earnings today",
    OutcomeReason.BLOCKED_KILL_SWITCH: "Kill-switch tripped",
    OutcomeReason.BLOCKED_OVERTRADE_LIMIT: "Overtrade limit reached",
    OutcomeReason.BLOCKED_UNKNOWN: "Unknown block reason",
    OutcomeReason.OPEN_CANDIDATE: "Open candidate",
}


def human_label(reason: OutcomeReason | str | None) -> str:
    if reason in [None, ""]:
        return ""
    try:
        enum_value = reason if isinstance(reason, OutcomeReason) else OutcomeReason(str(reason))
    except ValueError:
        return str(reason).replace("_", " ").capitalize()
    return HUMAN_LABELS[enum_value]
