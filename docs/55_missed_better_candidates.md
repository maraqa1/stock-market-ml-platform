# Missed Better Candidates Diagnostic

This read-only diagnostic compares the latest eligible candidate pool against broker positions and the latest trade ledger.

It answers: did the system have stronger approved or reduced candidates that were not currently held or traded?

The report does not submit orders, change gates, change scoring, or recommend automatic exposure increases. Rows marked `review_candidate` are investigation prompts only.

Output:

- `data/trading/diagnostics/missed_better_candidates_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/missed_better_candidates_summary_YYYYMMDD_HHMMSS.md`

Important fields:

- `baseline_symbol`: held or traded symbol used as the comparison baseline.
- `candidate_symbol`: eligible non-held/non-traded candidate.
- `edge_gap_bps`: candidate edge minus baseline score, in basis-point-like diagnostic units.
- `why_not_traded`: diagnostic reason; this is not an order instruction.
- `diagnostic_decision`: `review_candidate`, `no_action`, or `insufficient_data`.

Limitations:

- It uses available candidate and ledger artifacts. If lineage is incomplete, it reports `insufficient_data` instead of fabricating links.
- It does not override anti-churn, session, liquidity, model, meta-label, or risk gates.
