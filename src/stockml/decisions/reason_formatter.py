from __future__ import annotations


REASON_LABELS = {
    "not_long_or_short": "Neutral decision",
    "model_not_decision_grade": "Model not decision-grade",
    "no_decision_reason_present": "Neutral reason present",
    "current_price_missing": "Current price missing",
    "current_price_invalid": "Current price invalid",
    "price_below_minimum": "Price below minimum",
    "market_cap_missing": "Market cap missing",
    "market_cap_below_minimum": "Market cap below minimum",
    "avg_dollar_volume_missing": "Average dollar volume missing",
    "liquidity_below_minimum": "Liquidity below minimum",
    "volatility_missing": "Volatility missing",
    "volatility_extreme": "Volatility extreme",
    "bottom_intraday_range_after_gap_down": "Price closed near bottom of intraday range after gap down",
    "intraday_move_extreme_negative": "Intraday move extremely negative",
    "expected_trade_return_below_threshold": "Expected trade return below threshold",
    "risk_adjusted_score_below_threshold": "Risk-adjusted score below threshold",
    "shorting_disabled": "Shorting disabled",
    "sector_exposure_limit": "Sector exposure limit reached",
    "max_daily_orders_reached": "Maximum daily orders reached",
    "max_basket_notional_reached": "Maximum basket notional reached",
    "quantity_below_one": "Position size too small to buy one share",
    "stop_loss_unavailable": "Stop loss unavailable",
    "take_profit_unavailable": "Take profit unavailable",
    "risk_tier_reject": "Risk tier rejected",
    "operator_keep_position": "Operator chose to keep position",
    "no_eligible_replacement_available": "No eligible replacement available (excluded held names or insufficient rank improvement)",
    "manual_close_dry_run_submit_orders_disabled": "Manual close recorded as dry-run because order submission is disabled",
    "manual_close_submitted": "Manual paper close submitted",
    "manual_close_alpaca_api_error": "Manual paper close failed at Alpaca API",
    "live_trading_disabled_for_manual_close": "Live trading is disabled for manual close",
    "paper_trading_disabled": "Paper trading is disabled",
    "alpaca_credentials_missing": "Alpaca credentials missing",
    "unsupported_operator_action": "Unsupported operator action",
    "symbol_required": "Symbol required",
    "approved": "Approved",
    "reduced": "Trimmed size",
}


def format_reason_code(value: object) -> str:
    code = str(value or "").strip()
    if not code:
        return "Not provided"
    return REASON_LABELS.get(code, code.replace("_", " ").capitalize())


def format_reasons(value: object) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return "Not provided"
    return "; ".join(format_reason_code(part.strip()) for part in text.split("|") if part.strip())
