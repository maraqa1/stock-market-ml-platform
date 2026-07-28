# Same-Day Position Management Action

This change makes the unified position-management layer act on failed same-day holding evidence instead of treating it as advisory only.

## Problem

A position can be opened as a same-day trade, then later receive a holding review of:

- `holding_quality=avoid`
- `holding_gate_pass=false`
- `holding_gate_reason=holding_edge_not_confirmed`

Before this change, the position-management decision could still return `hold` with `primary_reason=no_action_required` unless the position had already crossed the broader loss, hard-stop, reversal, or profit-giveback thresholds.

## Behavior

For a same-day position, failed holding evidence is now actionable:

- Losing or flat position: `close`, `primary_reason=same_day_holding_edge_failed`
- Profitable position: `reduce`, `primary_reason=same_day_holding_edge_failed_profitable`
- Confirmed reversal: `close`, `primary_reason=confirmed_model_reversal`

The position-management output includes the holding-review fields used by the decision:

- `trading_stream`
- `holding_quality`
- `holding_gate_pass`
- `holding_gate_reason`

## Oversized Positions

If the current broker quantity is materially above the approved planned quantity, the position manager recommends a reduction back to the approved quantity:

- `recommended_action=reduce`
- `primary_reason=position_exceeds_approved_plan_size`
- `recommended_target_qty=<planned_suggested_quantity>`

This is designed for cases where an order was opened before sizing alignment was fixed.

## Safeguards

This does not change entry gates, model scoring, exposure limits, short-side policy, or live-trading guardrails. Paper Autopilot still respects open-order and anti-churn guards before submitting paper actions.
