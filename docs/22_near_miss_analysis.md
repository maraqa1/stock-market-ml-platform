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

Unknown rejection reasons are preserved with `unknown` severity so new guardrails do not break the report or pretend an unmeasured gap is measurable.

## Severity

- `near_miss`: candidate is within 10% of the required threshold.
- `moderate_gap`: candidate is within 25% of the required threshold.
- `hard_fail`: candidate is more than 25% away from passing.
- `unknown`: the reason, actual value, or required threshold is unavailable.

For minimum thresholds, distance is `required - actual`. For maximum thresholds such as spread and volatility, distance is `actual - required`.

## Portal

The `/trading` page includes a **Near Misses** zone. It shows summary cards for:

- total rejected or trimmed diagnostic candidates
- total near misses
- total moderate gaps
- hard fails
- most common failed gate

The table shows symbol, status, failed gate, actual value, required value, distance, severity, and readable reason.

Near misses are diagnostic only. They are not automatically promoted to trades.

## Paper Autopilot Fallback

Paper Autopilot can use near-miss rows as a guarded paper-only open source. It runs after strong intraday candidates and before the broader flat-account fallback so cleaner near misses are not skipped in favor of weaker watch-list names.

The fallback is controlled in `config/autopilot.yaml`:

- `near_miss_fallback_enabled`: enables the paper-only fallback.
- `near_miss_fallback_requires_flat_account`: optionally require no positions to be open. The default is `false` so near-miss candidates can fill remaining slots while another position is held.
- `near_miss_fallback_max_per_day`: caps paper opens from near-miss rows.
- `near_miss_fallback_size_multiplier`: reduces order size versus normal auto-open sizing.
- `near_miss_fallback_max_distance_pct`: maximum threshold miss distance.
- `near_miss_fallback_allowed_gates`: explicit gate allow-list.

The default gate allow-list excludes `price_below_minimum`, `liquidity_below_minimum`, `probability_edge_below_threshold`, and `side_probability_below_threshold`. The default near-miss cap is 5 per day, with half-size orders. Live trading remains disabled; the path submits only guarded paper orders when Paper Autopilot auto-open is enabled.
