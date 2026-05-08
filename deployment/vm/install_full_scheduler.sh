#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/massa/stock-market-ml-platform}"
SYSTEMD_DIR="/etc/systemd/system"

sudo cp "$REPO_DIR/deployment/systemd/stockml-full-nightly.service" "$SYSTEMD_DIR/"
sudo cp "$REPO_DIR/deployment/systemd/stockml-full-nightly.timer" "$SYSTEMD_DIR/"
sudo systemctl daemon-reload
sudo systemctl enable --now stockml-full-nightly.timer
sudo systemctl list-timers 'stockml-*'
