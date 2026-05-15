from __future__ import annotations

DIAGNOSTIC_NOTICE = "DIAGNOSTIC ONLY - NOT FOR ORDER SUBMISSION"
FORECAST_VERSION = 1

TIER_A_FIELDS = [
    "symbol",
    "forecast_scope",
    "is_open_position",
    "position_qty",
    "position_entry_price",
    "position_unrealized_plpc",
    "side",
    "current_trade_action",
    "candidate_rank",
    "model_score",
    "model_risk_adjusted_score",
    "meta_label_probability",
    "current_price",
    "vwap_distance_bps",
    "intraday_range_position",
    "spread_bps",
    "dollar_volume_today",
]

TIER_B_FIELDS = [
    "direction_context",
    "direction_basis",
    "expected_1d_return_bps",
    "expected_5d_return_bps",
    "expected_move_bps",
    "expected_move_bps_calibrated",
    "cap_applied",
    "pre_cap_expected_5d_bps",
    "units_audit",
    "magnitude_bucket",
    "downside_risk_bps",
    "upside_risk_bps",
    "volatility_adjusted_score",
    "spread_penalty",
    "liquidity_penalty",
    "risk_adjusted_forecast_score",
    "expected_profitability_score",
    "forecast_risk_penalty",
    "confirmation_quality",
    "operator_priority",
    "forecast_confirmation",
    "confirmation_score",
    "confirmation_reason",
    "side_alignment",
    "magnitude_ok",
    "profitability_ok",
    "risk_reward_ok",
    "suggested_stop_bps",
    "suggested_take_profit_bps",
    "invalidation_level",
    "forecast_reason",
    "regime_label",
]

TIER_C_FIELDS = [
    "forecast_direction",
    "direction_probability",
    "magnitude_confidence",
    "side_probability",
    "probability_edge",
    "calibrated_regime_probability",
]

OUTPUT_COLUMNS = [
    "diagnostic_only",
    "diagnostic_notice",
    "forecast_version",
    "generated_at",
    *TIER_A_FIELDS,
    *TIER_B_FIELDS,
    *TIER_C_FIELDS,
    "tier_a_status",
    "tier_b_status",
    "tier_c_status",
    "field_status",
]


def null_tier_c_fields() -> dict[str, object]:
    return {field: None for field in TIER_C_FIELDS}


def output_record(record: dict[str, object]) -> dict[str, object]:
    clean = {column: record.get(column) for column in OUTPUT_COLUMNS}
    clean["diagnostic_only"] = True
    clean["diagnostic_notice"] = DIAGNOSTIC_NOTICE
    clean["forecast_version"] = FORECAST_VERSION
    clean["tier_a_status"] = clean.get("tier_a_status") or "populated"
    clean["tier_b_status"] = clean.get("tier_b_status") or "populated"
    clean["tier_c_status"] = clean.get("tier_c_status") or "uncalibrated"
    clean["field_status"] = clean.get("field_status") or "tier_a=populated;tier_b=populated;tier_c=uncalibrated"
    return clean
