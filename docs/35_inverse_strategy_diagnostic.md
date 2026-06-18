# Inverse Strategy Diagnostic

This diagnostic pack evaluates whether recent paper losses indicate a real directional inversion problem or a lifecycle/execution issue.

It is read-only and does not change execution, scoring, gates, exposure, or live trading settings.

## Reports

- `inverse_strategy_diagnostic_YYYYMMDD_HHMMSS.csv`: compares original side/action to the inverse action using available fill or mark prices.
- `ranking_polarity_diagnostic_YYYYMMDD_HHMMSS.csv`: compares top-ranked long/bottom-ranked short logic against inverse and one-sided alternatives.
- `side_mapping_audit_YYYYMMDD_HHMMSS.csv`: flags suspicious action-to-broker-side mappings.
- `inverse_strategy_summary_YYYYMMDD_HHMMSS.md`: summarizes whether inverse appears better and whether the evidence is meaningful.

## Interpretation

A profitable inverse on a tiny sample is not enough to flip strategy direction. The recommended path is to collect enough completed trades and forward outcomes, then compare directional edge after spread and slippage costs. If inverse wins over a statistically meaningful sample and side-mapping audit flags are clean, investigate model polarity and label construction before any production strategy change.
