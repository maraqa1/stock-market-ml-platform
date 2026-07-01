# Validation Bucket Expected Return Calibration

This layer calibrates live candidate expected return using historical out-of-sample validation outcomes. It does not change model scoring, thresholds, exposure, or live-trading behavior.

## Purpose

Candidate rows can contain untrusted `expected_trade_return` values when the field is a raw score, transformed score, ambiguous unit, or missing. Execution safety should rely on side-specific validation buckets instead of raw candidate fields.

## Inputs

- Walk-forward or validation prediction CSVs from `data/model_outputs`.
- Rows must contain a side, model score or rank, and a realised forward return.
- Latest unlabeled rows are excluded because they do not yet have forward outcomes.

## Outputs

- `data/model_outputs/validation/expected_return_bucket_calibration_YYYYMMDD_HHMMSS.csv`
- `data/model_outputs/validation/expected_return_bucket_calibration_latest.csv`
- `data/model_outputs/validation/expected_return_bucket_calibration_summary_YYYYMMDD_HHMMSS.md`

## Bucket Rules

The builder creates these diagnostic bucket families:

- `model_score_decile`
- `rank_percentile_decile`
- `side_specific_rank_decile`
- `sector_neutral_decile` when sector data is available

Only side-specific Long and Short buckets are executable-safe for live candidate mapping.

## Safety Rules

A candidate is executable only when:

- It maps to a side-specific calibration bucket.
- The bucket is `usable`.
- The bucket sample count is sufficient.
- The calibrated expected return comes from validation outcomes.

Weak buckets remain blocked unless explicitly allowed by config. If no usable calibration exists, candidates keep `expected_return_uncalibrated`.

## Limitations

This layer does not prove the strategy is profitable. It only prevents raw or ambiguous expected-return fields from being treated as execution evidence.
