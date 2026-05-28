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

DB_NAME="${STOCKML_DB_NAME:-stockml}"
DB_USER="${STOCKML_DB_USER:-stockml}"
DB_PASSWORD="${STOCKML_DB_PASSWORD:-stockml}"
DB_HOST="${STOCKML_DB_HOST:-localhost}"
DB_PORT="${STOCKML_DB_PORT:-5432}"
STOCKML_PROFILE="${STOCKML_PROFILE:-us_full}"
STOCKML_WRITE_DATABASE="${STOCKML_WRITE_DATABASE:-1}"
PORT="${PORT:-8091}"
ENV_DIR="/etc/stockml"
ENV_FILE="$ENV_DIR/stockml.env"
DATABASE_URL="${DATABASE_URL:-postgresql+psycopg2://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}}"

escape_env_value() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

if [[ ! -d "$REPO_DIR" ]]; then
  echo "Repo directory not found: $REPO_DIR" >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python interpreter not found or not executable: $PYTHON_BIN" >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
  sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';"
else
  sudo -u postgres psql -c "ALTER USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';"
fi

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
fi

cd "$REPO_DIR"
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r "$REPO_DIR/requirements.txt"

tmp_env="$(mktemp)"
{
  printf 'PYTHONPATH="%s"\n' "$(escape_env_value "$REPO_DIR:$REPO_DIR/src")"
  printf 'STOCKML_PROJECT_ROOT="%s"\n' "$(escape_env_value "$REPO_DIR")"
  printf 'STOCKML_DB_NAME="%s"\n' "$(escape_env_value "$DB_NAME")"
  printf 'STOCKML_DB_USER="%s"\n' "$(escape_env_value "$DB_USER")"
  printf 'STOCKML_DB_PASSWORD="%s"\n' "$(escape_env_value "$DB_PASSWORD")"
  printf 'STOCKML_DB_HOST="%s"\n' "$(escape_env_value "$DB_HOST")"
  printf 'STOCKML_DB_PORT="%s"\n' "$(escape_env_value "$DB_PORT")"
  printf 'STOCKML_PROFILE="%s"\n' "$(escape_env_value "$STOCKML_PROFILE")"
  printf 'STOCKML_WRITE_DATABASE="%s"\n' "$(escape_env_value "$STOCKML_WRITE_DATABASE")"
  printf 'PORT="%s"\n' "$(escape_env_value "$PORT")"
  printf 'DATABASE_URL="%s"\n' "$(escape_env_value "$DATABASE_URL")"
} > "$tmp_env"
sudo install -d -m 0750 "$ENV_DIR"
sudo install -m 0640 -o root -g root "$tmp_env" "$ENV_FILE"
rm -f "$tmp_env"

PYTHONPATH="$REPO_DIR/src" DATABASE_URL="$DATABASE_URL" "$PYTHON_BIN" "$REPO_DIR/scripts/init_database.py"
sudo systemctl status postgresql --no-pager
