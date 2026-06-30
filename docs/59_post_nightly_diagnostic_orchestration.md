# Post-Nightly Diagnostic Orchestration

The post-nightly diagnostics runner waits for the full nightly pipeline to complete, then runs read-only diagnostics.

The default chain now includes:

- trade ledger
- profitability attribution
- strategy diagnostics
- intraday promotion replay
- broker fill reconciliation
- candidate-to-trade attribution
- missed better candidates
- position management outcomes
- ranking polarity
- side mapping audit

Each step can be skipped with its corresponding `--skip-*` flag. The runner does not submit orders, change gates, modify model scoring, or alter exposure.

Useful command:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_post_nightly_diagnostics.py --skip-wait
```

If the pipeline is not healthy, `--skip-wait` fails before diagnostics run.
