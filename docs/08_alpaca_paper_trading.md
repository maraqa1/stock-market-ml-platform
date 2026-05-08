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
STOCKML_ALPACA_AUTOTRADE_ENABLED=false
STOCKML_ALPACA_AUTOTRADE_START_UTC=14:45
STOCKML_ALPACA_AUTOTRADE_END_UTC=20:30
STOCKML_ALPACA_IGNORE_TRADE_WINDOW=false
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
- `data/portal_outputs/08_alpaca_paper_order_tracking_*.csv`
- `data/portal_outputs/08_alpaca_paper_positions_*.csv`

Review the latest run in the portal:

```bash
curl http://127.0.0.1:8091/trading
```

## Track Orders

After a dry run or a paper submission run, refresh the latest tracking snapshot:

```bash
/opt/jupyter-env/bin/python3 scripts/run_alpaca_paper_trader.py --track-only
```

The portal shows the latest tracking rows, Alpaca order IDs, fill status, fill quantity, average fill price, and current paper positions when Alpaca credentials are configured. In dry-run mode the tracking table still records the planned lifecycle, but no Alpaca order IDs exist because no orders were sent.

## Auto Paper Trading

Install the VM timers:

```bash
cd /home/massa/stock-market-ml-platform
sudo bash deployment/vm/install_alpaca_auto_trader.sh
```

Default behavior is safe:

- `STOCKML_ALPACA_AUTOTRADE_ENABLED=false` writes a dry-run plan only.
- `STOCKML_ALPACA_SUBMIT_ORDERS=false` prevents Alpaca order submission even if the auto-trader timer runs.
- The auto-trader timer runs Monday to Friday at `14:45 UTC`.
- The tracking timer refreshes order and position status hourly during the US market session window.

To allow automated paper-order submission, both flags must be explicitly enabled in the VM `.env`:

```bash
STOCKML_ALPACA_AUTOTRADE_ENABLED=true
STOCKML_ALPACA_SUBMIT_ORDERS=true
```

Recommended first step:

```bash
/opt/jupyter-env/bin/python3 scripts/run_alpaca_auto_trader.py --force
curl http://127.0.0.1:8091/trading
```

Keep this on Alpaca paper trading until order reconciliation, drawdown controls, and human review gates are proven over multiple market sessions.

## Submit Paper Orders

Only enable this after reviewing the generated plan.

```bash
STOCKML_ALPACA_SUBMIT_ORDERS=true /opt/jupyter-env/bin/python3 scripts/run_alpaca_paper_trader.py
```

## Notes

Alpaca paper trading uses the Trading API paper endpoint. Extended-hours and 24/5 behavior depends on Alpaca asset eligibility and order constraints. Keep this integration on paper mode until validation, risk limits, and position reconciliation are implemented.
