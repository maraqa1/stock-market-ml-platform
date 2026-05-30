# StockML Spec Ledger

This ledger records the specifications used to build the StockML paper-trading platform. Older specs are reconstructed from existing docs, tests, migrations, and commit history. The same-day momentum pack is recorded directly from the operator-provided SPEC 72-80 prompt.

Live trading status: **permanently disabled**. Same-day work is paper-only.

Companion rebuild documents:

- `docs/specs/platform_blueprint.md`
- `docs/specs/function_registry.md`
- `docs/specs/spec_pack_same_day_72_80.md`

## Historical Core Specs

| Spec | Title | Status | Evidence |
| --- | --- | --- | --- |
| 03 | Gold dataset definition | reconstructed | `docs/03_gold_dataset_definition.md`, gold dataset modules/tests |
| 04 | Feature engineering | reconstructed | `docs/04_feature_engineering.md`, `src/stockml/features/` |
| 05 | Target engineering | reconstructed | `docs/05_target_engineering.md`, `src/stockml/gold/target_engineering.py` |
| 08 | Alpaca paper trading | reconstructed | `docs/08_alpaca_paper_trading.md`, `src/stockml/trading/` |
| 11 | Paper trading lifecycle | reconstructed | `docs/11_paper_trading_lifecycle.md`, portal trading zones/tests |
| 12 | Risk controls | reconstructed | `docs/12_risk_controls.md`, risk and quality-gate modules |
| 14 | Trade quality gate | reconstructed | `docs/14_trade_quality_gate.md`, `src/stockml/trading/trade_quality_gate.py` |
| 17 | Meta-labeling trade filter | reconstructed | `docs/17_meta_labeling_trade_filter.md`, `config/meta_labeling.yaml` |
| 22 | Near-miss analysis | reconstructed | `docs/22_near_miss_analysis.md`, near-miss tests/modules |
| 23 | Position health and basket risk | reconstructed | `docs/23_position_health_and_basket_risk.md` |
| 24 | Per-symbol forecast layer | reconstructed | `docs/24_per_symbol_forecast_layer.md`, `src/stockml/trading/per_symbol_forecast/` |
| 25 | Trading strategy loop | reconstructed | `docs/25_trading_strategy_loop.md` |

## Intraday, Safety, And Automation Specs

| Spec | Title | Status | Evidence |
| --- | --- | --- | --- |
| 33A | Intraday confirmation gate core | implemented | `tests/test_intraday_gates.py`, `src/stockml/intraday/gates.py` |
| 33B | Intraday provider and scope skeleton | implemented | `config/intraday.yaml`, `tests/test_intraday_provider_scope.py` |
| 33C | Guarded intraday decision tick | implemented | `migrations/005_intraday_decisions_up.sql`, `tests/test_intraday_worker.py` |
| 34A | Shadow would-trades | implemented | `migrations/006_shadow_would_trades_up.sql`, `tests/test_intraday_shadow.py` |
| 34B | Shadow outcome evaluation | implemented | `migrations/007_shadow_outcomes_up.sql` |
| 34C | Intraday shadow operator view | implemented | portal intraday services/routes/tests |
| 35A | Intraday kill-switch core | implemented | `migrations/004_kill_switch_events_up.sql`, `tests/test_kill_switch.py` |
| 35B | Kill-switch portal zone | implemented | portal intraday kill-switch routes/tests |
| 36 | Promotion safety contract and live-disabled guarantee | implemented | `migrations/008_promotion_evaluations_up.sql` |
| 44 | End-of-day flatten policy | implemented | `migrations/009_eod_flatten_up.sql`, `tests/test_eod_flatten.py` |
| 45 | Intraday candidate refresh loop | implemented | `migrations/010_intraday_candidate_snapshots_up.sql`, `tests/test_intraday_candidate_refresh.py` |
| 46 | Intraday promotion scoring | implemented | `migrations/011_intraday_promotion_log_up.sql`, `tests/test_intraday_promotion_scoring.py` |
| 47 | Auto-rotate recommendation engine | implemented | `migrations/012_rotation_recommendation_log_up.sql`, `tests/test_rotation_recommendations.py` |
| 48A | Guarded paper auto-open | implemented | `migrations/013_autopilot_open_log_up.sql`, `tests/test_autopilot_auto_open.py` |
| 48B | Autopilot rotation | planned | Prompted historically, not implemented |
| 49 | Daily trading report | implemented | `migrations/014_daily_report_runs_up.sql`, `tests/test_daily_reports.py` |
| 50 | Configurable autopilot rules | planned | Not implemented |
| 51 | Unified decision audit log | planned | Not implemented |

## Pipeline Reliability Specs

| Spec | Title | Status | Commits / Evidence | Notes |
| --- | --- | --- | --- | --- |
| PIPE-1 | Pipeline doctor readiness gate | implemented | `f49a3c4`, `4a34698`, `18287c0`; `scripts/run_pipeline_doctor.py` | Detects stale/running/missing pipeline artifacts. |
| PIPE-2 | Tradable universe exclusions | implemented | `c9f0653` | Tightens active/tradable universe filtering. |
| PIPE-3 | Sentiment fetch bounds and logging | implemented | `479469e`, `94a80b4`, `7d2be9a` | Adds canonical sentiment store and nightly deltas. |

## Same-Day Momentum Pack

Execution gate: SPEC 72 must produce a report, operator must read it, and operator must authorize continuation before SPEC 73.

| Spec | Title | Status | Commits | VM Verification |
| --- | --- | --- | --- | --- |
| 72 | Retrospective edge validation gate | implemented | `259d7f2`, `d502b56`, `78a8307`, `041eb78`, `114bc6a` | Report `reports/same_day_edge/20260529_204130.md`, verdict GREEN |
| 73 | Strategy stream column and per-position EOD policy | implemented | `def2713` | `64 passed` on VM |
| 74 | Intraday feature panel | implemented | `499debe` | `17 passed` on VM |
| 75 | Per-position EOD flatten extension | implemented | `83295c0` | `39 passed` on VM |
| 76 | Same-day gates and arbitration | implemented-pending-vm | `52c1cd2` | Awaiting VM result |
| 77 | Same-day model scoring and candidate generation | implemented-pending-vm | local commit pending | Requires VM verification |
| 78 | Same-day operator UI and missed-opportunity report | planned | Not started | Must follow SPEC 77 |
| 79 | Same-day stream attribution and position sizing | planned | Not started | Must follow SPEC 78 |
| 80 | Same-day Autopilot promotion contract | planned | Not started | Must follow SPEC 79 and Paper Assist data accumulation |

## Same-Day Pack Acceptance State

### SPEC 72

Core files:
- `src/stockml/same_day/labels.py`
- `src/stockml/same_day/training.py`
- `src/stockml/intraday/history.py`
- `scripts/measure_same_day_edge.py`
- `scripts/download_intraday_history.py`

Focused tests:
- `tests/same_day/test_labels.py`
- `tests/same_day/test_training_no_leakage.py`
- `tests/same_day/test_intraday_history.py`

### SPEC 73

Core files:
- `migrations/016_strategy_stream_positions_up.sql`
- `src/stockml/trading/snapshot_schema.py`
- `src/stockml/trading/snapshot_writer.py`
- `src/stockml/trading/order_builder.py`
- `src/stockml/autopilot/eod.py`

Focused tests:
- `tests/positions/test_strategy_stream.py`
- `tests/autopilot/test_eod_per_position.py`

### SPEC 74

Core files:
- `migrations/017_intraday_features_up.sql`
- `src/stockml/same_day/features.py`
- `src/stockml/same_day/universe.py`
- `src/stockml/same_day/feature_worker.py`

Focused tests:
- `tests/same_day/test_features.py`
- `tests/same_day/test_feature_no_lookahead.py`

### SPEC 75

Core files:
- `src/stockml/autopilot/eod.py`
- `src/stockml/intraday/kill_switch.py`
- `src/stockml/same_day/feature_worker.py`

Focused tests:
- `tests/autopilot/test_same_day_eod.py`
- `tests/autopilot/test_eod_per_position.py`
- `tests/test_eod_flatten.py`
- `tests/test_kill_switch.py`

### SPEC 76

Core files:
- `config/same_day.yaml`
- `migrations/018_arbitration_conflicts_up.sql`
- `src/stockml/same_day/gates.py`
- `src/stockml/arbitration/arbitrator.py`
- `src/stockml/arbitration/conflicts.py`
- `src/stockml/trading/outcome_reasons.py`
- `src/stockml/trading/reason_normalizer.py`

Focused tests:
- `tests/same_day/test_gates.py`
- `tests/arbitration/test_arbitrator.py`
- `tests/test_alpaca_order_planner.py`
- `tests/test_pipeline_event_schema.py`

### SPEC 77

Core files:
- `migrations/019_same_day_scoring_up.sql`
- `src/stockml/same_day/scoring.py`
- `src/stockml/same_day/score_worker.py`
- `src/stockml/same_day/training.py`

Focused tests:
- `tests/same_day/test_scoring.py`
- `tests/test_pipeline_event_schema.py`

## Required Update Before SPEC 77

Record the VM result for SPEC 76:

```bash
cd /home/massa/stock-market-ml-platform
git pull --ff-only origin dev

PYTHONPATH=src /opt/jupyter-env/bin/python3 -m pytest \
  tests/same_day/test_gates.py \
  tests/arbitration/test_arbitrator.py \
  tests/test_alpaca_order_planner.py \
  tests/test_pipeline_event_schema.py
```

If green, update SPEC 76 status from `implemented-pending-vm` to `implemented` and record the pass count.

## Required Update Before SPEC 78

Record the VM result for SPEC 77:

```bash
cd /home/massa/stock-market-ml-platform
git pull --ff-only origin dev

PYTHONPATH=src /opt/jupyter-env/bin/python3 -m pytest \
  tests/same_day/test_scoring.py \
  tests/same_day/test_gates.py \
  tests/arbitration/test_arbitrator.py \
  tests/test_pipeline_event_schema.py
```
