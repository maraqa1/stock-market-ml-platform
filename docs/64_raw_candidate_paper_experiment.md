# Raw Candidate Paper Experiment

This mode creates a separate paper-only experiment lane named `raw_candidate_no_gates`.

It does not replace or loosen the normal Paper Autopilot path. Normal execution-ranked candidates, expected-return calibration, short-side policy, session policy, and risk gates remain unchanged.

## Defaults

The experiment is disabled by default in `config/raw_candidate_experiment.yaml`.

Default caps:

- `max_trades_per_day: 3`
- `max_trades_per_cycle: 1`
- `max_notional_per_trade: 250`
- `max_total_experiment_notional: 750`
- `daily_loss_stop_usd: 50`
- `allow_shorts: false`
- `live_trading_allowed: false`

## Ledgers

Experiment files are written under:

- `data/trading/experiments/raw_candidate_experiment_events_YYYYMMDD.csv`
- `data/trading/experiments/raw_candidate_experiment_trades_YYYYMMDD.csv`

Every row is tagged with:

- `experiment_mode=raw_candidate_no_gates`
- `strategy_mode=experiment`
- original candidate status and block reasons
- whether normal gates would have passed
- `client_order_id` containing `rawexp`

## Run

Dry run:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_raw_candidate_experiment.py --dry-run
```

Attribution only:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_raw_candidate_experiment.py --attribution-only
```

## Safety Notes

Live trading remains disabled. The experiment must not be used to bypass the normal strategy. Its purpose is to learn whether rejected, research-only, No Decision, or raw-ranked candidates carry useful signal.
