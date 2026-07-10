# Same-Symbol Churn Lifecycle Guard

This patch addresses a paper-trading lifecycle failure where a valid model candidate can be opened, closed, and reopened repeatedly in the same session.

The guard is diagnostic and paper-only. It does not change model scoring, risk gates, exposure limits, short-side policy, or live-trading behavior.

## Protections

- `EOD_FLATTEN` is only allowed in the configured EOD flatten window.
- Reconstructed snapshot closes are no longer labeled as true `EOD_FLATTEN`.
- Fresh closes before `minimum_hold_minutes` are blocked unless the reason is an emergency/explicit allowed close reason.
- Same-symbol reopen is blocked after a close.
- Same-symbol daily opens are capped by `same_symbol_limits.max_opens_per_symbol_per_day`.

## Config

`config/eod.yaml`

- `eod_flatten.flatten_start_time`
- `eod_flatten.flatten_end_time`
- `eod_flatten.allow_intraday_flatten`

`config/autopilot.yaml`

- `anti_churn.cooldown_minutes_after_close`
- `anti_churn.block_same_symbol_reopen_same_day`
- `same_symbol_limits.max_opens_per_symbol_per_day`
- `same_symbol_limits.max_closes_per_symbol_per_day`
- `same_symbol_limits.max_reopens_per_symbol_per_day`

## Diagnostic

Run:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/diagnose_symbol_lifecycle.py \
  --symbol DFTX \
  --date 2026-07-10
```

Outputs:

- `data/trading/diagnostics/symbol_lifecycle_DFTX_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/symbol_lifecycle_DFTX_YYYYMMDD_HHMMSS.md`

The report includes open/close time, hold minutes, realized P&L, EOD-window validity, minimum-hold status, cooldown status, and churn detection.
