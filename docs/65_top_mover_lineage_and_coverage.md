# Top Mover Lineage and Gold/Model Coverage Diagnostic

This diagnostic traces externally observed market movers through the StockML / Marketcast pipeline.

It is read-only. It does not change model scoring, candidate selection, risk gates, short-side policy, exposure, order sizing, or live trading.

## Pipeline Stages

The lineage report checks each mover through:

```text
tradable universe
-> price history
-> validated universe
-> metadata
-> feature panel
-> Gold v2
-> model signal table
-> source_trade_action
-> candidate pool
-> execution domain
-> order plan
```

## CLI

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_top_mover_lineage_diagnostic.py \
  --date 2026-07-09 \
  --symbols LITE,SNDK,HPE,FDX,FLEX,NCLH,ON,FCX,MU,GLW,PARA,APA,COST,AXON,J,MCK,CINF,PEP,DVN,TKO
```

Optional input CSV:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_top_mover_lineage_diagnostic.py \
  --input-csv data/trading/diagnostics/external_movers_YYYYMMDD.csv
```

The input CSV may include:

- `symbol`
- `screenshot_direction`
- `screenshot_price`
- `mover_type`
- `source`
- `observed_at`

## Outputs

- `data/trading/diagnostics/top_mover_lineage_detail_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/top_mover_lineage_summary_YYYYMMDD_HHMMSS.md`
- `data/trading/diagnostics/gold_model_coverage_audit_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/gold_model_coverage_summary_YYYYMMDD_HHMMSS.md`

## Special Flags

- `strong_long_missed_by_source_action`: model signal exists, rank is strong, ticker memory is `trust_long`, but source action is `No Decision`.
- `long_mover_memory_aligned_but_no_decision`: external up mover has `trust_long` memory but remains `No Decision`.
- `ticker_mapping_status`: exact match, alias match, missing alias required, or not found.
- `price_sanity_status`: plausible, stale price, possible split/scale issue, or missing price reference.

## Safety Rules

- Do not recommend or enable live trading.
- Do not enable shorts.
- Do not bypass gates.
- Do not allow planner-derived rows to execute.
- Do not increase exposure.
- Same-day mover ingestion, if built later, must begin as watch/shadow only.
