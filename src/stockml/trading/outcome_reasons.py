from __future__ import annotations

from enum import Enum


class OutcomeReason(str, Enum):
    ACCEPTED = "accepted"

    REJECTED_PRICE_MIN = "rejected_price_min"
    REJECTED_MARKETCAP_MIN = "rejected_marketcap_min"
    REJECTED_VOLATILITY_EXTREME = "rejected_volatility_extreme"
    REJECTED_INTRADAY_PATTERN_GAP_DOWN = "rejected_intraday_pattern_gap_down"
    REJECTED_INTRADAY_PATTERN_NEGATIVE = "rejected_intraday_pattern_negative"
    REJECTED_LIQUIDITY_THIN = "rejected_liquidity_thin"

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
    OutcomeReason.REJECTED_MARKETCAP_MIN: "Market cap below minimum",
    OutcomeReason.REJECTED_VOLATILITY_EXTREME: "Volatility extreme",
    OutcomeReason.REJECTED_INTRADAY_PATTERN_GAP_DOWN: "Closed near bottom after gap down",
    OutcomeReason.REJECTED_INTRADAY_PATTERN_NEGATIVE: "Intraday move extremely negative",
    OutcomeReason.REJECTED_LIQUIDITY_THIN: "Liquidity below minimum",
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
