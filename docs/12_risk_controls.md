# Risk Controls

Paper orders are sized from account equity and capped by basket and daily-order limits.

Default policy:

- minimum price: 5.00
- minimum market cap: 300M
- minimum 20-day average dollar volume: 5M
- max position: 3% of account equity
- max basket notional: 10,000
- max daily orders: 10
- short selling disabled
- live trading disabled

Risk tier multipliers:

- `high_quality`: 1.00
- `medium`: 0.50
- `speculative`: 0.25
- `reject`: 0.00

Confidence multiplier is based on `side_probability` and reduces size when model confidence is weaker. A row with quantity below one share is rejected.
