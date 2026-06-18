# 24x5 Position Intent Guard

This guard protects StockML / Marketcast paper trading from accidental close, cover, or reversal churn in the 24x5 execution path.

## Purpose

Before any paper broker order is submitted, the platform derives the intended lifecycle action from the current broker position and the attempted order side:

- no position + buy: open long
- no position + sell: open short
- long + buy: increase long
- long + sell: reduce or close long
- short + sell: increase short
- short + buy: reduce or cover short
- order quantity greater than the existing opposite position: reversal

## Default Blocks

The guard blocks:

- same-day reversals by default
- 24x5 reversals by default
- close or cover attempts before the 30-minute minimum hold
- opposite-side orders when broker position state cannot be loaded

Early close is allowed only for emergency or explicitly permitted reasons:

- hard_stop_hit
- take_profit_hit
- manual_kill
- broker_error_correction
- duplicate_order_correction
- emergency_risk_breach

## Diagnostics

Blocked attempts are written to:

`data/trading/diagnostics/position_intent_guard_YYYYMMDD_HHMMSS.csv`

The activity journal also records `position_intent_blocked` with the symbol, attempted side, derived intent, and block reason.

## Paper Only

This guard does not enable live trading, change model scoring, loosen gates, or increase exposure. It only prevents unsafe paper broker submissions after a candidate has already passed upstream selection.
