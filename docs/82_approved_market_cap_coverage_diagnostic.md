# Approved Market Cap Coverage Diagnostic

This read-only diagnostic investigates source-approved candidate rows where
`market_cap` is missing in the candidate pool.

It answers whether the missing value is likely caused by:

- provider metadata coverage (`provider_uncovered_market_cap`)
- metadata fetch/join gap (`metadata_fetch_or_join_gap`)
- candidate metadata join failure (`candidate_metadata_join_failure`)
- candidate Gold join failure (`candidate_gold_join_failure`)
- stale or unvalidated candidates (`validated_universe_exclusion_or_stale_candidate`)

The diagnostic does not change gates, config, ranking, sizing, or broker
submission.

Run:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_approved_market_cap_coverage.py
```

Outputs:

- `data/trading/diagnostics/approved_market_cap_coverage_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/approved_market_cap_coverage_YYYYMMDD_HHMMSS.md`

Important interpretation:

- `provider_uncovered_market_cap` means the metadata row exists, but its
  `market_cap` is empty.
- `metadata_fetch_or_join_gap` means the symbol survives validation but is absent
  from metadata.
- `candidate_metadata_join_failure` means metadata has a market cap, but the
  candidate pool lost it.
- `candidate_gold_join_failure` means Gold has a market cap, but the candidate
  pool lost it.

Rows remain blocked by the existing hard floor. This report only separates
"real size exclusion" from "data coverage/join defect."
