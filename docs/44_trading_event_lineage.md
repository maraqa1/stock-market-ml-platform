# Trading Event Lineage

StockML paper-trading diagnostics carry a stable lifecycle identity chain in activity-event details and journal CSV rows.

The additive lineage fields are:

- `pipeline_run_id`
- `cycle_id`
- `signal_id`
- `candidate_id`
- `event_key`
- `client_order_id`
- `broker_order_id`
- `position_id`
- `trade_id`
- `exit_decision_id`
- `order_intent`
- `strategy_mode`
- `session_mode`
- `candidate_source`
- `model_version`
- `lineage_warning`

The intended chain is:

`pipeline_run_id -> cycle_id -> signal_id -> candidate_id -> client_order_id -> broker_order_id -> position_id -> trade_id -> exit_decision_id`

Identifiers are deterministic when sufficient evidence exists. When evidence is missing, the field remains empty/null and `lineage_warning` records the missing evidence instead of inventing an identifier.

## Position And Trade Identity

Opening broker fills define the durable position and trade identity:

- `position_id = position-<opening_broker_order_id>`
- `trade_id = trade-<opening_broker_order_id>`

Candidate scan and selection rows are not positions. They keep `candidate_id` and related model context, but they do not use `paper:<symbol>` as a trade or position identity.

Submitted opening orders with broker evidence can derive `position_id` and `trade_id` from the broker order ID. Filled rows must keep the same IDs. Monitor and exit events try to link back to the latest known opening fill for the symbol; if no opening-fill evidence exists, they keep the warning instead of fabricating a trade.

## Exit Lineage

Exit recommendations and close actions carry `exit_decision_id` when there is enough evidence to link the close to a known `position_id` and `trade_id`. Close rows without opening-fill evidence report:

- `missing_trade_id`
- `missing_exit_decision_id`

This preserves the audit trail and makes broken chains visible in diagnosis reports.

## Session Mode Normalization

Exported session values are normalized to one of:

- `regular_session`
- `pre_market`
- `after_hours`
- `overnight_24_5`
- `weekend_closed`

Legacy values such as `regular`, `overnight`, `24x5`, and `24/5` are mapped to canonical values and marked with `inconsistent_session_mode` so old data remains traceable.

## Diagnostics

`scripts/diagnose_activity_lineage.py` reports coverage by event type plus broken-chain counters:

- `selected_without_submit_link`
- `submitted_without_fill_link`
- `fill_without_position`
- `fill_without_trade_id`
- `monitor_without_trade_id`
- `exit_without_trade_id`
- `close_without_exit_decision`
- `pnl_without_trade_id`

This change is metadata-only. It does not change model scoring, gates, risk controls, execution policy, order sizing, or live-trading safeguards.
