# Fresh VM Transfer Runbook

This runbook moves the StockML platform to a fresh Ubuntu VM while keeping trading safe. The goal is to bring up code, secrets, historical artifacts, portal, nightly pipeline, and paper-trading timers in that order.

## 1. Freeze The Old VM

On the current VM:

```bash
cd /home/massa/stock-market-ml-platform

sudo systemctl stop stockml-intraday-trading-clock.timer stockml-alpaca-auto-trader.timer stockml-alpaca-tracking.timer stockml-position-monitor.timer || true
sudo systemctl stop stockml-portal.service || true

git status --short
git log -1 --oneline
```

Do not transfer with live paper timers still running. Record the last commit hash and the latest key artifacts:

```bash
ls -lt data/raw/03_us_price_history_store.csv \
  data/interim/03_us_price_validated_universe_*.csv \
  data/processed/05_us_feature_panel_*.csv \
  data/gold/06_us_gold_ml_dataset_*.csv \
  data/model_outputs/model_predictions_latest.csv \
  data/portal_outputs/08_alpaca_paper_candidate_pool_*.csv \
  data/portal_outputs/08_alpaca_paper_order_plan_*.csv \
  data/trading/snapshots/trading_snapshot_*.csv 2>/dev/null | head -40
```

## 2. Prepare The New VM

Install system packages and Python runtime. The existing deployment scripts assume `/opt/jupyter-env/bin/python3`; if the new VM uses a different path, export `PYTHON_BIN`.

```bash
sudo apt-get update
sudo apt-get install -y git rsync curl python3 python3-venv python3-pip postgresql postgresql-contrib

sudo python3 -m venv /opt/jupyter-env
sudo /opt/jupyter-env/bin/python3 -m pip install --upgrade pip
```

Clone the repository:

```bash
cd /home/massa
git clone https://github.com/maraqa1/stock-market-ml-platform.git
cd /home/massa/stock-market-ml-platform
git checkout dev
/opt/jupyter-env/bin/python3 -m pip install -r requirements.txt
```

## 3. Transfer Secrets

Copy the old VM `.env` carefully. It contains API keys and paper-trading controls.

```bash
scp OLD_VM:/home/massa/stock-market-ml-platform/.env /home/massa/stock-market-ml-platform/.env
chmod 600 /home/massa/stock-market-ml-platform/.env
```

Before enabling trading, keep submission disabled:

```bash
grep -E 'STOCKML_ALPACA|ALPACA|EODHD|STOCKML_PROFILE|DATABASE_URL|PORT' .env
```

Recommended safe defaults during migration:

```bash
STOCKML_ALPACA_AUTOTRADE_ENABLED=true
STOCKML_ALPACA_SUBMIT_ORDERS=false
STOCKML_WRITE_DATABASE=0
```

## 4. Transfer Data Artifacts

The most important file is the canonical price store. Copy the data tree first, then regenerate only if verification fails.

```bash
rsync -avh --progress OLD_VM:/home/massa/stock-market-ml-platform/data/ \
  /home/massa/stock-market-ml-platform/data/
```

Minimum required directories:

- `data/raw/03_us_price_history_store.csv`
- `data/interim/`
- `data/processed/`
- `data/gold/`
- `data/model_outputs/`
- `data/portal_outputs/`
- `data/trading/`

Optional but useful:

```bash
rsync -avh --progress OLD_VM:/home/massa/stock-market-ml-platform/logs/ \
  /home/massa/stock-market-ml-platform/logs/
```

## 5. Install Database And Services

If PostgreSQL is used:

```bash
cd /home/massa/stock-market-ml-platform
bash deployment/vm/install_database.sh
```

Install portal first:

```bash
bash deployment/vm/install_portal_service.sh
curl -fsS http://127.0.0.1:8091/health
sudo systemctl status stockml-portal --no-pager
```

Install the nightly full pipeline timer, but keep trading submission disabled:

```bash
STOCKML_PROFILE=nyse_full STOCKML_WRITE_DATABASE=0 bash deployment/vm/install_full_scheduler.sh
sudo systemctl list-timers 'stockml-*' --no-pager
```

Install intraday/paper timers only after dry-run checks pass:

```bash
bash deployment/vm/install_alpaca_auto_trader.sh
```

## 6. Verify Data Lineage

Run these checks on the new VM:

```bash
cd /home/massa/stock-market-ml-platform

PYTHONPATH=src /opt/jupyter-env/bin/python3 -m pytest \
  tests/test_price_incremental_logic.py \
  tests/test_gold_dataset_schema.py \
  tests/test_alpaca_order_planner.py \
  tests/test_intraday_trading_clock_deployment.py
```

Check that the latest artifacts are present:

```bash
ls -lt data/raw/03_us_price_history_store.csv \
  data/interim/03_us_price_validated_universe_*.csv \
  data/gold/06_us_gold_ml_dataset_*.csv \
  data/model_outputs/model_predictions_latest.csv \
  data/portal_outputs/08_alpaca_paper_order_plan_*.csv 2>/dev/null | head -30
```

Run a safe plan-only paper-trading pass:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_alpaca_paper_trader.py --plan-only
```

Run the intraday clock once:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_intraday_trading_clock.py
```

## 7. Verify Coverage Symptoms

Use the current known symptom list to confirm the repaired price path survived transfer:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_symbol_coverage_audit.py \
  --provider eodhd \
  --symbols BB SPCE DELL SST IMAX HPQ FLO RDW UP VSH LION YSG QBTS UTI DAO EL
```

Expected after the recent repairs:

- `HPQ`, `IMAX`, `LION`, `QBTS`, `RDW`, `SPCE`, `UTI`, `VSH` should have price rows after targeted repair.
- `BB` and `DELL` should reach model/candidate evidence after feature/gold/model rebuilds.
- `SST` and `UP` may still fail price validation if they do not meet history, price, or liquidity thresholds.
- `DAO` and `YSG` remain universe-mapping issues unless aliases are added.

Targeted repair command:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 -m stockml.prices.download_price_history \
  --provider eodhd \
  --symbols HPQ IMAX LION QBTS RDW SPCE SST UP UTI VSH \
  --force-full \
  --start-date 2018-01-01 \
  --batch-size 10 \
  --sleep-seconds 0.2
```

## 8. Rebuild For Next Day Readiness

After data is copied and targeted price repair is done:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 - <<'PY'
from stockml.prices.validate_price_history import build_price_quality_report
print(build_price_quality_report(provider_name="eodhd"))
PY
```

Use the printed validated universe file:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 -m stockml.features.build_feature_panel \
  --exchange NYSE,NASDAQ \
  --universe-file data/interim/03_us_price_validated_universe_<STAMP>.csv \
  --metadata-file data/interim/04_us_metadata_enriched_<STAMP>.csv
```

Then:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 -m stockml.gold.build_gold_dataset \
  --exchange NYSE,NASDAQ \
  --feature-file data/processed/05_us_feature_panel_<STAMP>.csv \
  --skip-sentiment \
  --shard-rows 750000

PYTHONPATH=src /opt/jupyter-env/bin/python3 -m stockml.models.build_model_outputs \
  --gold-file data/gold/06_us_gold_ml_dataset_<STAMP>.csv \
  --model-shards 20 \
  --live-signal-mode \
  --baseline-only

PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_alpaca_paper_trader.py --plan-only
```

## 9. Enable Trading Submission Last

Only after portal, plan-only, intraday clock, and coverage audit are healthy:

```bash
sudo nano /etc/stockml/stockml.env
```

Set:

```text
STOCKML_ALPACA_AUTOTRADE_ENABLED=true
STOCKML_ALPACA_SUBMIT_ORDERS=true
```

Then:

```bash
sudo systemctl restart stockml-intraday-trading-clock.timer stockml-alpaca-auto-trader.timer stockml-alpaca-tracking.timer stockml-position-monitor.timer
sudo systemctl list-timers 'stockml-*' --no-pager
```

## 10. Rollback

If the new VM misbehaves:

```bash
sudo systemctl stop 'stockml-*' || true
```

Keep the old VM powered on but with trading timers stopped until the new VM has passed one full overnight pipeline plus one market-open dry run.
