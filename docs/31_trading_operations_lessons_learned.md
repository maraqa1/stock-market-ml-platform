# Trading Operations Lessons Learned

This document records operational lessons from the June 2026 paper-trading readiness and execution investigations. It is meant to prevent repeated debugging loops and to make pre-market readiness checks explicit.

## Core Principle

Do not treat a green portal card as sufficient proof of trading readiness. Readiness requires all of the following to agree:

- the nightly pipeline manifest is complete
- the latest model signal table is from today's run
- the latest candidate pool and order plan are from today's model
- broker open orders and positions match the autopilot state
- monitor services can run successfully when the account is flat
- ticker lineage can explain why a ticker reached or missed final selection

## Lesson 1 - Candidate Pool Can Be Stale While Model Is Fresh

Observed issue:

- The fresh model file existed for the current day.
- The latest candidate pool and order plan were still from the prior day.
- This made it look like current model selections had inconsistencies, when the real issue was artifact timestamp mismatch.

Example:

- `advanced_model_signal_table_20260612_065423.csv` was fresh.
- `08_alpaca_paper_candidate_pool_20260611_152557.csv` was stale.

Operational rule:

- Never review candidate quality without comparing candidate pool timestamp to model timestamp.
- If candidate pool is older than the model, label the candidate pool as stale and do not infer model behavior from it.

Required check:

```bash
ls -lt data/model_outputs/advanced_model_signal_table_*.csv \
       data/portal_outputs/08_alpaca_paper_candidate_pool_*.csv \
       data/portal_outputs/08_alpaca_paper_order_plan_*.csv | head -10
```

## Lesson 2 - Plan-Only Candidate Build Starts After Profile Pipeline Exits

Observed issue:

- The system appeared stuck after model outputs were written.
- Candidate pool had not started because `run_profile_pipeline.py --write-database` was still loading large database tables.
- The nightly systemd command runs candidate generation only after the profile pipeline exits successfully.

Observed command shape:

```bash
run_profile_pipeline.py --profile us_full --write-database \
&& run_alpaca_paper_trader.py --plan-only
```

Operational rule:

- If the service process is still `run_profile_pipeline.py`, candidate pool generation has not started.
- Candidate pool generation starts only when the shell advances to `run_alpaca_paper_trader.py --plan-only`.

Required check:

```bash
systemctl status stockml-full-nightly.service --no-pager -l
ps -eo pid,ppid,etime,pcpu,pmem,rss,stat,cmd | grep -E 'run_profile_pipeline|run_alpaca_paper_trader' | grep -v grep
```

## Lesson 3 - Historical Tracking Rows Are Not Open Orders

Observed issue:

- Latest tracking file had historical order rows with statuses such as `filled` and `canceled`.
- Autopilot state was incorrectly rewritten as if all tracked rows were open orders.
- This caused false `open_orders` counts and could block new trading cycles.

Fixed behavior:

- Only statuses such as `new`, `accepted`, `pending_new`, and `partially_filled` count as open tracked orders.
- `filled`, `canceled`, `expired`, and rejected/error rows do not count as open.

Operational rule:

- `orders_tracked` means rows inspected.
- `tracked_open_orders` means currently open tracked orders.
- These are different and must not be conflated.

Required check:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_alpaca_paper_trader.py --track-only
PYTHONPATH=src /opt/jupyter-env/bin/python3 - <<'PY'
import json
from stockml.common.paths import PORTAL_OUTPUTS_DIR
state = json.loads((PORTAL_OUTPUTS_DIR / "paper_autopilot_state.json").read_text())
for key in ["open_orders", "broker_open_orders", "tracked_open_orders", "open_positions"]:
    print(key, state.get(key))
PY
```

## Lesson 4 - Empty Position Files Are Valid Flat-Account State

Observed issue:

- Alpaca returned no open positions.
- The platform wrote an empty/one-byte positions CSV.
- Position monitor crashed with `pandas.errors.EmptyDataError`.

Fixed behavior:

- Empty positions files are treated as empty dataframes.
- A flat account should not crash the monitor.

Operational rule:

- Empty positions file means flat account, not monitor failure.
- Monitor service should exit successfully even when positions are empty.

Required check:

```bash
systemctl status stockml-position-monitor.service --no-pager -l
journalctl -u stockml-position-monitor.service --since "today" --no-pager -n 80
```

## Lesson 5 - Intraday Promotions Need Model Evidence

Observed issue:

- Strong intraday promoted candidates reached auto-open as bare symbols.
- Candidate pool/model evidence already existed, but was not carried into the autopilot selection object.
- The auto-open gate blocked these candidates as `model_evidence_missing`.

Fixed behavior:

- Intraday promoted candidates are enriched from the latest candidate pool with:
  - `trade_action`
  - `directional_action`
  - `directional_strength`
  - `meta_label_decision`
  - `trade_quality_status`
  - `candidate_status`
  - `order_eligible`
  - `risk_adjusted_score`

Operational rule:

- Do not loosen model evidence gates to fix missing handoff.
- Fix the handoff so the gate sees the evidence that already exists.

Required check:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 - <<'PY'
from stockml.autopilot.open import latest_strong_candidates, model_evidence_block_reason
for c in latest_strong_candidates(limit=20):
    details = dict(c.get("details") or {})
    print(c.get("symbol"), model_evidence_block_reason(c, details), details.get("model_evidence_source"))
PY
```

## Lesson 6 - Ticker Lineage Must Be Formal, Not Reconstructed Manually

Observed issue:

- Questions such as "Why was SNOW not traded?" required manually checking model, candidate pool, order plan, tracking, and positions.
- This made it hard to distinguish:
  - model rejection
  - candidate gate rejection
  - order-plan selection pressure
  - stale artifact mismatch
  - broker fill/cancel result

Added tool:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_ticker_lineage.py
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_ticker_lineage.py --symbols SNOW VPG RXO HUM EXTR
```

Output:

```text
data/portal_outputs/ticker_lineage_YYYYMMDD_HHMMSS.csv
```

Operational rule:

- Use ticker lineage before guessing why a ticker was or was not traded.
- If `candidate_order_eligible=True` and `order_plan_seen=False`, the next question is final order selection pressure, not model approval.
- If `candidate_seen=True` and `model_seen=False`, first check artifact timestamp alignment.

## Lesson 7 - Trading Readiness Should Be Evaluated In Order

Use this order before market open:

1. Confirm nightly process status.

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_pipeline_doctor.py --stale-after-minutes 300
systemctl status stockml-full-nightly.service --no-pager -l
```

2. Confirm fresh model exists.

```bash
ls -lt data/model_outputs/advanced_model_signal_table_*.csv | head -3
```

3. Confirm fresh candidate pool and order plan exist after the model.

```bash
ls -lt data/portal_outputs/08_alpaca_paper_candidate_pool_*.csv \
       data/portal_outputs/08_alpaca_paper_order_plan_*.csv | head -10
```

4. Confirm broker state is flat or expected.

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_alpaca_paper_trader.py --track-only
```

5. Confirm autopilot state matches broker state.

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_paper_autopilot.py status
```

6. Generate ticker lineage for final candidates.

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_ticker_lineage.py
```

## Current Known Gap

Ticker lineage currently shows ticker-level lineage and warnings, but should be further improved to distinguish:

- `ticker_missing_upstream_data`
- `candidate_pool_stale_vs_model`
- `order_plan_stale_vs_model`
- `candidate_pool_from_prior_run`

This prevents stale artifact warnings from being mistaken for live model/data defects.

## What Not To Do

- Do not increase exposure because the system is flat.
- Do not loosen gates because a candidate is missing model evidence.
- Do not trust yesterday's candidate pool after today's model is ready.
- Do not treat filled/canceled historical orders as open orders.
- Do not treat an empty positions file as a failure.
- Do not infer ticker rejection reason without running ticker lineage.

