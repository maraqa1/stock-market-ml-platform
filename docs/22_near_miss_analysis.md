# Near-Miss Analysis

Near-miss analysis is a diagnostic-only layer for rejected paper candidates. It helps the operator see whether a rejected name was close to passing a configured threshold or failed by a large margin.

It does not recommend trades, promote candidates, submit orders, or bypass guardrails.

## Inputs

The analysis reads the latest trading artifacts:

- paper candidate pool
- paper order plan
- rejected and trimmed basket rows
- intraday promotion rows when available

## Output

Each portal render writes the latest diagnostic snapshot to:

```bash
data/trading/near_miss/near_miss_YYYYMMDD_HHMMSS.csv
```

The output schema is stable:

- `symbol`
- `side`
- `trade_action`
- `status`
- `failed_gate`
- `failed_gate_label`
- `actual_value`
- `required_value`
- `distance_to_pass`
- `distance_pct`
- `severity`
- `reason`
- `candidate_rank`
- `side_probability`
- `probability_edge`
- `expected_trade_return`
- `risk_adjusted_score`
- `current_price`
- `market_cap`
- `avg_dollar_volume_20d`
- `volatility_20d`
- `risk_tier`
- `liquidity_tier`
- `volatility_tier`

## Supported Gates

- `expected_trade_return_below_threshold`
- `risk_adjusted_score_below_threshold`
- `market_cap_below_minimum`
- `price_below_minimum`
- `liquidity_below_minimum`
- `volatility_extreme`
- `wide_spread`
- `probability_edge_below_threshold`
- `side_probability_below_threshold`

Unknown rejection reasons are preserved as diagnostic hard failures so new guardrails do not break the report.

## Severity

- `near_miss`: candidate is within 10% of the required threshold.
- `moderate_gap`: candidate is within 25% of the required threshold.
- `hard_fail`: candidate is more than 25% away from passing, or the reason cannot be measured.

For minimum thresholds, distance is `required - actual`. For maximum thresholds such as spread and volatility, distance is `actual - required`.

## Portal

The `/trading` page includes a **Near Misses** zone. It shows summary cards for:

- total near misses
- hard fails
- most common failed gate
- number of gate types close to passing

The table shows symbol, failed gate, actual value, required value, distance, severity, and readable reason.

