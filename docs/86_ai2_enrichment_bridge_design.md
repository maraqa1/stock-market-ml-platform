# AI2 Enrichment Bridge Design

This branch introduces AI2 as an evidence layer for paper trading. It does not
make AI2 an execution owner.

## Design Rule

StockML `paper_autopilot` remains the single brain for paper order submission,
position management, rotation, anti-churn, lifecycle checks, and audit logging.
AI2 may confirm, caution, or block a StockML candidate, but it must not create a
trade that StockML did not already mark as executable.

## Data Flow

1. The daily StockML pipeline writes `execution_ranked_candidates_*.csv`.
2. AI2 enriches that candidate file with EODHD EOD/intraday context and returns
   a candidate decision file.
3. The AI2 bridge normalizes the returned fields and joins them back to the
   StockML execution-ranked candidate file by `symbol`.
4. The bridge writes a separate read-only artifact:
   `ai2_enriched_execution_ranked_candidates_*.csv`.
5. A later ticket may wire this artifact into `paper_autopilot`, but only as an
   additional confirmation gate.

## Current Branch Slice

Implemented now:

- AI2 schema normalization.
- AI2-to-StockML candidate merge.
- Separate output artifact writer.
- `STOCKML_DATA_ROOT` support so branch code can read the production pipeline
  data directory without copying artifacts into the worktree.
- AI2 candidate input export:
  `data/ai2/ai2_candidate_input_*.csv`.
- Trading portal status panel for candidate input, AI2 result, merged result,
  and AI2 proceed/allowed counts.
- Config with the bridge disabled by default.
- Tests proving AI2 cannot bypass StockML execution gates.

Not implemented yet:

- Market-hour AI2 polling.
- Portal upload/download controls.
- Autopilot consumption of AI2-confirmed candidates.
- Position-management replacement ranking using AI2 evidence.

## Safety Contract

- Live trading remains disabled.
- Shorts are not enabled by this bridge.
- AI2 cannot introduce new symbols into the executable set.
- AI2 cannot turn a blocked/research/watch row into an executable row.
- With `enabled: false`, the bridge is inert and writes diagnostics only.

## Branch Portal Runtime

For branch testing on the VM, run the portal with:

```bash
STOCKML_PROJECT_ROOT=/home/massa/stock-market-ml-platform-ai2-bridge
STOCKML_DATA_ROOT=/home/massa/stock-market-ml-platform/data
```

That keeps the branch code isolated while allowing the portal to read the live
pipeline, candidate, and trading artifacts from the production data directory.
