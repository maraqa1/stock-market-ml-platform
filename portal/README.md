# StockML Portal

Flask portal for the isolated `stock-market-ml-platform` research outputs.

Default port: `8091`.

Start locally:

```bash
PYTHONPATH=src python scripts/run_portal.py
```

The portal reads only from this repository's `data/` folders. Missing CSV files render clear empty states instead of crashing.

Routes:

- `/`
- `/health`
- `/universe`
- `/data-quality`
- `/gold`
- `/signals`
- `/trading`
- `/model-validation`
- `/no-decision`
- `/stock/<ticker>`
