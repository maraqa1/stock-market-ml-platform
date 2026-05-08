# Trade Quality Gate

The trade quality gate converts model signals into auditable paper-trading candidates. It does not enable live trading.

Workflow:

1. Check signal eligibility.
2. Enrich missing quote fields from the latest price-history store.
3. Apply price, liquidity, market-cap, volatility, anomaly, and score gates.
4. Assign risk tier.
5. Size position by account, basket, tier, and confidence.
6. Calculate stop loss, take profit, and max holding days.
7. Mark only approved or reduced rows as order eligible.

Statuses:

- `approved`: high-quality liquid stock with full risk-adjusted size.
- `reduced`: medium or speculative stock with reduced notional.
- `rejected`: hard rule failure; no order may be submitted.

Core rejection reasons include `price_below_minimum`, `market_cap_below_minimum`, `liquidity_below_minimum`, `volatility_extreme`, `bottom_intraday_range_after_gap_down`, `expected_trade_return_below_threshold`, `risk_adjusted_score_below_threshold`, `shorting_disabled`, `quantity_below_one`, `stop_loss_unavailable`, and `take_profit_unavailable`.

Only rows with `trade_quality_status` in `approved` or `reduced`, `order_eligible = true`, and `suggested_quantity >= 1` can reach Alpaca paper submission.
