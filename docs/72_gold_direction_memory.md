# Gold Direction Memory

Ticker direction memory is now derived in the Enhanced Gold v2 panel.

The Gold builder uses historical per-ticker forward 5-day sector alpha to create prior-outcome direction evidence. The current row is excluded from its own evidence by shifting the ticker history before calculating the memory fields.

## Added Gold Fields

- `ticker_direction_memory_scope`
- `ticker_direction_memory_status`
- `ticker_direction_sample_count`
- `ticker_long_win_rate_5d`
- `ticker_short_win_rate_5d`
- `ticker_avg_long_alpha_bps_5d`
- `ticker_avg_short_alpha_bps_5d`
- `ticker_direction_bias_gold`
- `ticker_direction_reason_gold`

## Bias Values

- `trust_long`: prior ticker history supports long direction.
- `trust_short`: prior ticker history supports short direction.
- `no_trade`: prior ticker history does not support either side.
- `insufficient_data`: ticker has not accumulated enough historical samples.

## Leakage Control

The direction-memory fields are calculated from prior rows only. A ticker/date row does not use its own forward return label to determine its direction memory.

## Scope

These fields are part of the Gold dataset and can flow into candidate diagnostics. They do not submit orders, flip direction, loosen gates, or enable live trading.
