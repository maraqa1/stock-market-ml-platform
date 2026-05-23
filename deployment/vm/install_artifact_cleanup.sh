#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/massa/stock-market-ml-platform}"
PYTHON_BIN="${PYTHON_BIN:-/opt/jupyter-env/bin/python3}"
SYSTEMD_DIR="/etc/systemd/system"

if [[ ! -d "$REPO_DIR" ]]; then
  echo "Repo directory not found: $REPO_DIR" >&2
  exit 1
fi

cd "$REPO_DIR"
PYTHONPATH="$REPO_DIR/src" "$PYTHON_BIN" -m py_compile "$REPO_DIR/scripts/cleanup_pipeline_artifacts.py"

sudo cp "$REPO_DIR/deployment/systemd/stockml-artifact-cleanup.service" "$SYSTEMD_DIR/"
sudo cp "$REPO_DIR/deployment/systemd/stockml-artifact-cleanup.timer" "$SYSTEMD_DIR/"
sudo systemctl daemon-reload
sudo systemctl enable --now stockml-artifact-cleanup.timer
sudo systemctl list-timers 'stockml-artifact-cleanup.timer' --no-pager

echo
echo "Installed daily StockML artifact cleanup."
echo "Dry-run manually with:"
echo "  PYTHONPATH=src $PYTHON_BIN scripts/cleanup_pipeline_artifacts.py"
