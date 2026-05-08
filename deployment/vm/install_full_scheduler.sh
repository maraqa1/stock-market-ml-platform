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

STOCKML_PROFILE="${STOCKML_PROFILE:-nasdaq_500}"
STOCKML_WRITE_DATABASE="${STOCKML_WRITE_DATABASE:-0}"
DATABASE_URL="${DATABASE_URL:-}"
ENV_DIR="/etc/stockml"
ENV_FILE="$ENV_DIR/stockml.env"
SYSTEMD_DIR="/etc/systemd/system"

escape_env_value() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

existing_env_value() {
  local key="$1"
  if sudo test -f "$ENV_FILE"; then
    sudo awk -F= -v key="$key" '$1 == key {value=substr($0, index($0, "=") + 1); gsub(/^"/, "", value); gsub(/"$/, "", value); print value}' "$ENV_FILE" | tail -n 1
  fi
}

cd "$REPO_DIR"
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r "$REPO_DIR/requirements.txt"
PYTHONPATH="$REPO_DIR/src" "$PYTHON_BIN" -m py_compile "$REPO_DIR/scripts/run_profile_pipeline.py"

if [[ -z "$DATABASE_URL" ]]; then
  DATABASE_URL="$(existing_env_value DATABASE_URL || true)"
fi

tmp_env="$(mktemp)"
{
  printf 'PYTHONPATH="%s"\n' "$(escape_env_value "$REPO_DIR/src")"
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
  printf 'STOCKML_PROFILE="%s"\n' "$(escape_env_value "$STOCKML_PROFILE")"
  printf 'STOCKML_WRITE_DATABASE="%s"\n' "$(escape_env_value "$STOCKML_WRITE_DATABASE")"
  printf 'PORT="%s"\n' "$(escape_env_value "${PORT:-8091}")"
  if [[ -n "$DATABASE_URL" ]]; then
    printf 'DATABASE_URL="%s"\n' "$(escape_env_value "$DATABASE_URL")"
  fi
} > "$tmp_env"
sudo install -d -m 0750 "$ENV_DIR"
sudo install -m 0640 -o root -g root "$tmp_env" "$ENV_FILE"
rm -f "$tmp_env"

sudo cp "$REPO_DIR/deployment/systemd/stockml-full-nightly.service" "$SYSTEMD_DIR/"
sudo cp "$REPO_DIR/deployment/systemd/stockml-full-nightly.timer" "$SYSTEMD_DIR/"
sudo systemctl daemon-reload
sudo systemctl enable --now stockml-full-nightly.timer
sudo systemctl list-timers 'stockml-*'
