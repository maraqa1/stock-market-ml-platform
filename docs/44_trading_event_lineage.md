# Trading Event Lineage

StockML paper-trading diagnostics now carry a stable lifecycle identity chain in activity-event details and journal CSV rows.

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

The intended chain is:

`pipeline_run_id -> cycle_id -> signal_id -> candidate_id -> client_order_id -> broker_order_id -> position_id -> trade_id -> exit_decision_id`

Identifiers are deterministic when sufficient evidence exists. When evidence is missing, the field remains empty/null and `lineage_warning` records the missing evidence instead of inventing an identifier.

This change is metadata-only. It does not change model scoring, gates, risk controls, execution policy, order sizing, or live-trading safeguards.
