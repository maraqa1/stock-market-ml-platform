# Ranking Polarity Diagnostic

This read-only diagnostic checks whether the model ranking direction appears aligned with realized forward returns.

It compares strategy variants:

- current top-ranked long / bottom-ranked short
- inverse top-ranked short / bottom-ranked long
- long-only top ranked
- long-only bottom ranked
- short-only top ranked
- short-only bottom ranked

Output:

- `data/model_outputs/diagnostics/ranking_polarity_diagnostic_YYYYMMDD_HHMMSS.csv`

The report flags `polarity_bug_likely` when bottom-ranked rows outperform top-ranked rows in available forward outcomes.

Limitations:

- Requires historical signal rows and forward outcomes from the Gold layer.
- If forward outcomes are unavailable, it reports `missing_data` rather than inferring polarity.
- It does not change model scoring, ranking, thresholds, gates, or trading behavior.
