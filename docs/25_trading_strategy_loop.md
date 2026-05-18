# Trading Strategy Loop

This document defines the intended paper-autopilot trading strategy loop. It is a design reference for improving profitability through stricter position management, not a request to loosen entry criteria or add more trading activity.

## Strategy Goal

The strategy should keep capital only in positions that still have confirmed, current, executable edge.

The loop is:

1. Select candidates with model and forecast evidence.
2. Enter only when trading, risk, broker, and basket gates pass.
3. Re-score every open position on every intraday clock.
4. Hold positions whose signal remains aligned and fresh.
5. Close positions whose signal flips, expires, fails quality, breaches loss limits, or gives back protected profit.
6. Attribute every completed trade so the system can learn which sources and exits produce realized profit.

## Current Components

The platform already has these pieces:

- Primary candidate generation and model shortlist artifacts.
- Per-symbol forecast artifacts under `data/trading/per_symbol_forecast/`.
- Intraday promotion and near-miss candidate layers.
- Paper Autopilot auto-open logic in `src/stockml/autopilot/open.py`.
- Paper Autopilot close/risk rules in `src/stockml/trading/paper_autopilot.py`.
- Position intelligence in `src/stockml/trading/position_intelligence.py`.
- Portal views for positions, action queue, and autopilot status.

The missing strategic center is a stricter answer to:

> Should this open position still be held right now?

## Entry Policy

Entries should be allowed only when all of these are true:

- Candidate source is approved for automation.
- Forecast is confirmed or explicitly allowed by policy.
- Direction is aligned with the intended trade side.
- Profitability, risk/reward, liquidity, and volatility flags pass.
- Basket has capacity.
- Daily auto-open caps allow another entry.
- Broker asset metadata allows the order.
- Whole-share order quantity is at least 1.

Auto-open orders should use whole-share `qty`, not fractional `notional`, for both longs and shorts.

## Position Management Policy

Every open position should be joined to the latest per-symbol forecast each clock. The position manager should emit one clear state:

- `hold_confirmed`: position side remains aligned and quality flags pass.
- `protect_profit`: trailing profit protection is armed.
- `watch`: position remains acceptable, but no strong add/close action exists.
- `watch_loss`: position is losing but remains above defensive close thresholds.
- `watch_stale`: signal is missing, stale, or unknown, but P&L is still inside allowed risk.
- `close_signal_flip`: forecast now points against the held side.
- `close_quality_failed`: forecast exists but no longer passes required quality flags.
- `close_loss_no_confirmation`: position is losing and lacks a confirmed aligned signal.
- `close_defensive_loss`: existing defensive stale/unknown loss rule is breached.
- `close_trailing_giveback`: profit protection was armed and giveback threshold is breached.
- `close_hard_stop`: hard stop threshold is breached.

Close reasons must be structured and logged. The portal should show the exact trigger, not just `unknown`.

## Existing Risk Rules To Preserve

Current Paper Autopilot defensive rules:

- Hard stop: close at or below `-4.0%`.
- Defensive stale-signal stop: close stale signal at or below `-2.5%`.
- Defensive unknown-signal stop: close unknown signal at or below `-2.0%`.
- Trailing profit protection: arm at `+3.0%`; close after `1.5%` giveback when signal is stale or unknown.

These are risk-control rules. Changing them should be treated as a separate risk policy change.

## Proposed Next Design

Add a position-management decision layer that combines:

- Open broker positions.
- Latest per-symbol forecast.
- Position peak P&L from autopilot state.
- Current position P&L.
- Entry source and entry timestamp if available.
- Broker asset metadata when needed.

The output should become the source of truth for close decisions and portal display.

Recommended fields:

- `symbol`
- `held_side`
- `forecast_side`
- `signal_state`
- `forecast_confirmation`
- `side_alignment`
- `expected_profitability_score`
- `profitability_ok`
- `risk_reward_ok`
- `liquidity_ok`
- `volatility_ok`
- `signal_age_minutes`
- `unrealized_plpc`
- `peak_plpc`
- `giveback_plpc`
- `management_state`
- `close_trigger_reason`
- `management_summary`
- `entry_source`
- `entry_score`

## Basket-Level Controls

The strategy should also protect the whole basket:

- If basket unrealized P&L drops below a configured daily risk threshold, disable new opens for the day.
- If multiple positions have stale/unknown signals, reduce new entries until freshness recovers.
- If open positions exceed the configured max, do not open more until the basket returns under the cap.
- Treat long and short exposure separately so a long-only basket does not accidentally persist when short opportunities are stronger.

## Trade Attribution

Every closed trade should capture:

- Entry source: promotion, per-symbol forecast, near miss, fallback.
- Entry side: long or short.
- Entry score and forecast fields.
- Entry order shape: qty, side, price if available.
- Exit reason.
- Holding time.
- Realized P&L and realized return.
- Whether exit was model-driven, risk-driven, profit-protection-driven, or manual.

This is required before changing model thresholds. Without attribution, it is impossible to know whether losses come from bad entries, stale holds, missing exits, sizing, or broker execution constraints.

## Information Needed For Final Design

To finalize the position-management design, decide these policy choices:

1. Maximum acceptable loss per position before forced close.
2. Whether a signal flip should close immediately or require confirmation across two clocks.
3. Whether stale/unknown losing positions should close at `-1%`, `-1.5%`, or keep the current `-2%`.
4. Whether winners should trail from `+2%` or keep current `+3%`.
5. Whether trailing giveback should be `1%`, `1.5%`, or volatility-adjusted.
6. Maximum basket-level drawdown before new opens pause for the day.
7. Whether longs and shorts should have separate max counts.
8. Whether near-miss entries should be allowed while the basket already has open positions.
9. Whether all exits should be automatic, or some states should require portal approval.
10. Which outcome matters most: higher win rate, lower drawdown, or higher average return per trade.

## Design Principle

Do not improve profitability by simply opening more trades. Improve it by making the system more selective about what it continues to hold.

