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
/opt/jupyter-env/bin/python3 scripts/run_universe_pipeline.py
/opt/jupyter-env/bin/python3 scripts/run_price_pipeline.py --exchange NASDAQ --limit 500
/opt/jupyter-env/bin/python3 scripts/run_metadata_pipeline.py --limit 500
/opt/jupyter-env/bin/python3 scripts/run_feature_pipeline.py --limit-tickers 500
/opt/jupyter-env/bin/python3 scripts/run_sentiment_pipeline.py --limit 500
/opt/jupyter-env/bin/python3 scripts/run_gold_pipeline.py --limit-tickers 500
/opt/jupyter-env/bin/python3 scripts/run_model_pipeline.py --limit-tickers 500
```

Model training and prediction read only from the latest Gold dataset under `data/gold/`. Upstream raw, interim, and processed files are never used directly by model code.

The model pipeline writes validation, feature importance, predictions, and signal tables:

```bash
/opt/jupyter-env/bin/python3 scripts/run_model_pipeline.py --limit-tickers 500
```

