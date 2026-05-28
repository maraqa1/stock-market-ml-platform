#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/massa/stock-market-ml-platform}"
PYTHON_BIN="${PYTHON_BIN:-/opt/jupyter-env/bin/python3}"

if [[ -f "$REPO_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_DIR/.env"
  set +a
fi

SERVICE="stockml-portal.service"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8091/health}"
ENV_DIR="/etc/stockml"
ENV_FILE="$ENV_DIR/stockml.env"

escape_env_value() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

existing_env_value() {
  local key="$1"
  if sudo test -f "$ENV_FILE"; then
    sudo awk -F= -v key="$key" '$1 == key {value=substr($0, index($0, "=") + 1); gsub(/^"/, "", value); gsub(/"$/, "", value); print value}' "$ENV_FILE" | tail -n 1
  fi
}

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

EXISTING_DATABASE_URL="$(existing_env_value DATABASE_URL || true)"
STOCKML_PROFILE="${STOCKML_PROFILE:-$(existing_env_value STOCKML_PROFILE || true)}"
STOCKML_WRITE_DATABASE="${STOCKML_WRITE_DATABASE:-$(existing_env_value STOCKML_WRITE_DATABASE || true)}"
PORT="${PORT:-8091}"

tmp_env="$(mktemp)"
{
  printf 'PYTHONPATH="%s"\n' "$(escape_env_value "$REPO_DIR:$REPO_DIR/src")"
  printf 'STOCKML_PROJECT_ROOT="%s"\n' "$(escape_env_value "$REPO_DIR")"
  if [[ -n "${STOCKML_DB_NAME:-}" ]]; then
    printf 'STOCKML_DB_NAME="%s"\n' "$(escape_env_value "$STOCKML_DB_NAME")"
  fi
  if [[ -n "${STOCKML_DB_USER:-}" ]]; then
    printf 'STOCKML_DB_USER="%s"\n' "$(escape_env_value "$STOCKML_DB_USER")"
  fi
  if [[ -n "${STOCKML_DB_PASSWORD:-}" ]]; then
    printf 'STOCKML_DB_PASSWORD="%s"\n' "$(escape_env_value "$STOCKML_DB_PASSWORD")"
  fi
  if [[ -n "${STOCKML_DB_HOST:-}" ]]; then
    printf 'STOCKML_DB_HOST="%s"\n' "$(escape_env_value "$STOCKML_DB_HOST")"
  fi
  if [[ -n "${STOCKML_DB_PORT:-}" ]]; then
    printf 'STOCKML_DB_PORT="%s"\n' "$(escape_env_value "$STOCKML_DB_PORT")"
  fi
  printf 'STOCKML_PROFILE="%s"\n' "$(escape_env_value "${STOCKML_PROFILE:-us_full}")"
  printf 'STOCKML_WRITE_DATABASE="%s"\n' "$(escape_env_value "${STOCKML_WRITE_DATABASE:-0}")"
  printf 'PORT="%s"\n' "$(escape_env_value "$PORT")"
  if [[ -n "${DATABASE_URL:-$EXISTING_DATABASE_URL}" ]]; then
    printf 'DATABASE_URL="%s"\n' "$(escape_env_value "${DATABASE_URL:-$EXISTING_DATABASE_URL}")"
  fi
} > "$tmp_env"
sudo install -d -m 0750 "$ENV_DIR"
sudo install -m 0640 -o root -g root "$tmp_env" "$ENV_FILE"
rm -f "$tmp_env"

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
