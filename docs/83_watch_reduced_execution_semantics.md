# Watch and Reduced Execution Semantics

Candidate execution buckets are mutually exclusive:

- `execution_candidate`: source-authorized, gate-passing, `order_ready=true`,
  and safe for Paper Autopilot submission.
- `watch_candidate`: source-authorized or explicitly watch-authorized, visible
  for review/diagnostics, but not executable.
- `blocked_candidate`: source-authorized but failing a hard gate or missing
  order-readiness proof.
- `shadow_observation`: planner-derived or research-only row without source
  execution authority.

Reduced rows resolve to exactly one status:

- `execution_candidate` when all gates pass and `order_ready=true`.
- `watch_candidate` only when the row is intentionally watch-authorized but not
  executable.
- `blocked_candidate` when sizing/order proof is absent, a hard gate fails, or
  the row is not submittable.

`research_only` is not used for source-approved reduced rows with a concrete
decision. This keeps counterfactual reports from mixing watch candidates with
shadow observations.

Materiality check: this ticket is semantic/test coverage for existing behavior
after Ticket 1. It does not change model scoring, thresholds, exposure, live
trading, shorts, or candidate selection.
