# Strategy Redesign Foundation

This foundation separates entry gates from position-management diagnostics.

The registry classifies gates by safety, strategy quality, execution quality, position-management trigger, research-only, or experimental status. Must-have safety gates remain mandatory for new entries and are not removed by this diagnostic layer.

Default main strategy policy:

- `name: nightly_swing_long_validation`
- executable lane: `nightly_swing_long`
- disabled lanes: `short_research`, `intraday_momentum_research`, `raw_candidate_experiment`
- max new orders per day: `1`
- max new orders per cycle: `1`
- max open positions total: `3`
- shorts disabled
- 24/5 execution diagnostics-only
- expected-return calibration required
- executable source trade action required
- block new entries when all open positions are red or drawdown is active

All position recommendations are diagnostics-only. They do not submit close orders or new orders.
