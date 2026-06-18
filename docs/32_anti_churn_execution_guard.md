# Anti-Churn Execution Guard

This guard is a paper-trading safety layer. It does not change model scoring, thresholds, or live-trading behavior.

## Defaults

- Same-cycle open and close for the same symbol are blocked and sent to manual review.
- Positions cannot be closed during the first 30 minutes unless the close reason is an emergency/confirmed reason such as hard stop, take profit, manual kill, broker correction, duplicate correction, or emergency risk breach.
- Symbols closed during the previous 60 minutes cannot be reopened.
- Same-day reverse-side re-entry is blocked.
- Stale or unknown signal state is not sufficient by itself to close a position.
- Defensive close requires an actual loss or risk breach.
- Rotation, same-day reversal close, and extended-hours execution are disabled while paper-trading churn is under diagnosis.

## Diagnostics

Blocked actions are written to:

`data/trading/diagnostics/anti_churn_report_YYYYMMDD_HHMMSS.csv`

Columns are stable: symbol, blocked_action, reason, existing_position_age_minutes, last_trade_time, last_trade_side, attempted_side, cycle_id, decision.

## Operating Rule

If this guard blocks trades, treat the report as the source of truth before changing thresholds or model scoring.
