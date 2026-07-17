# Forward Paper Program Spec

Purpose: make paper trading evidence count as a stable forward test rather than a moving-target experiment.

Current state:
- Paper trading infrastructure exists.
- Alpaca paper integration, lifecycle IDs, order tracking, broker fill reconciliation, closed-trade attribution, and diagnostics exist.
- The institutional question is not whether paper execution is possible; it is whether a clean, stable, reviewable forward-paper window exists.

Clean run definition:
- A clean run is a continuous calendar window where:
  - code version is recorded
  - strategy config hash is recorded
  - model artifact hash is recorded
  - candidate pool lineage is recorded
  - all submitted orders, fills, cancellations, positions, exits, and P&L are logged
  - no manual overrides occur unless explicitly labeled
  - live trading remains disabled

Minimum evidence window:
- Exploratory: 20 trading days.
- Serious internal review: 60 trading days.
- Institutional diligence starting point: 90+ trading days with stable strategy configuration.

Required daily artifacts:
- `pipeline_manifest`
- `gold_dataset_id`
- `model_version`
- `model_artifact_hash`
- `candidate_pool_path`
- `execution_ranked_candidate_path`
- `order_plan_path`
- `order_results_path`
- `order_tracking_path`
- `positions_path`
- `activity_journal_export_path`
- `trade_ledger_path`
- `profitability_attribution_path`
- `broker_fill_reconciliation_path`
- `strategy_config_hash`
- `gate_config_hash`

Required strategy freeze metadata:
- `strategy_version`
- `code_commit`
- `config_hash`
- `model_hash`
- `gate_policy_hash`
- `paper_program_start_date`
- `paper_program_status`
- `material_change_flag`
- `material_change_reason`

Material changes that reset or segment the evidence clock:
- Model scoring changes.
- Gate loosening or new override mode.
- Exposure/order-size increases.
- New asset class or session mode.
- Short-side enablement.
- Calibration methodology changes.
- Position management rule changes.
- Any manual trading intervention not reproducible by the autopilot.

Allowed non-resetting changes:
- Read-only diagnostics.
- UI display improvements.
- Bug fixes that do not change decisions, with a documented before/after impact check.
- Logging additions.

Suggested new outputs:
- `data/trading/forward_paper/forward_paper_manifest_YYYYMMDD.csv`
- `data/trading/forward_paper/forward_paper_daily_summary_YYYYMMDD.csv`
- `data/trading/forward_paper/forward_paper_config_changes_YYYYMMDD.csv`
- `data/trading/forward_paper/forward_paper_program_status.md`

Suggested new modules:
- `src/stockml/trading/forward_paper_manifest.py`
- `src/stockml/trading/config_fingerprint.py`
- `src/stockml/diagnostics/forward_paper_review.py`
- `scripts/run_forward_paper_manifest.py`
- `scripts/run_forward_paper_review.py`
- `tests/test_forward_paper_manifest.py`
- `tests/test_config_fingerprint.py`

Daily review metrics:
- orders planned/submitted/filled/canceled
- gross exposure
- net exposure
- realized P&L
- unrealized P&L
- slippage bps
- fill rate
- rejection reasons
- gate pass/fail counts
- long vs short P&L
- session-mode P&L
- volatility-opportunity P&L
- position-management action P&L
- candidate source P&L

Program status labels:
- `not_started`
- `running_clean`
- `running_with_warnings`
- `segmented_by_material_change`
- `not_fit_for_review`

Acceptance tests:
- A daily manifest is created even when no trades occur.
- Config hash changes are detected.
- Material changes segment the forward-paper period.
- Read-only diagnostics do not reset the period.
- Live trading flag must remain false.
- Every filled order links to lifecycle IDs or receives a lineage warning.

Maturity target:
- Move forward paper from `infrastructure ready` to `running_clean` once a stable strategy window begins.
- Evidence becomes reviewable only after enough uninterrupted days accumulate.
