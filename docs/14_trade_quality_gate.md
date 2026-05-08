# Trade Quality Gate

The paper-trading order plan is no longer a simple equal-notional conversion of model Long and Short rows.

Before any paper order can be submitted, StockML creates an auditable order plan with a trade-quality decision for each candidate.

## Inputs

- latest `advanced_model_signal_table_*.csv`
- latest price history from `data/raw/03_us_price_history_store.csv`
- latest metadata enrichment when available

## Approval Rules

Only `Long` and `Short` rows can enter the order planner. `No Decision` and `diagnostic_only` rows create no order.

The gate rejects candidates when:

- current price is missing or non-positive
- intraday return from open is below `-8%`
- price is in the bottom 20% of the intraday range after a gap down
- intraday volume is below `STOCKML_ALPACA_MIN_INTRADAY_VOLUME`
- market cap is below `STOCKML_ALPACA_MIN_MARKET_CAP`
- volatility tier is extreme
- expected trade return is below configured transaction cost
- risk-adjusted score is below `STOCKML_ALPACA_MIN_RISK_ADJUSTED_SCORE`
- stop loss cannot be calculated

## Risk Tiers

- `large_liquid`: full configured notional
- `mid_risk`: 50% of configured notional
- `speculative`: 25% of configured notional
- `reject`: zero notional and no submission

## Stop And Take Profit

Default:

- stop loss: 3%
- take profit: 6%
- max holding days: 5

High-volatility/speculative:

- stop loss: 5%
- take profit: 10%
- max holding days: 10

Short orders reverse the stop/take-profit direction.

## Order Plan Fields

The order plan includes:

- current price, open, high, low, volume
- market cap
- intraday range position
- intraday return from open
- volatility, liquidity, and risk tier
- approved notional
- suggested quantity
- stop loss and take profit
- max holding days
- trade quality status and reason

Rejected trades remain in the plan for auditability but are never submitted.
