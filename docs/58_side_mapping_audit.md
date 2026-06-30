# Side Mapping Audit

This read-only diagnostic audits direction mapping from model and candidate actions into broker order sides.

It checks for high-severity problems such as:

- Long mapped to sell
- Short mapped to buy
- No Decision mapped to an order
- Close action mapped as a new open
- Directional intraday action conflicting with nightly trade action

Output:

- `data/model_outputs/diagnostics/side_mapping_audit_YYYYMMDD_HHMMSS.csv`

Important fields:

- `trade_action`: normalized model/candidate direction.
- `directional_action`: normalized intraday/same-day direction when present.
- `broker_side`: broker side from the order plan or results.
- `audit_flag`: `ok` or the mapping issue detected.
- `severity`: `info`, `medium`, `high`, or `warning`.

Limitations:

- Uses the latest order plan or order results artifact.
- If there is no order artifact, it reports `missing_data`.
- It does not change side mapping, model scoring, gates, or trading behavior.
