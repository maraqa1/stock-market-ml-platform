# StockML Trading Platform Blueprint

This document is a portable rebuild blueprint for the StockML paper-trading platform. It complements `spec_ledger.md` and `function_registry.md`.

The platform is a paper-only ML trading system. Live trading is permanently disabled by policy.

## Core Contract

- Trading mode: Alpaca paper API only.
- Live order submission: disabled and must remain disabled.
- Primary market data: EODHD, with provider abstractions under `src/stockml/marketdata/`.
- Runtime target: Ubuntu VM at `/home/massa/stock-market-ml-platform`.
- Python runtime on VM: `/opt/jupyter-env/bin/python3`.
- Source import path: `PYTHONPATH=src`.
- Portal: Flask/Jinja operator console.
- Database: Postgres in production, SQLite-compatible schema patterns for tests.

## High-Level Data Flow

```text
Universe
  -> price history store and deltas
  -> price validation
  -> metadata enrichment
  -> feature panel
  -> sentiment panel
  -> gold ML dataset
  -> model outputs
  -> candidate pool
  -> order plan
  -> paper broker submission
  -> order tracking and positions
  -> monitor / EOD / reports
```

## Pipeline Flow

The nightly research pipeline is orchestrated by:

- `src/stockml/pipeline/profile_runner.py`
- `scripts/run_profile_pipeline.py`
- `config/pipeline_profiles.yaml`

The intended full-universe lifecycle:

1. Day zero builds a complete active/tradable universe and downloads historical prices from 2018 onward.
2. Day zero also builds the historical sentiment store.
3. Each later nightly run downloads deltas only:
   - price delta from last stored date through current date
   - sentiment delta for the missing recent window
4. The pipeline validates artifacts before trading readiness.
5. Trading-day readiness runs automatically after a successful profile pipeline.

The doctor/readiness layer is owned by:

- `src/stockml/pipeline/doctor.py`
- `scripts/run_pipeline_doctor.py`
- `scripts/run_trading_day_readiness.py`

## Universe Contract

The tradable universe must exclude symbols that are inactive, liquidated, non-common equity, unsupported, or otherwise unsuitable for paper trading.

Core modules:

- `src/stockml/universe/clean_us_equity_universe.py`
- `src/stockml/universe/build_tradable_universe.py`

Same-day universe adds stricter filters:

- active stock universe
- average dollar volume >= $20M/day
- prior close price between $5 and $500
- market cap >= $500M
- not halted
- not in news or halt blackout

Same-day universe code:

- `src/stockml/same_day/universe.py`

## Feature And Gold Dataset Flow

Daily model features are created by the feature engineering layer and then assembled into the gold ML dataset.

Important artifacts:

- validated universe: `data/interim/03_us_price_validated_universe_*.csv`
- metadata: `data/interim/04_us_metadata_enriched_*.csv`
- feature panel: `data/processed/05_us_feature_panel_*.csv`
- sentiment store: `data/processed/05_news_sentiment_store.csv`
- gold dataset: `data/gold/06_us_gold_ml_dataset_*.csv`

Core docs:

- `docs/03_gold_dataset_definition.md`
- `docs/04_feature_engineering.md`
- `docs/05_target_engineering.md`

## Model And Candidate Flow

Daily model outputs are written under `data/model_outputs/`.

Important artifacts:

- `model_predictions_latest.csv`
- `advanced_model_signal_table_*.csv`
- `advanced_model_top_long_signals_*.csv`
- `advanced_model_top_short_signals_*.csv`

Candidate and order planning are owned by:

- `src/stockml/trading/order_planner.py`
- `src/stockml/trading/order_builder.py`
- `src/stockml/trading/trade_quality_gate.py`

Canonical outputs:

- `data/portal_outputs/08_alpaca_paper_candidate_pool_*.csv`
- `data/portal_outputs/08_alpaca_paper_order_plan_*.csv`
- `data/portal_outputs/08_alpaca_paper_order_results_*.csv`

## Paper Trading Runtime

Paper trading is owned by:

- `src/stockml/trading/paper_trader.py`
- `src/stockml/trading/paper_autopilot.py`
- `src/stockml/trading/order_monitor.py`
- `src/stockml/trading/paper_portfolio.py`

Autopilot state is persisted at:

- `data/portal_outputs/paper_autopilot_state.json`

Operator modes:

- Observe: computes and logs only.
- Paper Assist: proposes actions, operator confirms.
- Paper Autopilot: acts within explicit paper-only guards.

## Safety System

Safety is layered. New trading functions must pass all applicable layers.

Core safety modules:

- `src/stockml/intraday/kill_switch.py`
- `src/stockml/trading/trade_quality_gate.py`
- `src/stockml/trading/risk_checks.py`
- `src/stockml/trading/submission_guards.py`
- `src/stockml/autopilot/policy.py`
- `src/stockml/autopilot/eod.py`

Key safety contracts:

- Kill-switches are checked before evaluation or action.
- Live trading paths are not introduced.
- Same-day stream starts in Observe mode.
- Same-day auto-execution is impossible until SPEC 80 promotion criteria are implemented and met.
- Same-day positions must flatten at EOD.
- Multi-day positions may hold overnight by design.

## Intraday Layer

Existing intraday layer:

- `src/stockml/intraday/provider.py`
- `src/stockml/intraday/worker.py`
- `src/stockml/intraday/refresh.py`
- `src/stockml/intraday/promotion.py`
- `src/stockml/intraday/shadow.py`

Cadence floor:

- Intraday and same-day workers must not run more frequently than every 5 minutes.

Market-hours contract:

- Provider calendar is normalized into UTC-aware datetimes.
- US market-local values are interpreted using `America/New_York`.

## Same-Day Momentum Stream

The same-day stream is separate from the multi-day forecast stream.

Implemented foundations:

- SPEC 72: validation gate
- SPEC 73: `strategy_stream`
- SPEC 74: intraday features
- SPEC 75: EOD flatten safeguards
- SPEC 76: gates and arbitration

Planned:

- SPEC 77: scoring and same-day candidate generation
- SPEC 78: Paper Assist UI and missed-opportunity report
- SPEC 79: sizing and attribution
- SPEC 80: promotion contract

Same-day stream source code:

- `src/stockml/same_day/`
- `src/stockml/arbitration/`

## Strategy Streams

Canonical stream values:

- `multi_day_forecast`
- `same_day_momentum`

Position policy:

- `multi_day_forecast`
  - `must_flatten_at_eod=False`
  - default max hold from EOD config
- `same_day_momentum`
  - `must_flatten_at_eod=True`
  - max hold is same session

Schema foundation:

- `migrations/016_strategy_stream_positions_up.sql`
- `src/stockml/trading/snapshot_schema.py`

## EOD Behavior

EOD state machine:

- `review`
- `trim`
- `observe`
- `flatten`
- `verify`
- `postclose`

Same-day:

- T-5 flatten selects all positions with `must_flatten_at_eod=True`.
- If same-day positions remain overnight, `OVERNIGHT_POSITIONS` is recorded and next session same-day ticks are blocked.

Multi-day:

- Not flattened by default.
- Can be trimmed if weak/stale.
- Multi-day-only overnight records do not block streams.

## Arbitration

Arbitration resolves conflicts between multi-day and same-day streams.

Rules:

- If symbol held by multi-day, same-day cannot open.
- If symbol held by same-day, same-day cannot open another position.
- If both streams agree, multi-day wins.
- If streams conflict, abstain and log conflict.
- If multi-day says No Decision and same-day has a signal, same-day emits.
- If only same-day has a signal, same-day emits.
- If only multi-day has a signal, multi-day behavior remains unchanged.

Core code:

- `src/stockml/arbitration/arbitrator.py`
- `src/stockml/arbitration/conflicts.py`
- `migrations/018_arbitration_conflicts_up.sql`

## Portal

The portal is the operator-facing system of record.

Important zones:

- pipeline freshness
- candidate pool
- order plan
- submitted orders
- open positions
- action queue
- autopilot state
- intraday diagnostics
- reports

Portal code lives under:

- `portal/`

## Systemd / VM Operation

Common VM commands:

```bash
cd /home/massa/stock-market-ml-platform
git pull --ff-only origin dev

PYTHONPATH=src /opt/jupyter-env/bin/python3 -m pytest <focused-tests>
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_pipeline_doctor.py
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_trading_day_readiness.py --skip-profile
```

Nightly service:

```bash
systemctl status stockml-full-nightly.timer --no-pager
systemctl status stockml-full-nightly.service --no-pager
journalctl -u stockml-full-nightly.service --since "today" --no-pager -n 200
```

## Rebuild Instructions For Another Agent

To rebuild the platform from specs:

1. Read `docs/specs/spec_ledger.md`.
2. Read `docs/specs/function_registry.md`.
3. Read this blueprint.
4. Apply migrations in order.
5. Build the pipeline first.
6. Build paper trading second.
7. Build safety and kill-switches before any automation.
8. Build intraday and same-day streams only after the pipeline is stable.
9. Preserve paper-only guarantees at every stage.
10. Run focused tests per spec before moving to the next spec.
