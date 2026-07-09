# Long Source Approval Expansion

This layer is a conservative diagnostic for planner-derived Long rows whose source direction is still `No Decision`.

It does not loosen risk gates, does not enable shorts, does not enable inverse execution, does not increase exposure, and does not enable live trading.

## Default Mode

The default is:

```yaml
source_approval_expansion:
  enabled: false
  mode: diagnostic_only
```

In this mode, the layer only adds diagnostic fields:

- `source_expansion_candidate`
- `source_expansion_decision`
- `source_expansion_reason`
- `would_upgrade_to_source_long`

It does not change `execution_domain`, `final_execution_side`, or order eligibility.

## Conservative Criteria

A row can only be flagged as a would-upgrade Long candidate when all of these are true:

- The row is planner-derived Long.
- The row is not source-approved already.
- Ticker direction memory has enough samples.
- Ticker direction bias is `trust_long`.
- Validated expected return is positive.
- Expected-return scope is ticker, bucket, or side.
- Risk tier is not reject.
- Volatility tier is not extreme.
- Source No Decision reason is allowlisted, such as `source_threshold_too_strict` or `weak_confidence`.
- No hard blockers are present.

Hard blockers include:

- `direction_memory_conflict`
- `meta_label_rejected`
- `model_evidence_missing`
- `risk_gate_failed`
- `asset_not_overnight_tradable`
- `price_below_minimum`

## Enabled Mode

If explicitly enabled later, the layer may only move a qualified row to `watch_candidate`.

It must not create an `execution_candidate` directly.

## Diagnostic Output

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_source_approval_expansion_diagnostic.py
```

Outputs:

- `data/trading/diagnostics/source_approval_expansion_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/source_approval_expansion_YYYYMMDD_HHMMSS.md`
