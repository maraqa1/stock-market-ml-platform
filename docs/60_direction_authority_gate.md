# Direction Authority Gate

The Direction Authority Gate resolves candidate direction before paper execution.

Execution direction is authoritative only when `source_trade_action` is `Long` or `Short`. Planner-derived fields such as `trade_action` and `directional_action` are retained for research and diagnostics, but they must not make a `No Decision` row executable.

Rules:

- `source_trade_action=No Decision` is not executable.
- Planner-derived Long/Short on a No Decision row becomes `research_only`.
- `ticker_direction_bias=trust_long` supports Long/buy.
- `ticker_direction_bias=trust_short` supports Short/sell.
- Opposing ticker memory creates `direction_memory_conflict`.
- Insufficient memory defaults to watch/research handling, not full approval.
- Shorts remain blocked unless short-side validation is explicitly enabled and positive.
- `side_probability` is stored as `raw_side_score` unless a calibrated probability is present.
- Inverse evidence is diagnostic only.

Primary outputs:

- `direction_authority_source`
- `source_approved_direction`
- `planner_derived_direction`
- `final_proposed_side`
- `executable_direction_status`
- `direction_alignment_status`
- `direction_conflict`
- `direction_conflict_reason`
- `direction_memory_supports_side`
- `direction_memory_opposes_side`
- `direction_memory_status`
- `direction_resolution`
- `direction_resolution_reason`
- `raw_side_score`
- `calibrated_probability_win`
- `probability_calibration_status`

Diagnostic command:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_direction_authority_diagnostic.py
```

Diagnostic outputs:

- `data/trading/diagnostics/direction_authority_detail_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/direction_authority_summary_YYYYMMDD_HHMMSS.md`
- split execution, blocked, and research candidate pools in `data/trading/diagnostics/`

This gate is paper-only and does not change model scoring, flip directions, loosen gates, increase exposure, or enable live trading.
