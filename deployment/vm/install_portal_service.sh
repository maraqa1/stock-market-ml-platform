#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/massa/stock-market-ml-platform}"
SERVICE="stockml-portal.service"

sudo cp "$REPO_DIR/deployment/systemd/$SERVICE" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
sudo systemctl restart "$SERVICE"
sudo systemctl status "$SERVICE" --no-pager
curl -fsS http://127.0.0.1:8091/health

