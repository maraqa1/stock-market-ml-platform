# Calibration Coverage Debug

This diagnostic explains why live candidates cannot pass `expected_return_uncalibrated`.

It is read-only. It does not change model scoring, trading gates, exposure, or live-trading behavior.

## Command

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/debug_validation_bucket_calibration.py
```

## Outputs

- `data/model_outputs/diagnostics/calibration_coverage_debug_YYYYMMDD_HHMMSS.csv`
- `data/model_outputs/diagnostics/calibration_coverage_debug_summary_YYYYMMDD_HHMMSS.md`

## What It Checks

The diagnostic inspects validation inputs, forward-return label coverage, validation bucket construction, and live candidate-to-bucket mapping.

It reports:

- Whether walk-forward predictions, validation files, signal tables, model status files, realised forward labels, and latest calibration files exist.
- Forward-return coverage for `forward_1d_return`, `forward_5d_return`, `forward_20d_return`, `alpha_vs_spy`, `alpha_vs_sector`, and `realised_forward_return_bps`.
- Bucket sample counts, hit rates, expected return bps, quality, and insufficient-data reason.
- Candidate mapping failures such as missing model version, side not found, rank bucket not found, or insufficient calibration quality.

## Expected Root Causes

- `missing_forward_return_labels`: validation files exist, but no usable forward-return labels are present.
- `insufficient_bucket_sample_count`: labels exist, but the side/rank buckets do not have enough samples.
- `model_version_not_found`: live candidates were produced by a model version that does not match the calibration file.
- `side_not_found`: calibration exists for one side only.
- `rank_bucket_not_found`: candidate rank percentile cannot be matched to a bucket.

## Safety Rule

Raw `expected_trade_return` is never used as a fallback. Candidates remain blocked until validation-derived bucket calibration is available and usable.
