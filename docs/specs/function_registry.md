# StockML Function Registry

This registry maps platform functions to the modules, scripts, migrations, configs, tests, and specs that own them.

## Pipeline And Data Freshness

| Capability | Primary Code | Scripts | Config / Migrations | Tests / Specs |
| --- | --- | --- | --- | --- |
| Profile pipeline orchestration | `src/stockml/pipeline/profile_runner.py` | `scripts/run_profile_pipeline.py` | `config/pipeline_profiles.yaml` | pipeline profile tests; PIPE specs |
| Pipeline doctor | `src/stockml/pipeline/doctor.py` | `scripts/run_pipeline_doctor.py` | none | `tests/test_pipeline_doctor.py` |
| Trading-day readiness | `scripts/run_trading_day_readiness.py`, report checks | `scripts/run_trading_day_readiness.py` | quality thresholds | `tests/test_trading_day_readiness_script.py` |
| Universe filtering | `src/stockml/universe/` | universe build scripts | none | `tests/test_universe_filters.py` |
| Daily price store and deltas | `src/stockml/prices/`, market data providers | profile pipeline | raw store files | price/pipeline tests |
| Sentiment store and deltas | `src/stockml/sentiment/` | `scripts/run_sentiment_pipeline.py` | provider env vars | `tests/test_sentiment_schema.py` |

## Modeling And Candidate Generation

| Capability | Primary Code | Artifacts | Tests / Specs |
| --- | --- | --- | --- |
| Feature panel | `src/stockml/features/` | `data/processed/05_us_feature_panel_*` | SPEC 04 |
| Gold dataset | `src/stockml/gold/` | `data/gold/06_us_gold_ml_dataset_*` | SPEC 03 |
| Model outputs | `src/stockml/models/` | `data/model_outputs/*` | model tests/docs |
| Per-symbol forecast | `src/stockml/trading/per_symbol_forecast/` | `data/trading/per_symbol_forecast/*` | SPEC 24 |
| Candidate pool | `src/stockml/trading/order_planner.py` | `08_alpaca_paper_candidate_pool_*` | `tests/test_alpaca_order_planner.py` |
| Order plan | `src/stockml/trading/order_planner.py`, `src/stockml/trading/order_builder.py` | `08_alpaca_paper_order_plan_*` | `tests/test_alpaca_order_planner.py` |

## Intraday And Same-Day

| Capability | Primary Code | Config / Migrations | Tests / Specs |
| --- | --- | --- | --- |
| Intraday provider and market calendar | `src/stockml/intraday/provider.py`, `src/stockml/marketdata/providers/` | `config/intraday.yaml` | SPEC 33B |
| Intraday decision worker | `src/stockml/intraday/worker.py` | `migrations/005_intraday_decisions_up.sql` | SPEC 33C |
| Shadow would-trades | `src/stockml/intraday/shadow.py` | `migrations/006_*`, `007_*` | SPEC 34A/B |
| Intraday candidate refresh | `src/stockml/intraday/refresh.py` | `migrations/010_intraday_candidate_snapshots_up.sql` | SPEC 45 |
| Intraday promotion scoring | `src/stockml/intraday/promotion.py`, `promotion_score.py` | `migrations/011_intraday_promotion_log_up.sql` | SPEC 46 |
| Same-day edge validation | `src/stockml/same_day/labels.py`, `training.py` | reports under `reports/same_day_edge/` | SPEC 72 |
| Intraday history downloader | `src/stockml/intraday/history.py` | raw intraday store | SPEC 72 support |
| Same-day feature panel | `src/stockml/same_day/features.py`, `universe.py`, `feature_worker.py` | `migrations/017_intraday_features_up.sql` | SPEC 74 |
| Same-day gates | `src/stockml/same_day/gates.py` | `config/same_day.yaml` | SPEC 76 |
| Stream arbitration | `src/stockml/arbitration/arbitrator.py`, `conflicts.py` | `migrations/018_arbitration_conflicts_up.sql` | SPEC 76 |
| Same-day scoring | `src/stockml/same_day/scoring.py`, `score_worker.py` | `migrations/019_same_day_scoring_up.sql` | SPEC 77 |
| Same-day operator view and missed opportunities | `portal/services/same_day_view.py`, `src/stockml/same_day/missed_ops.py`, `scripts/generate_missed_ops_report.py` | `migrations/020_same_day_missed_opportunities_up.sql` | SPEC 78 |
| Same-day sizing and stream attribution | `src/stockml/trading/position_sizing.py`, `src/stockml/trading/order_planner.py`, `src/stockml/reports/daily.py` | `config/same_day.yaml` | SPEC 79 |

## Risk, Safety, And EOD

| Capability | Primary Code | Config / Migrations | Tests / Specs |
| --- | --- | --- | --- |
| Kill-switch core | `src/stockml/intraday/kill_switch.py` | `config/kill_switches.yaml`, `migrations/004_kill_switch_events_up.sql` | SPEC 35A |
| Trade quality gate | `src/stockml/trading/trade_quality_gate.py` | trading config | SPEC 14 |
| EOD flatten state machine | `src/stockml/autopilot/eod.py` | `config/eod.yaml`, `migrations/009_eod_flatten_up.sql` | SPEC 44, SPEC 75 |
| Position stream policy | `src/stockml/trading/snapshot_schema.py`, `snapshot_writer.py`, `order_builder.py` | `migrations/016_strategy_stream_positions_up.sql` | SPEC 73 |
| Same-day overnight safeguards | `src/stockml/autopilot/eod.py`, `src/stockml/intraday/kill_switch.py` | kill-switch events | SPEC 75 |

## Paper Trading And Autopilot

| Capability | Primary Code | Artifacts / Migrations | Tests / Specs |
| --- | --- | --- | --- |
| Paper trader basket | `src/stockml/trading/paper_trader.py` | portal output CSVs | SPEC 08, 11 |
| Paper autopilot state machine | `src/stockml/trading/paper_autopilot.py` | `data/portal_outputs/paper_autopilot_state.json` | paper autopilot specs |
| Auto-open guardrails | `src/stockml/autopilot/open.py` | `migrations/013_autopilot_open_log_up.sql` | SPEC 48A |
| Rotation recommendations | `src/stockml/autopilot/rotate.py` | `migrations/012_rotation_recommendation_log_up.sql` | SPEC 47 |
| Autopilot policy guard | `src/stockml/autopilot/policy.py` | kill-switch config | paper-only safety tests |
| Daily report | `src/stockml/reports/daily.py` | `migrations/014_daily_report_runs_up.sql` | SPEC 49 |

## Portal And Operator Views

| Capability | Primary Code | Tests / Specs |
| --- | --- | --- |
| Trading console | portal routes/templates/services | portal tests, SPEC 11 |
| Pipeline freshness | portal freshness services/templates | pipeline freshness tests |
| Intraday zone | portal intraday templates/services | SPEC 34C, 35B |
| Symbol detail and trace | symbol detail services, `src/stockml/trading/mover_trace.py` | mover trace tests |

## Upcoming Same-Day Functions

These are planned by SPEC 77-80 and intentionally not implemented yet.

| Spec | Planned Function | Expected Location |
| --- | --- | --- |
| 77 | Same-day production training, scoring, candidates, signal log | `src/stockml/same_day/training.py`, `scoring.py`, `score_worker.py`; `migrations/019_same_day_scoring_up.sql` |
| 78 | Same-day operator UI and missed-opportunity report | `portal/services/same_day_view.py`; `src/stockml/same_day/missed_ops.py`; `scripts/generate_missed_ops_report.py` |
| 79 | Stream attribution and same-day sizing | `src/stockml/trading/position_sizing.py`; `src/stockml/trading/order_planner.py`; `src/stockml/reports/daily.py`; `config/same_day.yaml` |
| 80 | Same-day Autopilot promotion contract | `src/stockml/autopilot/same_day_promotion.py`, `same_day_auto.py`; promotion UI; migration `same_day_promotion_evaluations` |
