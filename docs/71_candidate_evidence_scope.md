# Candidate Evidence Scope Diagnostics

The execution-ranked candidate pool may include evidence that is calibrated at different levels. A side-level expected return is not the same thing as a ticker-specific forecast.

This diagnostic labels that scope explicitly and splits the candidate pool into execution, research, and blocked outputs.

## Scope Fields

- `expected_return_scope`
- `hit_rate_scope`
- `profit_factor_scope`

Possible values:

- `ticker`
- `bucket`
- `side`
- `global`
- `unknown`

If every buy candidate has the same expected return and every sell candidate has a different repeated expected return, the expected return is labelled `side`.

## Ticker Direction Memory Fields

- `ticker_direction_memory_status`
- `ticker_direction_sample_count`
- `inverse_warning_status`
- `inverse_warning_actionable`

If `ticker_direction_sample_count` is below the configured minimum, inverse warnings are not actionable. This prevents a one-sample inversion observation from driving execution.

## Outputs

Diagnostics:

- `data/trading/diagnostics/candidate_evidence_scope_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/candidate_evidence_scope_YYYYMMDD_HHMMSS.md`

Split candidate pools:

- `data/trading/exports/research_candidate_pool_YYYYMMDD_HHMMSS.csv`
- `data/trading/exports/execution_candidate_pool_YYYYMMDD_HHMMSS.csv`
- `data/trading/exports/blocked_candidate_pool_YYYYMMDD_HHMMSS.csv`

## Command

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_candidate_evidence_scope.py
```

## Safety

This is a diagnostic and export layer. It does not change execution behaviour, loosen gates, flip direction, enable live trading, or increase exposure.
