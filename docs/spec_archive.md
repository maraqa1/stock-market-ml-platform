# StockML Spec Archive

Canonical spec records now live in `docs/specs/`.

- `docs/specs/spec_ledger.md` is the active implementation ledger.
- `docs/specs/function_registry.md` maps platform functions to code, migrations, configs, tests, and specs.
- This file remains as the legacy archive for older reconstructed notes.

This file is the durable ledger for specs that have been implemented or queued in
the paper-trading platform. Some older entries are reconstructed from commit
history, tests, migrations, and operator-session evidence; those entries are
marked as reconstructed.

## How To Use This Archive

- Add one row for every spec patch before or during implementation.
- Link each spec to the commit, tests, migrations, and rollout command.
- Mark incomplete or reconstructed details explicitly.
- Keep live-trading status explicit: this codebase remains paper-only.

## Status Legend

- `implemented`: code merged and tests added.
- `reconstructed`: implemented, but spec text was reconstructed after the fact.
- `planned`: spec exists but has not been implemented.
- `partial`: some supporting code exists, but the spec is not complete.

## Core Historical Specs

| Spec | Title | Status | Evidence |
| --- | --- | --- | --- |
| 03 | Gold dataset definition | reconstructed | `docs/03_gold_dataset_definition.md`, gold dataset modules/tests |
| 04 | Feature engineering | reconstructed | `docs/04_feature_engineering.md`, `src/stockml/features/*`, feature tests |
| 05 | Target engineering | reconstructed | `docs/05_target_engineering.md`, `src/stockml/gold/target_engineering.py` |
| 08 | Alpaca paper trading | reconstructed | `docs/08_alpaca_paper_trading.md`, `src/stockml/trading/*`, Alpaca tests |
| 11 | Paper trading lifecycle | reconstructed | `docs/11_paper_trading_lifecycle.md`, portal trading zones, lifecycle tests |
| 12 | Risk controls | reconstructed | `docs/12_risk_controls.md`, risk/trade-quality modules |
| 14 | Trade quality gate | reconstructed | `docs/14_trade_quality_gate.md`, `src/stockml/trading/trade_quality_gate.py` |
| 17 | Meta-labeling trade filter | reconstructed | `docs/17_meta_labeling_trade_filter.md`, `config/meta_labeling.yaml`, model tests |

## Intraday And Safety Specs

| Spec | Title | Status | Commit / Evidence | Notes |
| --- | --- | --- | --- | --- |
| 33A | Intraday confirmation gate core | implemented | `15bbd9a Add intraday confirmation gate core`; `tests/test_intraday_gates.py` | Added fixed `BlockReason`, pure feature/gate logic, `paper_only_guard.py`. |
| 35A | Intraday kill-switch core | implemented | `2827742 Add intraday kill switch core`; `migrations/004_kill_switch_events_up.sql`; `tests/test_kill_switch.py` | Added versioned kill-switch config and event table. |
| 35B | Kill-switch portal zone | implemented | `0b81430 Add intraday kill switch portal`; `portal/templates/intraday/index.html` | Surfaced kill-switch state and resume controls in the portal. |
| 33B | Intraday provider and scope skeleton | implemented | `1835811 Add intraday provider scope skeleton`; `927d426 Parse Alpaca calendar market hours correctly`; `config/intraday.yaml`; `tests/test_intraday_provider_scope.py` | Reuses Alpaca paper client for data access; provider hook keeps aggregator replaceable. Calendar times are normalized from market-local New York time to UTC before market-open checks. |
| 33C | Guarded intraday decision tick | implemented | `e98fb75 Add guarded intraday decision tick`; `migrations/005_intraday_decisions_up.sql`; `tests/test_intraday_worker.py` | Worker writes decision rows only after kill-switch gate. No order submission. |
| 34A | Shadow would-trades | implemented | `3e9bc5a Add intraday shadow would trades`; `migrations/006_shadow_would_trades_up.sql`; `tests/test_intraday_shadow.py` | Logs would-trades from allow decisions, still shadow-only. |
| 34B | Shadow outcome evaluation | implemented | `ba4625a Add intraday shadow outcome evaluation`; `migrations/007_shadow_outcomes_up.sql` | Adds 20-day outcome evaluation and idempotency tests. |
| 34C | Intraday shadow operator view | implemented | `747cff6 Add intraday shadow operator view`; `tests/test_intraday_portal_service.py` | Adds `/intraday` zones for flow, track record, and readiness. |
| 36 | Promotion safety contract and live-disabled guarantee | implemented | `728f0cf Add intraday promotion safety contract`; `2d16fbc Handle missing promotion tables gracefully`; `migrations/008_promotion_evaluations_up.sql` | Promotion is read-only. Live trading remains disabled in code. |

## Trading Console Correctness Specs

| Spec | Title | Status | Commit / Evidence | Notes |
| --- | --- | --- | --- | --- |
| 37 | Rotation candidate excludes held positions | implemented | `b88b3ac Exclude held positions from rotations`; `tests/monitor/test_rotation_candidates.py` | Prevents Replace recommendations whose replacement is already held. |
| 38 | In-flight close state per row | partial | `5b26074 Clear stale close-order banners from broker tracking`; positions zone changes | Banner correctness improved. Full DB-backed close state-machine remains a follow-up if position events are promoted to the portal row model. |
| 39 | Age timestamp diagnosis and rendering | partial | Action queue/open positions still use age values from monitor output | Needs a dedicated age filter and reconciliation diagnostic if we continue 37-43. |
| 40 | Collapse Recommendation and Operator Call | partial | `496481e Show operator calls in action queue` | Operator call is visible, but the duplicate-column cleanup is not fully complete. |
| 41 | Rotation noise suppression | partial | `config/monitor.yaml`, `b88b3ac` | Minimum rank/score thresholds exist; suppression log and diagnostics view are not complete. |
| 42 | Banner-vs-row precedence | partial | `5b26074`, portal positions banner updates | Needs unified zone macro work if we continue 37-43. |
| 43 | Filter input scoping and labels | planned | Prompted but not yet implemented | URL-persisted zone filters remain future work. |

## Candidate Evaluation And Action Queue Specs

| Spec | Title | Status | Commit / Evidence | Notes |
| --- | --- | --- | --- | --- |
| CE-1 | Feed candidate evaluations into action queue | implemented | `3a029bb Feed candidate evaluations into action queue`; `scripts/run_candidate_evaluation.py`; `src/stockml/agents/candidate_evaluation_engine.py` | Evaluates nightly candidates with current quotes and exposes open-candidate rows. |
| CE-2 | Fix candidate evaluation script imports | implemented | `2f89b41 Fix candidate evaluation script imports` | Makes the script runnable from VM paths. |
| CE-3 | Gate candidate queue entries by spread | implemented | `968219e Gate candidate queue entries by spread` | Avoids surfacing candidates with obviously poor spread quality. |

## Paper Autopilot Specs

| Spec | Title | Status | Commit / Evidence | Notes |
| --- | --- | --- | --- | --- |
| PA-1 | Paper Autopilot tick logging | implemented | `23e7716 Log paper autopilot ticks` | State persisted to `data/portal_outputs/paper_autopilot_state.json`. |
| PA-2 | Switchable autopilot modes | implemented | `636c772 Add switchable paper autopilot modes`; `62594d4 Autosave autopilot mode selection` | Modes: Observe, Paper Assist, Paper Autopilot, AI Gated Paper. |
| PA-3 | Monitor close authority | implemented | `0d90372 Allow paper autopilot to close monitored exits` | Paper Autopilot can submit paper closes for monitor close recommendations. |
| PA-4 | Defensive stale loser exits | implemented | `7e613dc Let paper autopilot defend stale losers` | Paper Autopilot can close stale losers within guard rails. |
| PA-5 | Trader-style exits | implemented | `9f0a172 Add trader-style paper autopilot exits`; `f347104 Fix paper autopilot decision applier signature` | Adds hard stop and trailing-profit protection. |
| PA-6 | Capability table | implemented | `f5588ac Show autopilot capability table` | Portal explains mode capabilities and limits. |
| PA-7 | Block regular basket submission while Paper Autopilot runs | implemented | `src/stockml/trading/autopilot_guard.py`; `tests/test_position_event_wiring.py` | Regular paper trader basket submission is refused when `paper_autopilot_state.json` has `mode=paper_autopilot` and `status=running`. Tracking-only remains allowed. |
| PA-8 | Paper Autopilot replacement close authority | implemented | `src/stockml/trading/paper_autopilot.py`; `tests/test_paper_autopilot.py` | When `autopilot.rotate_enabled` is true, Paper Autopilot can submit a paper close for monitor `replace`/`rotate` recommendations. Replacement opens remain gated by the auto-open path. |

## SPEC 44-51 Automation Completion Pack

| Spec | Title | Status | Commit / Evidence | Notes |
| --- | --- | --- | --- | --- |
| 44 | End-of-day flatten policy | implemented | `527dc10 Add paper autopilot EOD flatten policy`; `migrations/009_eod_flatten_up.sql`; `tests/test_eod_flatten.py` | Paper Autopilot runs EOD review/trim/flatten windows and surfaces EOD banner. |
| 45 | Intraday candidate refresh loop | implemented | `927d426 Parse Alpaca calendar market hours correctly`; `migrations/010_intraday_candidate_snapshots_up.sql`; `tests/test_intraday_candidate_refresh.py`; `tests/test_intraday_provider_scope.py` | Observe-only 5-minute snapshots for all daily candidates. No order submission. Market-hours gating depends on provider calendar normalization from US Eastern market-local times to UTC. |
| 46 | Intraday promotion scoring | implemented | `migrations/011_intraday_promotion_log_up.sql`; `tests/test_intraday_promotion_scoring.py` | Observe-only scoring from candidate snapshots to promotion verdicts, surfaced in Trading Console. |
| 47 | Auto-rotate recommendation engine | implemented | `migrations/012_rotation_recommendation_log_up.sql`; `tests/test_rotation_recommendations.py` | Paper Assist rotation recommendations requiring operator confirmation; no automatic background apply. |
| 48A | Guarded paper auto-open | implemented | `migrations/013_autopilot_open_log_up.sql`; `tests/test_autopilot_auto_open.py` | Paper Autopilot can submit paper-only opens from strong intraday promotions only when `autopilot.open_enabled` is true, kill-switches allow, slots/caps permit, and EOD is inactive. Auto-rotate remains deferred. |
| 48B | Autopilot rotation | planned | Prompted, not implemented | Automatic rotation remains behind a stricter promotion contract and manual config flag. |
| 49 | Daily trading report | implemented | `migrations/014_daily_report_runs_up.sql`; `tests/test_daily_reports.py` | Read-only daily after-action report with account state, activity, autopilot actions, candidate flow, missed opportunities, rule triggers, recommendations, and CSV/JSON exports. |
| 50 | Configurable autopilot rules | planned | Prompted, not implemented | Moves hardcoded autopilot rules to versioned config and diagnostics view. |
| 51 | Unified decision audit log | planned | Prompted, not implemented | Database view to query all decision sources in one place. |

## Rollout Pattern

For each implemented spec, the VM rollout normally follows this pattern:

```bash
cd /home/massa/stock-market-ml-platform
git pull origin main

PYTHONPATH=src /opt/jupyter-env/bin/python3 -m pytest <focused-tests> -q

# Apply any new migration for that spec.
PYTHONPATH=src /opt/jupyter-env/bin/python3 - <<'PY'
from pathlib import Path
from sqlalchemy import text
from stockml.db.connection import get_engine

migration = Path("<migration-file>.sql")
engine = get_engine(required=True)
with engine.begin() as conn:
    for statement in migration.read_text().split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(text(statement))
print("migration_applied", migration)
PY

sudo systemctl restart stockml-portal
```

## Paper-Only Safety Notes

- Live trading remains disabled.
- Autopilot actions use paper-only order paths.
- New open/rotate authority is not implemented yet; it is planned for SPEC 48
  and must remain gated by explicit promotion criteria and config flags.
- Intraday and candidate refresh cadence has a 5-minute floor.

## Market Timing Notes

- All worker comparisons use UTC-aware datetimes internally.
- US equity regular-session calendar values from broker APIs may arrive as
  market-local strings such as `09:30` and `16:00`, not full UTC timestamps.
- Provider adapters must normalize those market-local calendar values using
  `America/New_York` for the selected session date before returning
  `open_at` and `close_at`.
- The SPEC 45 refresh failure on 2026-05-12 was caused by treating Alpaca
  calendar values as closed/invalid during the regular session. Regression
  coverage lives in `tests/test_intraday_provider_scope.py`.
- A provider migration, for example Alpaca to Massive/Polygon, must preserve
  this contract: provider calendar in, UTC-aware `MarketCalendar` out.

## Maintenance Rule

Every new spec patch should update this file in the same commit with:

1. Spec number and title.
2. Status.
3. Commit or migration evidence.
4. Focused test command.
5. VM rollout command if needed.
