# Order Readiness Proof

Execution-ranked candidates now carry explicit order-readiness evidence:

- `order_ready`
- `order_ready_reason`
- `order_eligible`
- `approved_notional`
- `suggested_quantity`
- `notional`
- `limit_price`
- `current_price`

`order_ready=true` is only assigned when the source candidate row already has a
submittable order plan:

- `order_eligible=true`
- positive `approved_notional` or `notional`
- positive `suggested_quantity`
- positive price proof from `limit_price`, `current_price`, `close`,
  `decision_price`, or `last_price`

Rows with missing order evidence are blocked with an `order_not_ready_*` reason.
Readiness is not defaulted and is not inferred as a fallback.

## Ticket 8 Preservation Check

Using production candidate pool
`data/portal_outputs/08_alpaca_paper_candidate_pool_20260730_092553.csv` under
`regular_session`, the hardened ranker preserves the pending Ticket 8 candidates:

| Symbol | Execution Domain | Status | Execution Rank | Order Ready | Approved Notional | Suggested Quantity |
|---|---|---|---:|---|---:|---:|
| GCT | execution_candidate | executable | 1 | true | 250.0 | 6 |
| ATRC | execution_candidate | executable | 2 | true | 250.0 | 6 |

## Materiality

Five latest candidate pools were rerun old-vs-new under `regular_session`.
Compared fields were `executable`, `final_execution_side`, `execution_domain`,
`status`, `primary_block_reason`, and `execution_rank`.

Result: `NON_MATERIAL`.

No row changed executability, execution domain, final execution side, or rank.
Segment 1 remains unchanged.
