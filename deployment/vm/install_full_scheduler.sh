#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/massa/stock-market-ml-platform}"
PYTHON_BIN="${PYTHON_BIN:-/opt/jupyter-env/bin/python3}"
STOCKML_PROFILE="${STOCKML_PROFILE:-nasdaq_500}"
STOCKML_WRITE_DATABASE="${STOCKML_WRITE_DATABASE:-0}"
DATABASE_URL="${DATABASE_URL:-}"
SYSTEMD_DIR="/etc/systemd/system"

cd "$REPO_DIR"
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r "$REPO_DIR/requirements.txt"
PYTHONPATH="$REPO_DIR/src" "$PYTHON_BIN" -m py_compile "$REPO_DIR/scripts/run_profile_pipeline.py"

sudo cp "$REPO_DIR/deployment/systemd/stockml-full-nightly.service" "$SYSTEMD_DIR/"
sudo cp "$REPO_DIR/deployment/systemd/stockml-full-nightly.timer" "$SYSTEMD_DIR/"
sudo systemctl daemon-reload
sudo systemctl set-environment STOCKML_PROFILE="$STOCKML_PROFILE"
sudo systemctl set-environment STOCKML_WRITE_DATABASE="$STOCKML_WRITE_DATABASE"
if [[ -n "$DATABASE_URL" ]]; then
  sudo systemctl set-environment DATABASE_URL="$DATABASE_URL"
fi
sudo systemctl enable --now stockml-full-nightly.timer
sudo systemctl list-timers 'stockml-*'
