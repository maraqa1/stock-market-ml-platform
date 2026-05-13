#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/massa/stock-market-ml-platform"
SYSTEMD_DIR="/etc/systemd/system"

sudo cp "${REPO_DIR}/deployment/systemd/stockml-intraday-trading-clock.service" "${SYSTEMD_DIR}/"
sudo cp "${REPO_DIR}/deployment/systemd/stockml-intraday-trading-clock.timer" "${SYSTEMD_DIR}/"

sudo systemctl daemon-reload
sudo systemctl enable stockml-intraday-trading-clock.timer
sudo systemctl restart stockml-intraday-trading-clock.timer

sudo systemctl list-timers 'stockml-intraday-trading-clock.timer' --no-pager

echo
echo "Installed synchronized intraday trading clock."
echo "Runs every 5 minutes during UTC market coverage and executes refresh, promotion scoring, rotation recommendations, and Paper Autopilot tick in sequence."
