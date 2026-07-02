# Ticker Direction Memory

Ticker direction memory records whether a specific ticker has historically behaved better in the original model direction or the inverse direction.

This layer is paper-trading research only. It does not submit orders, change model scores, loosen gates, increase exposure, or enable live trading.

## Purpose

The platform observed cases where current positions would have been profitable if the side had been inverted. A global inversion is unsafe because some tickers may be correct while others may be wrong. The memory is therefore calculated per ticker.

## Inputs

The runner reads the latest available direction-outcome diagnostics from:

- `data/trading/diagnostics/direction_inversion_open_positions_*.csv`
- `data/trading/diagnostics/trade_inverse_outcome_*.csv`
- `data/trading/diagnostics/inverse_strategy_diagnostic_*.csv`

If no input exists, the report status is `missing_data`.

## Output

The report is written to:

- `data/trading/diagnostics/ticker_direction_memory_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/ticker_direction_memory_YYYYMMDD_HHMMSS.md`

Important fields:

- `sample_count`
- `original_win_rate`
- `inverse_win_rate`
- `avg_original_return_bps`
- `avg_inverse_return_bps`
- `inverse_advantage_bps`
- `ticker_direction_bias`
- `ticker_direction_confidence`
- `ticker_direction_reason`

## Bias Values

- `trust_original`: ticker evidence supports the original side.
- `inverse_watch`: ticker evidence suggests the inverse side has performed better.
- `no_trade`: both sides look poor for this ticker.
- `insufficient_data`: not enough ticker-specific evidence.

## Direction Gate Integration

The direction gate consumes optional ticker-memory fields on candidate rows:

- `ticker_direction_bias`
- `ticker_direction_confidence`
- `ticker_direction_sample_count`
- `ticker_inverse_advantage_bps`
- `ticker_direction_reason`

If a ticker is `inverse_watch`, the gate returns `direction_inverse_watch` and blocks execution for review. The platform does not silently flip the trade.

If a ticker is `trust_original`, the gate records it as supporting evidence while still requiring all other model, calibration, risk, session, and execution gates to pass.

## Command

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_ticker_direction_memory.py
```

## Safety

This layer is designed to prevent repeated direction mistakes, not to justify new risk. It is evidence-gathering plus gating, and it keeps sparse per-ticker data as `insufficient_data`.
