# Position Health And Basket Risk

This paper-only layer adds explicit health labels for open positions and a basket-level pause for new entries. It does not change model training, model thresholds, or live-trading policy.

## Entry Alignment Gate

New entries are blocked when the latest signal is not trustworthy:

- `latest_signal_unknown_blocks_entry`
- `stale_signal_blocks_entry`
- `signal_direction_mismatch`
- `model_not_decision_grade`

The gate protects the basket from adding positions when the model signal is missing, stale, pointed the other way, or not in decision-grade status.

## Position Health

Every open paper position is classified into one actionable state:

- `healthy_hold`
- `watch`
- `watch_loss`
- `manual_review`
- `close_candidate`
- `close_now`

The goal is to avoid treating all losing positions as the same. A small fresh loss is a watch item; a stale red position is a close candidate; hard stops and confirmed reversals are close-now events.

## Basket Drawdown Pause

The `basket_risk` config pauses new entries when the basket is broadly red:

```yaml
basket_risk:
  pause_new_entries_if_red_position_pct_above: 0.70
  pause_new_entries_if_basket_return_below: -0.0075
  resume_new_entries_if_basket_return_above: -0.0025
```

When paused, monitoring and exits still run. Only new entries are blocked. The state is surfaced as `basket_state = new_entries_paused`.
