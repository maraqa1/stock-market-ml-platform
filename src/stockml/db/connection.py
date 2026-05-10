from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


_ENGINE_CACHE: dict[str, Engine] = {}


def _env_files() -> list[Path]:
    project_root = Path(__file__).resolve().parents[3]
    return [project_root / ".env", Path("/etc/stockml/stockml.env")]


def _strip_env_value(value: str) -> str:
    stripped = value.strip()
    if (stripped.startswith('"') and stripped.endswith('"')) or (stripped.startswith("'") and stripped.endswith("'")):
        return stripped[1:-1]
    return stripped


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = _strip_env_value(value)


def _hydrate_environment() -> None:
    for path in _env_files():
        _load_env_file(path)


def _database_url_from_parts() -> Optional[str]:
    password = os.environ.get("STOCKML_DB_PASSWORD", "").strip()
    if not password:
        return None
    user = os.environ.get("STOCKML_DB_USER", "stockml").strip() or "stockml"
    host = os.environ.get("STOCKML_DB_HOST", "localhost").strip() or "localhost"
    port = os.environ.get("STOCKML_DB_PORT", "5432").strip() or "5432"
    name = os.environ.get("STOCKML_DB_NAME", "stockml").strip() or "stockml"
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


def database_url(required: bool = True) -> Optional[str]:
    _hydrate_environment()
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        url = _database_url_from_parts() or ""
    if url:
        return url
    if required:
        raise RuntimeError(
            "DATABASE_URL is not set and STOCKML_DB_PASSWORD was not found in .env or /etc/stockml/stockml.env"
        )
    return None


def get_engine(url: Optional[str] = None, required: bool = True) -> Optional[Engine]:
    resolved = url or database_url(required=required)
    if not resolved:
        return None
    if resolved not in _ENGINE_CACHE:
        _ENGINE_CACHE[resolved] = create_engine(resolved, pool_pre_ping=True, future=True)
    return _ENGINE_CACHE[resolved]
