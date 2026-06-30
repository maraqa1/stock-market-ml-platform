# Unified Position Management Decision Engine

This diagnostic layer produces one read-only position-management recommendation per open paper position.

It does not submit orders, change thresholds, alter candidate selection, change sizing, or enable live trading. The output is intended to make the platform's position-management evidence visible before any execution wiring is considered.

## Outputs

- `data/trading/diagnostics/position_management_decisions_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/position_management_decisions_YYYYMMDD_HHMMSS.md`

Every row sets:

- `would_submit_order=false`
- `execution_allowed=false`
- `diagnostics_only=true`

## Recommended Actions

- `hold`: no position size change.
- `reduce`: partial de-risking; target quantity remains above zero when possible.
- `increase`: add to an existing position, diagnostic only.
- `close`: full exit, diagnostic only.
- `replace`: another candidate may be preferable, diagnostic only.
- `manual_review`: evidence is missing, conflicting, or unsafe.

## Precedence

The engine applies strict precedence:

1. Data integrity and pending-action guards.
2. Hard risk exits.
3. Confirmed model reversal.
4. Profit protection and giveback.
5. Edge deterioration.
6. Basket and sector risk.
7. Increase opportunity.
8. Replace recommendation.
9. Default hold.

## Inputs

The runner uses the latest available repository artifacts:

- open broker paper positions
- holding review
- order tracking / open orders
- latest order plan

Missing evidence is not fabricated. Missing or ambiguous core position data produces `manual_review`.

## Execution

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_position_management_decisions.py
```

This command is read-only except for writing diagnostic artifacts.

