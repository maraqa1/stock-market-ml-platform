# Net-Of-Cost Execution Ranking

Execution-ranked candidates now preserve raw research rank while assigning `execution_rank` by:

`net_expected_return_bps = validated_expected_return_bps - estimated_execution_cost_bps`

Cost inputs are read per row when available:
- `estimated_execution_cost_bps`
- `estimated_total_cost_bps`
- `spread_edge_cost_bps`
- `estimated_cost_bps`
- `cost_bps`
- otherwise `transaction_cost_bps + spread_bps + estimated_slippage_bps + borrow_cost_estimate_bps`

Only executable candidates receive `execution_rank`. Blocked, watch, and shadow rows keep their diagnostics but do not receive execution rank.

This change does not alter model scoring, gates, exposure, or live-trading safety.
