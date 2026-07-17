# PIT Data Foundation Plan

Purpose: make the research and trading datasets defensible against survivorship bias, stale metadata, and non-point-in-time fundamentals.

Current state:
- Modules exist for universe, metadata, providers, prices, Gold, and Gold v2.
- Delisting awareness exists in tests and some data paths.
- A full point-in-time, survivorship-safe historical universe and fundamentals reconstruction is not yet proven.

Institutional gap:
- A reviewer cannot yet prove that the model only saw symbols and metadata that were available as of each historical decision date.
- Current metadata/fundamentals can still behave like latest-known data unless every join is explicitly as-of dated.

Required architecture:

1. Point-in-time universe table
- Input: historical exchange symbol files, delisting events, symbol changes, corporate-action metadata.
- Output: `data/gold/pit_universe_membership_YYYYMMDD.csv`
- Required columns:
  - `as_of_date`
  - `ticker`
  - `exchange`
  - `listed_at`
  - `delisted_at`
  - `active_on_as_of_date`
  - `security_type`
  - `country`
  - `source`
  - `source_loaded_at`
  - `lineage_id`

2. Point-in-time metadata snapshots
- Input: provider metadata/fundamentals snapshots with provider timestamps.
- Output: `data/gold/pit_metadata_snapshot_YYYYMMDD.csv`
- Required columns:
  - `as_of_date`
  - `ticker`
  - `company`
  - `sector`
  - `industry`
  - `market_cap`
  - `shares_outstanding`
  - `float_shares`
  - `metadata_effective_at`
  - `metadata_loaded_at`
  - `provider`
  - `lineage_id`

3. As-of joins into Gold v2
- Gold v2 must join universe and metadata using `as_of_date`, never latest metadata.
- Historical rows must exclude symbols that were not active on that date unless explicitly retained for delisting outcome analysis.
- Latest candidate rows may use latest metadata, but must label the scope as `latest_snapshot`, not `historical_pit`.

4. Data quality gates
- Fail Gold v2 validation if:
  - historical rows use metadata with `metadata_effective_at > as_of_date`
  - active universe rows have missing listing state
  - delisted symbols disappear before their delisting date
  - sector/industry is latest-only without a PIT label
  - duplicate `ticker/as_of_date` rows exist

5. Lineage
- Every generated row should carry:
  - `pipeline_run_id`
  - `source_file`
  - `provider`
  - `as_of_date`
  - `lineage_id`
  - `pit_validation_status`

Candidate implementation modules:
- `src/stockml/universe/`
- `src/stockml/metadata/`
- `src/stockml/marketdata/providers/`
- `src/stockml/gold/enhanced_gold_v2.py`
- `src/stockml/gold/gold_quality_checks.py`
- `src/stockml/reports/symbol_coverage_audit.py`

Suggested new modules:
- `src/stockml/universe/pit_universe.py`
- `src/stockml/metadata/pit_metadata.py`
- `src/stockml/gold/pit_joins.py`
- `scripts/build_pit_data_foundation.py`
- `tests/test_pit_universe.py`
- `tests/test_pit_metadata.py`
- `tests/test_gold_pit_joins.py`

Acceptance tests:
- Delisted ticker remains present before `delisted_at` and absent after it.
- Metadata effective after the row date is rejected.
- Gold v2 historical rows use only metadata available on or before the row date.
- Latest snapshot metadata is clearly labeled and not used as historical PIT evidence.
- Rebuilding the same date range is deterministic.

Maturity target:
- Move PIT foundation from `partial / medium-low` to `implemented / medium`.
- Institutional-grade status still requires provider audit files and a documented delisted-symbol source.
