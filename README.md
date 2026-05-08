# Stock Market ML Platform

Research-grade isolated stock-market ML platform for US equity universe construction, Gold dataset generation, walk-forward model validation, ranking-first Long / Short / No Decision signals, and portal-ready outputs.

This repository is isolated from the current running Yahoo student platform.

It does not modify:
- /home/massa/notebooks/yahoo_sector_data
- /usr/local/bin/build-yahoo-advanced-trading-model
- /opt/yahoo-student-portal/app.py

All new data is written under:
- /home/massa/stock-market-ml-platform/data

## Current pipeline

The platform is organized as a ranking-first research stack:

1. US equity universe construction
2. Yahoo Finance OHLCV price ingestion and validation
3. Yahoo metadata enrichment
4. Technical, liquidity, volatility, sector-relative, and market-context feature panel
5. Pluggable news sentiment panel
6. Gold ML dataset with ranking targets and candidate-selection scores
7. Portal-ready CSV outputs for dashboard metrics, signal lists, and sector breakdowns

## Limited validation commands

Use limited runs while developing so the repo does not force a full-market crawl:

```bash
PYTHONPATH=src python -m pytest -q
python scripts/run_metadata_pipeline.py --limit 25
python scripts/run_feature_pipeline.py --limit-tickers 25
python scripts/run_sentiment_pipeline.py --limit 25
python scripts/run_gold_pipeline.py --limit-tickers 25
```

Generated CSV outputs are intentionally ignored by Git and Docker build contexts.

## Portal

The first Flask portal lives under `portal/` and serves the research pipeline outputs on port `8091`, separate from any legacy portal on `8090`.

```bash
PYTHONPATH=src python scripts/run_portal.py
```

Production-style service assets:

- `deployment/systemd/stockml-portal.service`
- `deployment/vm/install_portal_service.sh`

Fresh VM install:

```bash
cd /home/massa/stock-market-ml-platform
git pull origin main
bash deployment/vm/install_portal_service.sh
```

Health check:

```bash
curl http://127.0.0.1:8091/health
```

Troubleshooting:

```bash
sudo systemctl status stockml-portal --no-pager
sudo journalctl -u stockml-portal -n 80 --no-pager
```

## NASDAQ 500 Limited Build

For a practical limited data pull, build Gold from the first 500 NASDAQ-listed tradable candidates:

```bash
cd /home/massa/stock-market-ml-platform
/opt/jupyter-env/bin/python3 scripts/run_profile_pipeline.py --profile nasdaq_500
```

Model training and prediction read only from the latest Gold dataset under `data/gold/`. Upstream raw, interim, and processed files are never used directly by model code.

Sentiment currently uses Yahoo Finance ticker news plus CNBC RSS headlines. CNBC articles are attached to a ticker only when the ticker appears as an exact token in the headline or summary; unmatched articles are ignored.

The model pipeline writes validation, feature importance, predictions, and signal tables:

```bash
/opt/jupyter-env/bin/python3 scripts/run_model_pipeline.py --limit-tickers 500
```

Growth profiles live in `config/pipeline_profiles.yaml`:

- `nasdaq_500`
- `nasdaq_1500`
- `nasdaq_full`

Nightly incremental profile scheduler:

```bash
cd /home/massa/stock-market-ml-platform
STOCKML_PROFILE=nasdaq_500 bash deployment/vm/install_full_scheduler.sh
```

To grow later, change only the profile:

```bash
STOCKML_PROFILE=nasdaq_1500 bash deployment/vm/install_full_scheduler.sh
```

## Database

PostgreSQL can be used as the persistent store for generated pipeline outputs while CSV exports remain available.

Fresh VM PostgreSQL bootstrap:

```bash
cp .env.template .env
nano .env
bash deployment/vm/install_database.sh
```

Set a connection string:

```bash
export DATABASE_URL='postgresql+psycopg2://stockml:stockml@localhost:5432/stockml'
```

Initialize schema:

```bash
/opt/jupyter-env/bin/python3 scripts/init_database.py
```

Load latest generated outputs:

```bash
/opt/jupyter-env/bin/python3 scripts/load_latest_outputs_to_database.py
```

Run a profile and load its outputs into the database:

```bash
/opt/jupyter-env/bin/python3 scripts/run_profile_pipeline.py --profile nasdaq_500 --write-database
```

Enable database writes in the nightly scheduler:

```bash
export DATABASE_URL='postgresql+psycopg2://stockml:stockml@localhost:5432/stockml'
STOCKML_PROFILE=nasdaq_500 STOCKML_WRITE_DATABASE=1 bash deployment/vm/install_full_scheduler.sh
```

The database loader stores normalized tables for universe, price history, metadata, sentiment, model artifacts, and wide JSON-backed panel rows for feature and Gold datasets. The portal still reads CSV outputs in this iteration; the database is the persistence layer for scale and later API/portal query work.

Reboot persistence:

- PostgreSQL is enabled as a system service and stores data in the VM's PostgreSQL data directory.
- Portal and nightly pipeline services are enabled through systemd.
- Human-managed runtime configuration starts in the repo-local `.env` file, which is ignored by git.
- Systemd runtime configuration is copied to `/etc/stockml/stockml.env`, including `DATABASE_URL`, database credentials, `STOCKML_PROFILE`, `STOCKML_WRITE_DATABASE`, and `PORT`.
- Re-running the install scripts updates services without deleting generated CSV files or PostgreSQL data.

Recover the configured database password on the VM:

```bash
grep STOCKML_DB_PASSWORD .env
sudo grep STOCKML_DB_PASSWORD /etc/stockml/stockml.env
```

