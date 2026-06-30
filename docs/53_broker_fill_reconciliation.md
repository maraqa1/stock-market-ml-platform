# Broker Fill Reconciliation

This read-only diagnostic compares Alpaca paper order artifacts, activity journal events, and the unified trade ledger.

It does not submit orders, alter model scoring, loosen gates, change thresholds, or enable live trading.

## Output

- `data/trading/diagnostics/broker_fill_reconciliation_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/broker_fill_reconciliation_summary_YYYYMMDD_HHMMSS.md`

## Interpretation

- `matched_fill`: broker fill, activity fill, and ledger trade are linked.
- `missing_activity_fill`: broker fill exists but no fill event reached the activity journal.
- `missing_ledger_trade`: activity fill exists but the ledger did not build a trade.
- `submitted_not_filled`: order is still live or accepted without a fill.
- `dry_run`: plan-only row, no broker fill expected.
