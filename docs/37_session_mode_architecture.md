# Session Mode Architecture

StockML now treats broker submission time as an explicit session mode instead of folding all non-regular sessions into a broad extended-hours flag.

## Modes

- `regular_session`: US market hours, 09:30-16:00 America/New_York.
- `pre_market`: 04:00-09:30 America/New_York.
- `after_hours`: 16:00-20:00 America/New_York.
- `overnight_24_5`: weekday non-weekend hours outside pre-market, regular, and after-hours.
- `weekend_closed`: Saturday and Sunday in America/New_York.

## Policy

`session_order_policy` owns broker-submission eligibility, order type, extended-hours attribution, spread guard, quote freshness metadata, and size multiplier. Regular session can submit normal market orders when configured. Non-regular trading requires limit orders and explicit session enablement. Overnight 24x5 requires overnight-tradable assets, non-halted assets where available, strict spread checks, quote freshness, reduced size, anti-churn, and position-intent protections.

## Attribution

Every autopilot order decision records session fields in the open log details:

- `session_mode`
- `order_policy`
- `extended_hours`
- `overnight_tradable`
- `spread_bps`
- `quote_freshness_seconds`
- `session_reject_reason`

This makes 24x5 behavior auditable without changing model scores, gates, exposure limits, or live-trading state.
