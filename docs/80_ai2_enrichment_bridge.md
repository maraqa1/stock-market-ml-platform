# AI2 Enrichment Bridge

The AI2 bridge automates the candidate enrichment path while preserving StockML as the single execution brain.

## Level 2: API bridge

Flow:

1. StockML reads the latest `execution_ranked_candidates_*.csv`.
2. StockML writes `data/ai2/ai2_candidate_input_*.csv`.
3. If `ai2_enrichment.api_enabled=true` and `endpoint_url` is configured, StockML posts the candidate rows to AI2.
4. AI2 returns CSV or JSON enrichment rows.
5. StockML normalizes the response and writes:
   - `data/ai2/ai2_candidate_response_*.csv`
   - `data/portal_outputs/ai2_enriched_execution_ranked_candidates_*.csv`
   - `data/ai2/ai2_enrichment_bridge_manifest_*.json`

The API key is read from the environment key named by `ai2_enrichment.api_key_env`.
The browser never receives the key.

## Level 3: guarded closed-loop hook

When `ai2_enrichment.auto_refresh_before_autopilot_tick=true`, paper autopilot runs the AI2 bridge before selecting auto-open candidates.

This does not loosen any StockML gates. AI2 can only confirm candidates that StockML already marked as:

- `execution_domain=execution_candidate`
- `executable=true`
- `order_eligible=true`
- `order_ready=true`
- valid `final_execution_side`

The single-brain consumer remains `execution_ranked_auto_open_candidates()`, which now reads the AI2-merged file when the bridge is enabled.

## Safe defaults

Default config keeps API automation off:

- `api_enabled=false`
- `endpoint_url=""`
- `auto_refresh_before_autopilot_tick=false`

Live trading and short selling are not enabled by this bridge.
