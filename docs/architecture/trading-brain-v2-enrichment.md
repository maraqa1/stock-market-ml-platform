# Trading Brain V2 Enrichment Layer

Trading Brain V2 treats enrichment as a provider-neutral step between the raw execution-ranked candidate pool and V2 candidate intake.

## Contract

Input:

- Raw candidate CSV produced by the StockML pipeline.

Output:

- Enriched candidate shortlist CSV with a machine-readable decision/status column.
- Canonical artifact: `data/ai2/*_enriched_candidates_*.csv` or provider-specific shortlist naming.
- Audit events under `data/trading/audit/`.

Required status columns, in priority-compatible form:

- `Decision`
- `execution_decision`
- `ai2_status`
- `decision`
- `ai2_decision`

## Providers

The current provider is `ai2`. The adapter interface is generic so future providers such as Claude or ChatGPT can be added without changing Trading Brain V2 entry, risk, sizing, or position-management code.

Provider configuration is read from `trading_brain.ai2_enrichment` for backwards compatibility:

- `provider`: defaults to `ai2`
- `enabled`: fail-safe switch
- `endpoint_url`: optional HTTP endpoint
- `api_key_env`: environment variable containing the provider API key
- `output_dir`: canonical enriched artifact directory
- `fail_safe_on_error`: defaults to `true`

Endpoint lookup also supports:

- `ENRICHMENT_ENDPOINT`
- `<PROVIDER>_ENRICHMENT_ENDPOINT`
- `AI2_ENRICHMENT_ENDPOINT`

API keys are never written to output files or portal responses. They are read server-side from the configured environment variable, such as `AI2_API_KEY`, `CLAUDE_API_KEY`, or `CHATGPT_API_KEY`.

## Runtime Flow

1. `scripts/run_candidate_enrichment.py` receives a raw candidate CSV.
2. `AI2EnrichmentOrchestrator` validates that the raw file exists and is readable.
3. The configured provider adapter enriches the file.
4. The enriched CSV is validated for row count and status fields.
5. The enriched file is copied to a canonical output path.
6. The canonical file is passed into `AP-B01 Gold Dataset Intake`.
7. Audit events record received, started, completed, failed, and handed-off states.

## Safety

The enrichment step is read-only with respect to brokerage execution. If enrichment fails, returns an empty file, or omits required status fields, V2 fails safe and does not trade from that file. Live execution remains separately blocked by `v2_allow_live_execution=false`.
