from __future__ import annotations

from stockml.trading.outcome_reasons import OutcomeReason
from stockml.trading.snapshot_schema import default_stage_verdicts


LEGACY_TEXT_TO_ENUM = {
    "price_below_minimum": OutcomeReason.REJECTED_PRICE_MIN,
    "Price below minimum": OutcomeReason.REJECTED_PRICE_MIN,
    "price_band": OutcomeReason.REJECTED_PRICE_BAND,
    "REJECTED_PRICE_BAND": OutcomeReason.REJECTED_PRICE_BAND,
    "market_cap_below_minimum": OutcomeReason.REJECTED_MARKETCAP_MIN,
    "Market cap below minimum": OutcomeReason.REJECTED_MARKETCAP_MIN,
    "volatility_extreme": OutcomeReason.REJECTED_VOLATILITY_EXTREME,
    "Volatility extreme": OutcomeReason.REJECTED_VOLATILITY_EXTREME,
    "bottom_intraday_range_after_gap_down": OutcomeReason.REJECTED_INTRADAY_PATTERN_GAP_DOWN,
    "Price closed near bottom of intraday range after gap down": OutcomeReason.REJECTED_INTRADAY_PATTERN_GAP_DOWN,
    "intraday_move_extreme_negative": OutcomeReason.REJECTED_INTRADAY_PATTERN_NEGATIVE,
    "Intraday move extremely negative": OutcomeReason.REJECTED_INTRADAY_PATTERN_NEGATIVE,
    "liquidity_below_minimum": OutcomeReason.REJECTED_LIQUIDITY_THIN,
    "Liquidity below minimum": OutcomeReason.REJECTED_LIQUIDITY_THIN,
    "REJECTED_LIQUIDITY_THIN": OutcomeReason.REJECTED_LIQUIDITY_THIN,
    "REJECTED_WIDE_SPREAD": OutcomeReason.REJECTED_WIDE_SPREAD,
    "REJECTED_TIME_OF_DAY": OutcomeReason.REJECTED_TIME_OF_DAY,
    "REJECTED_SIGNAL_STALE": OutcomeReason.REJECTED_SIGNAL_STALE,
    "REJECTED_SIGNAL_FRESH": OutcomeReason.REJECTED_SIGNAL_FRESH,
    "REJECTED_HALTED": OutcomeReason.REJECTED_HALTED,
    "REJECTED_EARNINGS_TODAY": OutcomeReason.REJECTED_EARNINGS_TODAY,
    "REJECTED_EARNINGS_RECENT": OutcomeReason.REJECTED_EARNINGS_RECENT,
    "REJECTED_CONTINUATION_THRESHOLD": OutcomeReason.REJECTED_CONTINUATION_THRESHOLD,
    "REJECTED_REVERSAL_RISK_TOO_HIGH": OutcomeReason.REJECTED_REVERSAL_RISK_TOO_HIGH,
    "REJECTED_MARKET_MISALIGNED": OutcomeReason.REJECTED_MARKET_MISALIGNED,
    "REJECTED_SECTOR_MISALIGNED": OutcomeReason.REJECTED_SECTOR_MISALIGNED,
    "REJECTED_SYMBOL_ACTIVITY_LIMIT": OutcomeReason.REJECTED_SYMBOL_ACTIVITY_LIMIT,
    "REJECTED_DAILY_CANDIDATE_CAP": OutcomeReason.REJECTED_DAILY_CANDIDATE_CAP,
    "REJECTED_NO_BORROW": OutcomeReason.REJECTED_NO_BORROW,
    "Meta label probability below threshold": OutcomeReason.REJECTED_META_LABEL_THRESHOLD,
    "Meta-label probability below threshold": OutcomeReason.REJECTED_META_LABEL_THRESHOLD,
    "meta_label_probability_below_threshold": OutcomeReason.REJECTED_META_LABEL_THRESHOLD,
    "expected_trade_return_below_threshold": OutcomeReason.REJECTED_EXPECTED_RETURN_THRESHOLD,
    "Expected return below threshold": OutcomeReason.REJECTED_EXPECTED_RETURN_THRESHOLD,
    "Expected trade return below threshold": OutcomeReason.REJECTED_EXPECTED_RETURN_THRESHOLD,
    "risk_adjusted_score_below_threshold": OutcomeReason.REJECTED_RISK_ADJUSTED_THRESHOLD,
    "Risk-adjusted score below threshold": OutcomeReason.REJECTED_RISK_ADJUSTED_THRESHOLD,
    "Expected trade return below transaction cost": OutcomeReason.REJECTED_EXPECTED_RETURN_BELOW_COST,
    "expected_trade_return_below_transaction_cost": OutcomeReason.REJECTED_EXPECTED_RETURN_BELOW_COST,
    "Trimmed size": OutcomeReason.REJECTED_SIZE_TRIMMED_TO_ZERO,
    "reduced": OutcomeReason.REJECTED_SIZE_TRIMMED_TO_ZERO,
    "quantity_below_one": OutcomeReason.REJECTED_SIZE_TRIMMED_TO_ZERO,
    "wide_spread": OutcomeReason.BLOCKED_WIDE_SPREAD,
    "Wide spread": OutcomeReason.BLOCKED_WIDE_SPREAD,
    "stale_quote": OutcomeReason.BLOCKED_STALE_QUOTE,
    "earnings_today": OutcomeReason.BLOCKED_EARNINGS_TODAY,
    "kill_switch": OutcomeReason.BLOCKED_KILL_SWITCH,
    "overtrade_limit": OutcomeReason.BLOCKED_OVERTRADE_LIMIT,
}


def normalize_reason(text: str | None) -> OutcomeReason | None:
    clean = str(text or "").strip()
    if not clean:
        return None
    if clean.lower() == "approved":
        return None
    for legacy, enum_val in LEGACY_TEXT_TO_ENUM.items():
        if clean.lower() == legacy.lower():
            return enum_val
    return None


def _stage_for_reason(reason: OutcomeReason) -> tuple[str, str]:
    if reason == OutcomeReason.REJECTED_SIZE_TRIMMED_TO_ZERO:
        return "sizing", "trimmed_to_zero"
    if reason == OutcomeReason.REJECTED_META_LABEL_THRESHOLD:
        return "meta_label", "rejected:below_threshold"
    if reason.value.startswith("blocked_"):
        return "intraday_gate", f"block:{reason.value.removeprefix('blocked_')}"
    if reason.value.startswith("near_miss_"):
        return "trade_quality", f"near_miss:{reason.value.removeprefix('near_miss_')}"
    return "trade_quality", f"rejected:{reason.value.removeprefix('rejected_')}"


def normalize_concatenated(text: str | None) -> tuple[OutcomeReason | None, dict[str, str]]:
    verdicts = default_stage_verdicts()
    raw_parts = [part.strip() for part in str(text or "").replace("|", ";").split(";") if part.strip()]
    terminal: OutcomeReason | None = None
    unrecognized: list[str] = []
    for part in raw_parts:
        if part.lower() == "approved":
            verdicts["trade_quality"] = "approved"
            continue
        reason = normalize_reason(part)
        if reason is None:
            unrecognized.append(part)
            continue
        stage, verdict = _stage_for_reason(reason)
        verdicts[stage] = verdict
        terminal = reason
    if unrecognized:
        verdicts["operator"] = "unrecognized:" + "|".join(unrecognized)
    return terminal, verdicts
