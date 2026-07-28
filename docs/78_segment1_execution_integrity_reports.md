# Segment 1 Execution Integrity Reports

Segment 1 showed that candidate selection ran, but Alpaca paper submission did not
produce fills. These reports separate execution integrity from strategy edge.

## Submission Path

The scheduled auto-trader delegates to the configured `paper_autopilot` execution
owner. The legacy paper-trader path is not used when `execution_owner` is
`paper_autopilot`.

## Manifest Metrics

Forward-paper manifests include:

- `executable_candidate_count`
- `submitted_order_count`
- `filled_order_count`
- `submitted_to_executable_ratio`
- `filled_to_submitted_ratio`
- `executable_not_submitted_count`
- `executable_not_submitted_reasons`

## Session Consistency

Execution-ranked candidates include:

- `active_session_mode`
- `regular_session_eligible`
- `overnight_24_5_eligible`
- `tradable_session_set`
- `session_reject_reason`

An asset that is regular-session tradable but not overnight tradable is not marked
executable during `overnight_24_5`.

## Measurement Reports

Read-only scripts:

- `scripts/run_source_direction_coverage_report.py`
- `scripts/run_gate_funnel_report.py`
- `scripts/run_counterfactual_status_report.py`

These reports do not loosen gates, enable shorts, enable live trading, or change
model scoring.
