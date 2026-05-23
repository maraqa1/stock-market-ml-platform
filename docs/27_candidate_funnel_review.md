# Candidate Funnel Review

This review describes how a ticker moves from the raw data set into the paper-trading candidate pool and where high-gainer symptoms can appear.

## Flow

1. **Raw universe**
   - Output: `data/raw/01_us_equity_universe_*.csv`
   - Built from exchange symbol lists.
   - A ticker missing here is a universe/provider symbol issue.

2. **Cleaned and tradable universe**
   - Outputs:
     - `data/interim/02_us_universe_cleaned_*.csv`
     - `data/interim/02_us_tradable_universe_*.csv`
   - Filters to common-stock-like tradable names.
   - A ticker missing here was removed by universe cleaning or exchange filtering.

3. **Canonical price store**
   - Output: `data/raw/03_us_price_history_store.csv`
   - First run lands full history. Nightly runs download deltas and merge into this canonical file.
   - A ticker missing here cannot enter validation, features, Gold, model, or candidates.

4. **Price validation**
   - Output: `data/interim/03_us_price_validated_universe_*.csv`
   - Requires sufficient history, price, and liquidity.
   - A high-gainer may fail here if it has too little history, low price, low dollar volume, or no current provider rows.

5. **Metadata enrichment**
   - Output: `data/interim/04_us_metadata_enriched_*.csv`
   - Missing metadata is not supposed to block feature generation. Features fall back to `Unknown` sector/industry and conservative defaults.
   - Audit reports should therefore show missing metadata as context, not as the main blocking stage after price validation.

6. **Feature panel**
   - Output: `data/processed/05_us_feature_panel_*.csv`
   - Uses the canonical price store plus latest validated universe and metadata.
   - If a ticker was newly price-repaired, features must be rebuilt before it appears downstream.

7. **Gold dataset**
   - Output: `data/gold/06_us_gold_ml_dataset_*.csv`
   - Adds ranking targets and model-ready fields.
   - A ticker in validated universe but missing Gold usually means downstream artifacts are stale or feature generation did not include it.

8. **Model outputs**
   - Outputs:
     - `data/model_outputs/model_predictions_latest.csv`
     - `data/model_outputs/advanced_model_signal_table_*.csv`
   - Produces `rank_overall`, `trade_action`, `signal`, `directional_action`, and meta-label fields.
   - A high rank with `trade_action=No Decision` means the model sees the ticker but has not promoted it to the strict decision band. Directional fields may still show research-side bias.

9. **Candidate pool**
   - Output: `data/portal_outputs/08_alpaca_paper_candidate_pool_*.csv`
   - Selects from model signals using rank/directional windows, long/short balance, meta-label gate, and trade-quality gate.
   - A ticker can be visible in model outputs but omitted from the candidate pool if it is outside the configured candidate size or directional window.

10. **Order plan**
    - Output: `data/portal_outputs/08_alpaca_paper_order_plan_*.csv`
    - Final basket selection applies trade quality, sizing, notional, shorting, and sector concentration constraints.
    - A ticker in candidate pool but not order plan was outcompeted or blocked by final portfolio/risk selection.

## Issues Found

1. **Old generated artifacts filled storage**
   - Gold, feature, and model CSVs accumulated indefinitely.
   - Fix: daily `stockml-artifact-cleanup.timer` and `scripts/cleanup_pipeline_artifacts.py`.

2. **Some top gainers had no provider price history**
   - Symptoms: `has_price=False`, `missing_provider_price_history`.
   - Fix: `download_price_history --symbols ... --force-full` targeted repair and explicit failure rows for provider silence.

3. **Missing metadata was over-reported as a blocking reason**
   - Feature generation can now continue without metadata, so the audit should not stop at `metadata`.
   - Fix: symbol coverage audit now reports the next true blocking stage, usually `gold` if downstream artifacts are stale.

4. **Downstream artifacts can be stale after price repair**
   - Price repair alone is not enough. Validated universe, feature panel, Gold, model, and order plan must be rebuilt.
   - Symptom: `has_price=True`, `in_validated_universe=True`, `has_gold_rows=False`.

5. **Model and candidate selection are not the same as today's gainer list**
   - The nightly model uses prior daily data. Same-day gainers require intraday promotion, but auto-open now requires model evidence to avoid weak unanchored opens.
   - A high-gainer can be missed because it was not in last night's model universe, not because the intraday move was unseen.

6. **Strict action bands can hide useful directional evidence**
   - `trade_action=Long/Short` is narrow and only assigned to top/bottom decision slots.
   - `directional_action` is broader and should be reviewed for research shortlist behavior.

## Repeatable Diagnosis

Build a full funnel:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_candidate_funnel_report.py --provider eodhd
```

Build a focused high-gainer funnel:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_candidate_funnel_report.py \
  --provider eodhd \
  --symbols BB SPCE DELL SST IMAX HPQ FLO RDW UP VSH LION YSG QBTS UTI DAO EL
```

The report writes:

- `data/interim/00_symbol_coverage_audit_*.csv`
- `data/interim/00_candidate_funnel_summary_*.csv`
- `data/interim/00_candidate_funnel_artifacts_*.csv`

Use the summary to separate data coverage failures from model/risk-selection failures.
