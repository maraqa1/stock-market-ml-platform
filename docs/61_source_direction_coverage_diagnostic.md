# Source Direction Coverage Diagnostic

This read-only diagnostic explains why candidate rows do or do not carry a source-approved trading direction.

The report is intended for the case where the candidate pool has many planner-derived rows with `source_trade_action = No Decision`. Those rows remain non-executable. The diagnostic does not loosen gates, submit orders, change model scoring, or enable live trading.

## Outputs

- `data/trading/diagnostics/source_direction_coverage_detail_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/source_direction_coverage_summary_YYYYMMDD_HHMMSS.md`

## Detail Fields

- `symbol`
- `rank`
- `source_trade_action`
- `planner_derived_direction`
- `model_score`
- `rank_overall`
- `directional_strength`
- `confidence_score`
- `risk_adjusted_score`
- `meta_label_probability`
- `ticker_direction_bias`
- `ticker_direction_sample_count`
- `expected_return_scope`
- `validated_expected_return_bps`
- `risk_tier`
- `volatility_tier`
- `liquidity_tier`
- `primary_block_reason`
- `execution_domain`
- `source_no_decision_reason`
- `long_near_miss`

## No Decision Reasons

The diagnostic assigns one of these causes:

- `missing_model_score`
- `weak_directional_strength`
- `weak_confidence`
- `meta_label_missing`
- `meta_label_rejected`
- `insufficient_direction_memory`
- `direction_memory_conflict`
- `risk_gate_failed`
- `source_threshold_too_strict`
- `source_signal_not_available`
- `planner_only_without_source_authority`
- `unknown`

## Usage

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_source_direction_coverage_diagnostic.py
```

The summary reports source-approved Long/Short counts, No Decision counts, reason distribution, Long near-misses, and whether the evidence points to conservative thresholds or missing model evidence.
