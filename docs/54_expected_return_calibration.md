# Expected Return Calibration

This diagnostic checks whether `expected_trade_return` is a real calibrated return or a score-scale value that should not be used for execution.

It is read-only for model scoring. The safety helper is conservative: rows with missing, infinite, extreme, ambiguous, raw-score-like, transformed-score-like, or forward-leakage-like expected returns are marked `expected_return_uncalibrated` and are not executable until calibrated bucket evidence is available.

Outputs:

- `data/model_outputs/diagnostics/expected_return_calibration_YYYYMMDD_HHMMSS.csv`
- `data/model_outputs/diagnostics/expected_return_calibration_summary_YYYYMMDD_HHMMSS.md`

Run:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_expected_return_calibration.py
```

Interpretation:

- `usable`: value is on ordinary return scale and inside +/-20%.
- `calibrated`: row has historical bucket evidence and the diagnostic uses that bucket return.
- `uncalibrated`: unit/source is ambiguous and must not be used for execution.
- `invalid`: missing, infinite, forward-leakage-like, raw-score-like, transformed-score-like, or extreme.

This does not loosen risk gates, model scoring, exposure, or live-trading safeguards.
