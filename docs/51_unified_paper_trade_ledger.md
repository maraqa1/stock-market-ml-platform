# Unified Paper Trade Ledger

The unified paper trade ledger is a read-only diagnostic artifact that joins paper activity journal events into trade-level rows.

It does not change model scoring, candidate selection, risk thresholds, execution behavior, order sizing, or live trading state.

## Linkage Order

The builder prefers lifecycle identifiers in this order:

1. `trade_id`
2. `position_id`
3. `broker_order_id`
4. `client_order_id`
5. `candidate_id`
6. `signal_id`
7. `symbol + timestamp` fallback, marked `lineage_quality=low`

Submitted orders do not create trades. Opening fills create trade rows. Closing fills are linked back to an existing trade where possible; orphan closes are written to the unmatched lifecycle report.

## Outputs

- `data/trading/diagnostics/trade_ledger_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/unmatched_lifecycle_events_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/trade_ledger_summary_YYYYMMDD_HHMMSS.md`

## Fit for Attribution

The summary returns one of:

- `FIT_FOR_ATTRIBUTION`
- `PARTIAL_ATTRIBUTION_ONLY`
- `NOT_FIT_FIX_LINEAGE`
- `NOT_FIT_NO_TRADES`
- `NOT_FIT_INSUFFICIENT_PRICES`

A ledger is fit when at least one filled trade has a usable entry price and reliable side. Open-only ledgers are partial attribution. Missing prices or unlinked fills are not fit for full attribution.
