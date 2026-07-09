# Short Side Validation

This report validates short-side edge before any short execution is enabled.

It is diagnostic only. It does not enable shorts, loosen short-side gates, flip direction, enable live trading, or submit orders.

## Outputs

- `data/trading/diagnostics/short_side_validation_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/short_side_validation_YYYYMMDD_HHMMSS.md`

## Metrics

The report includes:

- short candidate count
- source-approved short count
- short win rate
- short average return
- short expected value after cost
- short profit factor
- short performance by sector
- short performance by volatility regime
- short performance by market regime
- borrow/cost sensitivity
- short squeeze risk flags
- minimum evidence needed before shorts are allowed

## Acceptance Before Short Execution

Shorts remain disabled unless all of these are proven:

- expected return is positive after cost
- profit factor is above 1.1
- sample size is adequate
- performance survives walk-forward split
- no severe short-squeeze risk exists

## Usage

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_short_side_validation.py
```
