# Candidate-To-Trade Attribution

This read-only diagnostic joins unified trade-ledger rows back to the candidate/order-plan context that caused the trade.

It does not alter scoring, gates, thresholds, order sizing, or execution behavior.

## Join Order

1. `candidate_id`
2. `client_order_id`
3. `symbol + cycle_id`
4. `symbol` fallback, marked `low`

## Outputs

- `data/trading/diagnostics/candidate_trade_attribution_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/candidate_trade_attribution_summary_YYYYMMDD_HHMMSS.md`

## Key Fields

The report carries candidate rank, candidate status, order eligibility, trade-quality status/reason, model version, model score, expected return, risk-adjusted score, meta-label decision, session mode, and overnight tradability.
