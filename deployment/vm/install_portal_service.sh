#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/massa/stock-market-ml-platform}"
PYTHON_BIN="${PYTHON_BIN:-/opt/jupyter-env/bin/python3}"
SERVICE="stockml-portal.service"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8091/health}"

if [[ ! -d "$REPO_DIR" ]]; then
  echo "Repo directory not found: $REPO_DIR" >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python interpreter not found or not executable: $PYTHON_BIN" >&2
  exit 1
fi

cd "$REPO_DIR"

echo "Installing portal dependencies with $PYTHON_BIN"
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r "$REPO_DIR/requirements.txt"

echo "Compiling portal entrypoints"
PYTHONPATH="$REPO_DIR:$REPO_DIR/src" "$PYTHON_BIN" -m py_compile \
  "$REPO_DIR/portal/app.py" \
  "$REPO_DIR/scripts/run_portal.py"

echo "Installing systemd service"
sudo cp "$REPO_DIR/deployment/systemd/$SERVICE" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE"
sudo systemctl restart "$SERVICE"

echo "Waiting for portal health check: $HEALTH_URL"
for attempt in {1..20}; do
  if curl -fsS "$HEALTH_URL"; then
    echo
    sudo systemctl status "$SERVICE" --no-pager
    exit 0
  fi
  sleep 1
done

echo "Portal health check failed. Recent service logs:" >&2
sudo systemctl status "$SERVICE" --no-pager || true
sudo journalctl -u "$SERVICE" -n 80 --no-pager || true
exit 1
