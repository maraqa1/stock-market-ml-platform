# Direction Conflict Precedence

`direction_memory_conflict` is surfaced ahead of softer reduced/risk labels after
hard floor blockers and short-side validation. This is a display/diagnostic
precedence rule; it must not change whether a row is executable.

The diagnostic now reports:

- total memory conflicts
- rows where `primary_block_reason = direction_memory_conflict`
- min/median/max `ticker_direction_confidence` for those rows
- sample rows with symbol, rank, status, and confidence

This is intentionally conservative. Ticker direction memory has historically been
weak in parts of the pool, so conflict confidence near 0.5 should be reviewed as a
policy question before it is treated as a hard strategy signal.

Materiality rule: if this precedence changes only `primary_block_reason`, it is
non-material. If it changes `executable`, `execution_domain`,
`final_execution_side`, order size, or execution rank, it is material and must not
be hot-patched into a frozen segment.
