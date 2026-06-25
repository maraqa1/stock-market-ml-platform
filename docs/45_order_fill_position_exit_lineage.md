# Order Fill Position and Exit Lifecycle Lineage

This paper-trading lineage contract keeps model/candidate identity separate from broker execution identity until a fill proves that a position exists.

## Identity Chain

The intended chain is:

`pipeline_run_id -> cycle_id -> signal_id -> candidate_id -> client_order_id -> broker_order_id -> position_id -> trade_id -> exit_decision_id`

Candidate events may carry `scan_candidate_id` and `parent_candidate_id` when an internal scan event wraps an already-selected candidate. The original `candidate_id` remains the model/candidate identity used to join selected, scanned, gated, and submitted events.

## Submission Is Not a Position

Submitted or accepted orders may carry `client_order_id` and `broker_order_id`, but must not fabricate `position_id` or `trade_id`. Those fields are created only when a fill is confirmed.

Opening fills use:

- `position_id = position-<opening broker_order_id>`
- `trade_id = trade-<opening broker_order_id>`

If a monitor or exit event cannot resolve a broker position back to a proven opening fill, it must retain null lineage fields and emit `lineage_warning=ambiguous_symbol_position` rather than inventing a trade.

## Session Fields

The journal stores separate session fields:

- `event_session_mode`: derived from `event_at`
- `planned_execution_session_mode`: policy/session intended by the order plan
- `actual_submission_session_mode`: session at broker submission/fill time

This prevents regular-session events from being mislabeled as `overnight_24_5` just because extended-hours support is enabled.

## Validation Throttle

When `autopilot.validation_mode=true`, paper auto-open is capped by:

- `validation_max_new_orders_per_cycle`
- `validation_max_new_orders_per_day`
- `validation_max_open_positions_total`

These caps are restrictive safety controls and do not loosen any existing anti-churn, session, model, liquidity, or risk gate.
