# Per-Symbol Forecast Units Diagnosis

Date: 2026-05-15

## Finding

The observed per-symbol forecast magnitude issue was primarily a units conversion bug.

The motivating snapshot showed FWRD with:

- `risk_adjusted_score`: `0.53132675112664`
- `expected_trade_return`: `1.06265350225328`
- old `expected_5d_return`: `1.06265350225328`
- old `expected_move_bps`: about `10626`

The trading candidate pool stores `expected_trade_return` in percent-point style units for these rows. In this convention, `1.06265350225328` means about `1.06%`, or `106.27 bps`, not a raw return fraction of `106%`.

The old per-symbol forecast code copied that value into `expected_5d_return` and then computed:

```text
expected_move_bps = abs(expected_5d_return * 10000)
```

That transformed `1.06%` into `10,626 bps`, creating an unrealistic 5-day move for an $8 stock.

## Confirmed Hypothesis

Confirmed: Hypothesis 2.

The source value is percent-like, but display code treated it as a raw fraction. There is also a secondary guard against Hypothesis 1: slope-derived forecasts are now expressed as bps and rejected when the slope implies an unreasonable number of bps per score unit.

## Fix

The per-symbol forecast layer now uses one internal convention:

- internal return projections are basis points
- projected return fields use the `_bps` suffix
- legacy fraction-style fields are removed from the forecast CSV schema
- forecast values are capped before writing
- capped rows are marked with `cap_applied=true`
- pre-cap values are preserved in `pre_cap_expected_5d_bps`
- cap events are written to `forecast_cap_log` when a database is available

FWRD-style input now produces an expected 5-day return around `106 bps`, not `10,626 bps`.

