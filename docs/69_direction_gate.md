# Direction Gate

The Direction Gate is an additive execution-safety gate for paper trading. It
does not change model scoring, open a broker path, loosen risk rules, enable
live trading, or flip trades automatically.

## Purpose

The platform has separate direction fields:

- `source_trade_action`: original model/source decision.
- `trade_action`: planner or ranking-layer derived action.
- `directional_action`: contextual direction, when present.

Only `source_trade_action` is authoritative for execution. A row with
`source_trade_action = No Decision` remains research-only even if later fields
show `Long` or `Short`.

## Execution Rule

A candidate can be executable only when:

- `direction_decision = direction_pass`
- `direction_gate_pass = true`
- existing risk, liquidity, volatility, calibration, session, anti-churn, and
  position-intent gates also pass

The gate is added to execution ranking; it does not replace existing gates.

## Hard Blocks

- Missing `source_trade_action` -> manual review.
- `source_trade_action = No Decision` -> research-only.
- Planner-derived `Long` or `Short` without source approval -> research-only.
- `directional_action` alone -> research-only.
- Conflicting source/planner/directional actions -> manual review.
- Negative `validated_expected_return_bps` -> block.
- Poor `validated_profit_factor` -> block.
- Inverse warning -> inverse-watch, not automatic flip.
- Shorts remain research-only unless short-side validation explicitly passes.

Raw `expected_trade_return` is not used as direction evidence.

## Diagnostics

Run:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_direction_gate_diagnostic.py \
  --start 2026-06-01 \
  --end 2026-07-02
```

Outputs:

- `data/trading/diagnostics/direction_gate_diagnostic_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/direction_gate_diagnostic_YYYYMMDD_HHMMSS.md`

The report shows pass/block/research-only counts, No Decision counts, short
blocks, and top candidates before and after the direction gate.

## Current Policy

The default policy is conservative:

- long candidates can pass only with authoritative source Long and calibrated
  positive evidence
- short candidates are research-only by default
- inverse signals are diagnostics-only and do not automatically reverse trades
