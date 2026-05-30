# Same-Day Momentum Trading Pack: SPEC 72-80

This document preserves the operator-approved same-day momentum spec pack in a portable form. It is the source document for rebuilding or continuing SPEC 72-80.

## Global Rules

- Live trading is permanently disabled.
- The same-day stream is paper-only.
- The existing multi-day forecast stream must continue unchanged.
- Operator modes:
  - Observe: compute and log, never act.
  - Paper Assist: propose actions, operator confirms each action.
  - Paper Autopilot: acts within explicit guards.
- Same-day stream ships in Observe first.
- Escalation to Paper Assist requires SPEC 78.
- Escalation to Autopilot requires SPEC 80.
- Minimum intraday cadence is 5 minutes.
- Canonical rows use `SnapshotRow` and `strategy_stream`.
- Kill-switches apply to same-day paths.
- No same-day code path may submit live orders.

## Execution Gate

SPEC 72 is mandatory before any production same-day implementation.

Codex or another agent must not start SPEC 73 until:

1. SPEC 72 has produced a retrospective edge report.
2. The operator has read the report.
3. The operator has explicitly authorized continuation.
4. If the verdict is AMBER or RED, the operator records the decision in `docs/same_day_continuation_decision.md`.

Recorded result for this repo:

- SPEC 72 report: `reports/same_day_edge/20260529_204130.md`
- Verdict: GREEN
- Continuation authorized by operator.

## Shared Vocabulary

- Same-day stream: intraday momentum trading stream.
- Multi-day stream: existing daily ranking and forecast stream.
- `strategy_stream`: stream that opened or owns a position/candidate.
- Arbitration: layer resolving conflicting stream opinions.
- Continuation probability: probability that momentum continues over the next 30 minutes by at least 50 bps.
- Reversal probability: separate tracked reversal risk.

Canonical stream values:

- `multi_day_forecast`
- `same_day_momentum`

## SPEC 72 - Retrospective Edge Validation

Goal: validate whether same-day momentum has positive expected value after costs on historical intraday data before production work begins.

Implemented status: implemented.

Commits:

- `259d7f2 Add same-day edge validation gate`
- `d502b56 Add intraday history downloader for same-day validation`
- `78a8307 Parse EODHD intraday Unix timestamps`
- `041eb78 Preserve parsed intraday timestamps in cache`
- `114bc6a Speed up same-day edge sampling`

Core files:

- `src/stockml/same_day/labels.py`
- `src/stockml/same_day/training.py`
- `src/stockml/intraday/history.py`
- `scripts/measure_same_day_edge.py`
- `scripts/download_intraday_history.py`

Core target:

- Decision time `t`: bar ending at `t` has just closed.
- Features use bars ending at or before `t-5min`.
- Entry uses next bar open at `t+5min`.
- Label horizon starts at `t+5min`.
- Long label uses horizon high.
- Short label uses horizon low.
- Positive label if continuation reaches at least 50 bps within 30 minutes.

Report sections:

1. Universe and sample.
2. Label distribution.
3. Model performance on holdout.
4. Economic performance on holdout.
5. Slice analysis.
6. Verdict.
7. Recommendations.
8. Caveats.

Verdict rules:

- GREEN: mean net bps > 15 across thresholds and t-stat > 2 at threshold 0.60.
- AMBER: mean net bps in [0, 15] at best threshold or t-stat in [1, 2].
- RED: mean net bps <= 0 at best threshold or t-stat < 1.

Focused tests:

- `tests/same_day/test_labels.py`
- `tests/same_day/test_training_no_leakage.py`
- `tests/same_day/test_intraday_history.py`

## SPEC 73 - Strategy Stream And Per-Position EOD Policy

Goal: add `strategy_stream` to positions/candidates/snapshots and make EOD flatten policy per-position.

Implemented status: implemented.

Commit:

- `def2713 Add strategy stream EOD foundation`

Schema:

- positions add `strategy_stream`
- positions add `must_flatten_at_eod`
- positions add `max_hold_until`
- candidate_pool add `strategy_stream`

Default policy:

- Existing rows become `multi_day_forecast`.
- Multi-day positions default `must_flatten_at_eod=False`.
- Same-day positions default `must_flatten_at_eod=True`.

Core files:

- `migrations/016_strategy_stream_positions_up.sql`
- `migrations/016_strategy_stream_positions_down.sql`
- `src/stockml/trading/snapshot_schema.py`
- `src/stockml/trading/snapshot_writer.py`
- `src/stockml/trading/order_builder.py`
- `src/stockml/autopilot/eod.py`

Focused tests:

- `tests/positions/test_strategy_stream.py`
- `tests/autopilot/test_eod_per_position.py`

VM verification:

- `64 passed`.

## SPEC 74 - Intraday Feature Panel

Goal: compute feature rows on every 5-minute bar for every same-day universe symbol. Features only; no scoring, candidates, or actions.

Implemented status: implemented.

Commit:

- `499debe Add same-day intraday feature panel`

Schema:

- `intraday_features`

Core files:

- `migrations/017_intraday_features_up.sql`
- `migrations/017_intraday_features_down.sql`
- `src/stockml/same_day/features.py`
- `src/stockml/same_day/universe.py`
- `src/stockml/same_day/feature_worker.py`

Feature contract:

- All features use bars ending strictly before decision time.
- Most recent feature bar ends at `t-5min`.
- Bar `[t-5min, t]` is not used.
- Worker runs only during 10:00-15:00 ET.
- Worker consults kill-switch before work.
- Worker writes one row per universe symbol per tick.
- No order submission in `src/stockml/same_day/`.

Focused tests:

- `tests/same_day/test_features.py`
- `tests/same_day/test_feature_no_lookahead.py`
- `tests/test_pipeline_event_schema.py`

VM verification:

- `17 passed`.

## SPEC 75 - Per-Position EOD Flatten Extension

Goal: extend EOD flatten so same-day positions reliably flatten, and failed same-day overnight positions block same-day next session only.

Implemented status: implemented.

Commit:

- `83295c0 Add same-day EOD overnight safeguards`

Core behavior:

- Same-day positions with `must_flatten_at_eod=True` flatten at T-5.
- Multi-day positions are not full-flattened by default.
- Multi-day weak/stale positions may trim at T-15.
- `OVERNIGHT_POSITIONS` records `same_day_count`, `multi_day_count`, and `symbols`.
- Same-day next session blocks only when `same_day_count > 0`.
- Multi-day-only overnight positions do not block streams.

Core files:

- `src/stockml/autopilot/eod.py`
- `src/stockml/intraday/kill_switch.py`
- `src/stockml/same_day/feature_worker.py`

Focused tests:

- `tests/autopilot/test_same_day_eod.py`
- `tests/autopilot/test_eod_per_position.py`
- `tests/test_eod_flatten.py`
- `tests/test_kill_switch.py`
- `tests/same_day/test_features.py`

VM verification:

- `39 passed`.

## SPEC 76 - Same-Day Gates And Arbitration

Goal: build same-day signal gates and arbitration between same-day and multi-day streams.

Implemented status: implemented.

Commit:

- `52c1cd2 Add same-day gates and stream arbitration`

Core files:

- `config/same_day.yaml`
- `migrations/018_arbitration_conflicts_up.sql`
- `migrations/018_arbitration_conflicts_down.sql`
- `src/stockml/same_day/gates.py`
- `src/stockml/arbitration/arbitrator.py`
- `src/stockml/arbitration/conflicts.py`
- `src/stockml/trading/outcome_reasons.py`
- `src/stockml/trading/reason_normalizer.py`

Gate order:

1. Liquidity.
2. Price band.
3. Market cap.
4. Spread.
5. Time of day.
6. Signal freshness.
7. Halts and catalysts.
8. Continuation/reversal probability.
9. Market and sector alignment.
10. Same-symbol activity limit.
11. Daily candidate cap.
12. Short borrow check.
13. Kill-switch.

Arbitration rules:

1. Held by multi-day blocks same-day.
2. Held by same-day blocks another same-day open.
3. Multi-day Long and same-day Long: multi-day wins.
4. Opposite stream directions: abstain and log conflict.
5. Multi-day No Decision and same-day actionable: same-day emits.
6. Same-day-only actionable: same-day emits.
7. Multi-day-only actionable: multi-day behavior unchanged.

Focused tests:

- `tests/same_day/test_gates.py`
- `tests/arbitration/test_arbitrator.py`
- `tests/test_alpaca_order_planner.py`
- `tests/test_pipeline_event_schema.py`

VM verification: covered by SPEC 77 VM run, `38 passed`.

## SPEC 77 - Same-Day Model Scoring And Candidate Generation

Status: implemented.

Goal: score the same-day model every 5 minutes using SPEC 74 features, apply SPEC 76 gates, and produce same-day candidate artifacts.

Core files:

- `src/stockml/same_day/training.py`
- `src/stockml/same_day/scoring.py`
- `src/stockml/same_day/score_worker.py`

Tables:

- `same_day_candidates`
- `same_day_signal_log`

Expected behavior:

- Separate long and short models.
- Independent calibration.
- Model lineage manifests.
- Model promotion gate.
- Signal log row for every universe symbol each tick.
- Candidate rows only for gate-passed signals.
- No order submission.

Focused tests:

- `tests/same_day/test_scoring.py`
- `tests/test_pipeline_event_schema.py`

VM verification: `38 passed`.

## SPEC 78 - Same-Day Operator UI And Missed Opportunity Report

Status: implemented.

Goal: make same-day candidates visible in Trading Console and enable Paper Assist. Generate a nightly missed-opportunity report.

Core files:

- `portal/services/same_day_view.py`
- `portal/templates/trading/_zones/_same_day_panel.html`
- `portal/templates/reports/missed_opportunities.html`
- `src/stockml/same_day/missed_ops.py`
- `scripts/generate_missed_ops_report.py`

Table:

- `same_day_missed_opportunities`

Paper Assist:

- Confirm Open uses existing paper broker path.
- Confirmed positions use `strategy_stream='same_day_momentum'`.
- Confirmed positions use `must_flatten_at_eod=True`.
- Override Skip logs operator decision.

Focused tests:

- `tests/same_day/test_missed_ops.py`
- `tests/test_portal_routes.py`
- `tests/test_pipeline_event_schema.py`

VM verification: `66 passed`.

## SPEC 79 - Stream Attribution And Position Sizing

Status: implemented.

Goal: stream-specific sizing, stream attribution in daily reports, and same-day daily loss caps.

Same-day sizing:

- max single position: 5% of equity
- default position value: min(3% of equity, $100)
- max concurrent same-day positions: 3
- max total same-day exposure: 15%
- min account equity: $250
- same-day max loss per day: -$50

Same-day stops:

- per-trade stop loss: -2% or 1.5 x ATR_5m, whichever is tighter
- trailing activates at +1.5%, gives back 0.7%
- time stop exits by T-30 before close

Core files:

- `config/same_day.yaml`
- `src/stockml/trading/position_sizing.py`
- `src/stockml/trading/order_planner.py`
- `src/stockml/autopilot/policy.py`
- `src/stockml/reports/daily.py`

Focused tests:

- `tests/order_planner/test_same_day_sizing.py`
- `tests/autopilot/test_same_day_policy.py`
- `tests/test_daily_reports.py`

VM verification: `34 passed`.

## SPEC 80 - Same-Day Autopilot Promotion Contract

Status: implemented-pending-vm.

Goal: define promotion from Paper Assist to Paper Autopilot. Code paths are behind a false-by-default flag.

Promotion criteria:

1. At least 100 confirmed Paper Assist opens over at least 30 calendar days.
2. Mean realized net P&L per trade > 0 with t-stat > 2.
3. Hit rate >= 50%.
4. Average win bps / average loss bps >= 1.5.
5. Operator override rate < 25%.
6. Worst single day > -2% of account equity.
7. No symbol/day concentration above limits.
8. No kill-switch cascade within 30 minutes of same-day Paper Assist action.

Auto-execution remains disabled unless:

- contract is met
- config flag is true
- PR-only flag flip is reviewed
- operator confirmation exists in code review

Even then, execution remains paper-only.

Core files:

- `config/autopilot.yaml`
- `migrations/021_same_day_promotion_evaluations_up.sql`
- `migrations/021_same_day_promotion_evaluations_down.sql`
- `src/stockml/autopilot/same_day_promotion.py`
- `src/stockml/autopilot/same_day_auto.py`
- `portal/templates/autopilot/same_day_promotion.html`

Focused tests:

- `tests/autopilot/test_same_day_promotion.py`
- `tests/test_portal_routes.py`
- `tests/test_pipeline_event_schema.py`
