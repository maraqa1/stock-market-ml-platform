# Strategy Diagnosis Before Changes

This diagnostics pack is a read-only layer for identifying where paper-trading losses are coming from before changing strategy behavior.

Rules:

- Do not change trading thresholds from these reports alone.
- Do not loosen gates.
- Do not modify model scoring.
- Do not increase exposure.
- Do not enable live trading.

Run:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_strategy_diagnostics.py
```

Outputs:

- `data/model_outputs/diagnostics_score_bucket_edge_YYYYMMDD_HHMMSS.csv`
- `data/model_outputs/diagnostics_long_short_edge_YYYYMMDD_HHMMSS.csv`
- `data/model_outputs/diagnostics_meta_label_impact_YYYYMMDD_HHMMSS.csv`
- `data/model_outputs/diagnostics_intraday_promotion_ablation_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics_execution_attribution_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics_position_management_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics_fallback_attribution_YYYYMMDD_HHMMSS.csv`
- `data/model_outputs/diagnostics_summary_YYYYMMDD_HHMMSS.md`

Interpretation:

- Score bucket edge should show whether higher model-score/rank buckets have stronger realized edge than lower buckets.
- Long vs Short edge should show whether long and short signals both have standalone edge.
- Meta-label impact should show whether meta-label filtering improves precision and net gain.
- Intraday promotion ablation should show whether intraday-adjusted/promotion fields improve outcomes versus nightly-only rank.
- Execution attribution should separate regular and extended-hours fill quality, resting orders, and fill status.
- Position management attribution should classify close/monitor events by exit reason; P&L impact requires linked close/fill records.
- Fallback attribution should identify whether main model, near-miss, per-symbol forecast, plan fallback, or flat-account fallback creates losses.

Missing data:

If required inputs are missing, each report writes a `missing_data` row rather than fabricating results.

