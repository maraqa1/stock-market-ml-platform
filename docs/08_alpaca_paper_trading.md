# Alpaca Paper Trading Preparation

Status: prepared, dry-run by default.

This integration turns StockML model signal outputs into a paper order plan for Alpaca. It does not submit paper orders unless explicitly enabled.

## Safety Defaults

- `STOCKML_ALPACA_SUBMIT_ORDERS=false` by default.
- Uses the Alpaca paper base URL by default.
- Limits order count and notional size.
- Limits total basket notional.
- Filters low-priced names before creating orders.
- Limits sector concentration when sector data is available.
- Filters weak signals before creating orders.
- Writes order plans and results under `data/portal_outputs`.
- Exposes the latest plan and results in the portal at `/trading`.

## Environment

Add these values to the VM `.env` file:

```bash
ALPACA_API_KEY=your-paper-key
ALPACA_SECRET_KEY=your-paper-secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets

STOCKML_ALPACA_SUBMIT_ORDERS=false
STOCKML_ALPACA_EXTENDED_HOURS=false
STOCKML_ALPACA_MAX_ORDERS=10
STOCKML_ALPACA_MAX_NOTIONAL_PER_ORDER=1000
STOCKML_ALPACA_MAX_TOTAL_NOTIONAL=10000
STOCKML_ALPACA_MIN_TRADE_PRICE=5
STOCKML_ALPACA_MAX_SECTOR_FRACTION=0.4
STOCKML_ALPACA_MIN_SIDE_PROBABILITY=0.55
STOCKML_ALPACA_MIN_ABS_PROBABILITY_EDGE=0.05
```

## Dry Run

```bash
/opt/jupyter-env/bin/python3 scripts/run_alpaca_paper_trader.py
```

Outputs:

- `data/portal_outputs/08_alpaca_paper_order_plan_*.csv`
- `data/portal_outputs/08_alpaca_paper_order_results_*.csv`

Review the latest run in the portal:

```bash
curl http://127.0.0.1:8091/trading
```

## Submit Paper Orders

Only enable this after reviewing the generated plan.

```bash
STOCKML_ALPACA_SUBMIT_ORDERS=true /opt/jupyter-env/bin/python3 scripts/run_alpaca_paper_trader.py
```

## Notes

Alpaca paper trading uses the Trading API paper endpoint. Extended-hours and 24/5 behavior depends on Alpaca asset eligibility and order constraints. Keep this integration on paper mode until validation, risk limits, and position reconciliation are implemented.
