#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/massa/stock-market-ml-platform"

sudo cp "${REPO_DIR}/deployment/systemd/stockml-alpaca-auto-trader.service" /etc/systemd/system/
sudo cp "${REPO_DIR}/deployment/systemd/stockml-alpaca-auto-trader.timer" /etc/systemd/system/
sudo cp "${REPO_DIR}/deployment/systemd/stockml-alpaca-tracking.service" /etc/systemd/system/
sudo cp "${REPO_DIR}/deployment/systemd/stockml-alpaca-tracking.timer" /etc/systemd/system/
sudo cp "${REPO_DIR}/deployment/systemd/stockml-position-monitor.service" /etc/systemd/system/
sudo cp "${REPO_DIR}/deployment/systemd/stockml-position-monitor.timer" /etc/systemd/system/
sudo cp "${REPO_DIR}/deployment/systemd/stockml-intraday-trading-clock.service" /etc/systemd/system/
sudo cp "${REPO_DIR}/deployment/systemd/stockml-intraday-trading-clock.timer" /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable stockml-alpaca-auto-trader.timer
sudo systemctl enable stockml-alpaca-tracking.timer
sudo systemctl enable stockml-position-monitor.timer
sudo systemctl enable stockml-intraday-trading-clock.timer
sudo systemctl restart stockml-alpaca-auto-trader.timer
sudo systemctl restart stockml-alpaca-tracking.timer
sudo systemctl restart stockml-position-monitor.timer
sudo systemctl restart stockml-intraday-trading-clock.timer

sudo systemctl list-timers 'stockml-*' --no-pager

echo
echo "Installed Alpaca paper trading timers."
echo "Default behavior is safe: STOCKML_ALPACA_AUTOTRADE_ENABLED=false and STOCKML_ALPACA_SUBMIT_ORDERS=false."
echo "Position monitor runs every 30 seconds during market hours and writes hold/watch/close decisions."
echo "Intraday trading clock runs every 5 minutes during UTC market coverage and keeps promotion/autopilot data fresh."
echo "Review ${REPO_DIR}/docs/08_alpaca_paper_trading.md before enabling paper submission."
