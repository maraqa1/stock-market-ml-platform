# Position Management Outcomes Diagnostic

This read-only diagnostic summarizes trade outcomes by position-management reason and action family.

It is designed to answer: which exit or holding-management rules are associated with winners, losers, flat trades, and open unrealized outcomes?

Output:

- `data/trading/diagnostics/position_management_outcomes_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/position_management_outcomes_summary_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/position_management_outcomes_summary_YYYYMMDD_HHMMSS.md`

Important fields:

- `exit_reason`: reason carried by the lifecycle ledger or reconstructed closed-trade attribution.
- `management_action_family`: normalized family such as `risk_exit`, `profit_exit`, `profit_protection`, `signal_exit`, `manual_exit`, `open_position`, or `unknown`.
- `outcome_bucket`: winner, loser, flat, open_winner, open_loser, or open_flat.
- `lineage_quality` and `lineage_warnings`: attribution reliability context.

Limitations:

- The report depends on the latest trade ledger. If no ledger exists, it falls back to reconstructed closed-trade attribution when available.
- It reports `insufficient_data` instead of estimating missing prices or reasons.
- It does not change stop-loss, take-profit, rotation, anti-churn, or exposure logic.
