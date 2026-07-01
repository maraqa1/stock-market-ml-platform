# Execution Rank and Short-Side Policy

This layer separates research ranking from execution ranking.

The model and candidate pool may still show high-volatility or non-executable names near the top of the raw rank. The execution rank is assigned only after core tradability gates have already passed, including actionable direction, calibration, price, market cap, volatility, liquidity, risk, session, overnight, anti-churn, and position-intent eligibility where those fields are present in the candidate or plan output.

Short-side validation is conservative by default. Short candidates remain visible for research diagnostics, but they are marked `research_only` with `short_side_validation_required` unless short-side execution is explicitly enabled in configuration after attribution evidence supports it.

The generated artifact is separate from the broker submission path:

`data/portal_outputs/execution_ranked_candidates_YYYYMMDD_HHMMSS.csv`

It preserves:

- `raw_rank`
- `model_rank`
- `research_rank`
- `execution_rank`
- block reasons and calibration metadata

This is paper-trading-only diagnostic output. It does not change model scoring, gates, exposure, position limits, or live-trading behavior.
