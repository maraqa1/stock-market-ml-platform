#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/massa/stock-market-ml-platform"

sudo cp "${REPO_DIR}/deployment/systemd/stockml-alpaca-auto-trader.service" /etc/systemd/system/
sudo cp "${REPO_DIR}/deployment/systemd/stockml-alpaca-auto-trader.timer" /etc/systemd/system/
sudo cp "${REPO_DIR}/deployment/systemd/stockml-alpaca-tracking.service" /etc/systemd/system/
sudo cp "${REPO_DIR}/deployment/systemd/stockml-alpaca-tracking.timer" /etc/systemd/system/
sudo cp "${REPO_DIR}/deployment/systemd/stockml-position-monitor.service" /etc/systemd/system/
sudo cp "${REPO_DIR}/deployment/systemd/stockml-position-monitor.timer" /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable stockml-alpaca-auto-trader.timer
sudo systemctl enable stockml-alpaca-tracking.timer
sudo systemctl enable stockml-position-monitor.timer
sudo systemctl restart stockml-alpaca-auto-trader.timer
sudo systemctl restart stockml-alpaca-tracking.timer
sudo systemctl restart stockml-position-monitor.timer

sudo systemctl list-timers 'stockml-*' --no-pager

echo
echo "Installed Alpaca paper trading timers."
echo "Default behavior is safe: STOCKML_ALPACA_AUTOTRADE_ENABLED=false and STOCKML_ALPACA_SUBMIT_ORDERS=false."
echo "Position monitor runs every 10 minutes during market hours and writes hold/watch/close decisions."
echo "Review ${REPO_DIR}/docs/08_alpaca_paper_trading.md before enabling paper submission."
