# Trade Inverse Outcome Diagnostic

This read-only diagnostic tests a narrow question: for already closed paper trades, what would the opposite side have produced over the same entry and exit window?

It does not change model scoring, gates, position sizing, execution, or live-trading behavior. It is intended to support polarity review before any strategy change.

Run:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_trade_inverse_outcome.py
```

Outputs:

- `data/trading/diagnostics/trade_inverse_outcome_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/trade_inverse_outcome_summary_YYYYMMDD_HHMMSS.csv`

Interpretation:

- `inverse_pnl_before_incremental_costs` is the simple negative of actual realized P&L. It does not prove an executable inverse strategy because borrow, spread, slippage, fill timing, and order availability can differ.
- A small losing sample should not trigger automatic reversal. The report explicitly flags small samples with `do_not_auto_reverse_small_sample`.
- Low lineage quality means broker fills were matched through diagnostic fallback and should be reviewed before drawing strong conclusions.
